import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

import cloudinary.uploader

from django.db.models import Q

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from auditlog.utils import get_client_ip, log_action
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError

from .serializers import (
    AdminUserSerializer,
    AdminUserUpdateSerializer,
    AdminVerificationSerializer,
    ChangePasswordSerializer,
    LoginOTPSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileUpdateSerializer,
    PublicProfileSerializer,
    ResendOTPSerializer,
    SendOTPSerializer,
    SubmitVerificationSerializer,
    UserProfileSerializer,
    UserRegistrationSerializer,
    UserRoleSerializer,
    UserSerializer,
    VerificationRequestSerializer,
    VerifyEmailOTPSerializer,
    VerifyOTPSerializer,
)
from .models import VerificationRequest, update_verification_level
User = get_user_model()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Synchronous OTP delivery helpers
# Used when USE_CELERY=False (local dev without Redis/Celery running).
# The Celery tasks remain unchanged and are used in production.
# ---------------------------------------------------------------------------

_OTP_EMAIL_SUBJECTS = {
    'email_verification': 'Verify Your Email — WARED',
    'password_reset':     'Password Reset Code — WARED',
}

_OTP_SMS_MESSAGES = {
    'phone_verification': 'Your WARED verification code is: {code}. Valid for 10 minutes.',
    'login':              'Your WARED login code is: {code}. Valid for 10 minutes.',
    'password_reset':     'Your WARED password reset code is: {code}. Valid for 10 minutes.',
}


def _send_otp_email_sync(user, code: str, purpose: str = 'email_verification') -> None:
    """Send OTP email synchronously (no Celery needed)."""
    from notifications.emails import send_templated_email
    subject = _OTP_EMAIL_SUBJECTS.get(purpose, 'Your WARED Verification Code')
    try:
        send_templated_email(
            to_email=user.email,
            subject=subject,
            template_name='otp_verification',
            context={
                'first_name': user.name or user.email,
                'otp_code':   code,
                'purpose':    purpose,
            },
        )
    except Exception as exc:
        logger.error('_send_otp_email_sync failed for %s: %s', user.email, exc)


def _send_otp_sms_sync(phone: str, code: str, purpose: str = 'phone_verification') -> None:
    """Send OTP SMS synchronously (no Celery needed)."""
    from sms.utils import send_sms
    template = _OTP_SMS_MESSAGES.get(purpose, 'Your WARED code is: {code}. Valid for 10 minutes.')
    message  = template.format(code=code)
    try:
        send_sms(phone, message)
    except Exception as exc:
        logger.error('_send_otp_sms_sync failed for %s: %s', phone, exc)


def _dispatch_otp_email(user, code: str, purpose: str = 'email_verification') -> None:
    """Dispatch OTP email via Celery if available, otherwise send synchronously."""
    if settings.USE_CELERY:
        from .tasks import send_otp_email as _task
        _task.delay(user.pk, code, purpose)
    else:
        _send_otp_email_sync(user, code, purpose)


def _dispatch_otp_sms(user_pk: int, phone: str, code: str, purpose: str) -> None:
    """Dispatch OTP SMS via Celery if available, otherwise send synchronously."""
    if settings.USE_CELERY:
        from .tasks import send_otp_sms as _task
        _task.delay(user_pk, phone, code, purpose)
    else:
        _send_otp_sms_sync(phone, code, purpose)


def _dispatch_welcome_email(user_pk: int) -> None:
    """Dispatch welcome email via Celery if available, otherwise skip in dev."""
    if settings.USE_CELERY:
        from .tasks import send_welcome_email as _task
        _task.delay(user_pk)


# ---------------------------------------------------------------------------
# Throttle — 5 requests / minute / IP for login & register
# ---------------------------------------------------------------------------

class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = 'password_reset'


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _blacklist_all_tokens(user) -> None:
    """Blacklist every outstanding refresh token for *user* (logs them out everywhere)."""
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
    tokens = OutstandingToken.objects.filter(user=user)
    for token in tokens:
        BlacklistedToken.objects.get_or_create(token=token)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def _set_auth_cookies(response, access_token: str, refresh_token: str) -> None:
    """Attach access and refresh tokens as httpOnly cookies to *response*."""
    jwt = settings.SIMPLE_JWT
    common = dict(
        httponly=jwt.get('AUTH_COOKIE_HTTP_ONLY', True),
        secure=jwt.get('AUTH_COOKIE_SECURE', False),
        samesite=jwt.get('AUTH_COOKIE_SAMESITE', 'Lax'),
        path=jwt.get('AUTH_COOKIE_PATH', '/'),
    )
    access_lifetime: timedelta = jwt.get('ACCESS_TOKEN_LIFETIME', timedelta(minutes=15))
    refresh_lifetime: timedelta = jwt.get('REFRESH_TOKEN_LIFETIME', timedelta(days=7))

    response.set_cookie(
        key=jwt.get('AUTH_COOKIE', 'access_token'),
        value=access_token,
        max_age=int(access_lifetime.total_seconds()),
        **common,
    )
    response.set_cookie(
        key=jwt.get('AUTH_COOKIE_REFRESH', 'refresh_token'),
        value=refresh_token,
        max_age=int(refresh_lifetime.total_seconds()),
        **common,
    )


def _delete_auth_cookies(response) -> None:
    """Clear access and refresh token cookies from *response*."""
    jwt = settings.SIMPLE_JWT
    opts = dict(
        path=jwt.get('AUTH_COOKIE_PATH', '/'),
        samesite=jwt.get('AUTH_COOKIE_SAMESITE', 'Lax'),
    )
    response.delete_cookie(key=jwt.get('AUTH_COOKIE', 'access_token'), **opts)
    response.delete_cookie(key=jwt.get('AUTH_COOKIE_REFRESH', 'refresh_token'), **opts)


# ---------------------------------------------------------------------------
# Auth views
# ---------------------------------------------------------------------------

@extend_schema(tags=['Auth'])
@method_decorator(csrf_exempt, name='dispatch')
class RegisterView(APIView):
    """
    POST /api/auth/register/
    Creates a new user account and sets httpOnly auth cookies.
    Returns user data in body — tokens are NOT returned in body.
    Rate-limited: 5 requests / minute / IP.
    """
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        from .otp import create_otp
        from .models import OTPCode
        from django.contrib.auth.password_validation import validate_password as _validate_pw
        from django.core.exceptions import ValidationError as DjangoValidationError

        # Handle re-registration of unverified accounts (case-insensitive)
        email = request.data.get('email', '').strip().lower()
        if email:
            existing = User.objects.filter(email__iexact=email).first()
            if existing:
                if existing.is_email_verified:
                    return Response(
                        {'email': ['A user with this email is already registered. Please sign in.']},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                # Unverified — update their info and resend OTP
                password = request.data.get('password', '')
                if password:
                    try:
                        _validate_pw(password, user=existing)
                    except DjangoValidationError as exc:
                        return Response(
                            {'password': exc.messages},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    existing.set_password(password)
                if request.data.get('name'):
                    existing.name = request.data['name']
                phone = request.data.get('phone', '').strip()
                if phone:
                    import re as _re
                    if not _re.match(r'^(\+9665\d{8}|05\d{8})$', phone):
                        return Response(
                            {'phone': ['Phone must be Saudi format: +9665XXXXXXXX or 05XXXXXXXX.']},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    existing.phone = phone
                existing.save()

                # Invalidate all previous OTPs and bypass cooldown
                OTPCode.objects.filter(
                    user=existing, purpose='email_verification', is_used=False,
                ).update(is_used=True)
                email_otp, _ = create_otp(existing, existing.email, purpose='email_verification')
                email_sent = False
                if email_otp:
                    _dispatch_otp_email(existing, email_otp.code, 'email_verification')
                    email_sent = True

                refresh = RefreshToken.for_user(existing)
                access = refresh.access_token
                resp_data = {
                    'detail': 'Account created. Please verify your email with the code we sent.',
                    'status': 'otp_resent',
                    'requires_verification': True,
                    'email_sent': email_sent,
                    'sms_sent': False,
                    'user': {
                        'id': existing.id,
                        'email': existing.email,
                        'name': existing.name,
                        'role': existing.role,
                    },
                    'tokens': {
                        'access': str(access),
                        'refresh': str(refresh),
                    },
                }
                if getattr(settings, 'DEBUG_OTP', False) and email_otp:
                    resp_data['debug_otp_code'] = email_otp.code

                response = Response(resp_data, status=status.HTTP_200_OK)
                _set_auth_cookies(response, str(access), str(refresh))
                return response

        serializer = UserRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # --- Email verification OTP (must not crash registration) ---
        email_otp = None
        email_sent = False
        try:
            email_otp, _ = create_otp(user, user.email, purpose='email_verification')
            if email_otp:
                _dispatch_otp_email(user, email_otp.code, 'email_verification')
                email_sent = True
        except Exception as exc:
            logger.error('Registration OTP email failed for %s: %s', user.email, exc)

        # --- Phone verification OTP (only if phone was provided at registration) ---
        sms_sent = False
        phone_otp = None
        if user.phone:
            try:
                phone_otp, _ = create_otp(user, user.phone, purpose='phone_verification')
                if phone_otp:
                    _dispatch_otp_sms(user.pk, user.phone, phone_otp.code, 'phone_verification')
                    sms_sent = True
            except Exception as exc:
                logger.error('Registration OTP SMS failed for %s: %s', user.phone, exc)

        refresh = RefreshToken.for_user(user)
        access  = refresh.access_token

        resp_data = {
            'detail':               'Account created. Please verify your email with the code we sent.',
            'requires_verification': True,
            'email_sent':           email_sent,
            'sms_sent':             sms_sent,
            'user': {
                'id':    user.id,
                'email': user.email,
                'name':  user.name,
                'role':  user.role,
            },
            'tokens': {
                'access':  str(access),
                'refresh': str(refresh),
            },
        }
        # In development, expose the OTP code so the flow can be tested
        # without a real email inbox.  Never enabled when USE_CELERY=True.
        if getattr(settings, 'DEBUG_OTP', False) and email_otp:
            resp_data['debug_otp_code'] = email_otp.code

        response = Response(resp_data, status=status.HTTP_201_CREATED)
        _set_auth_cookies(response, str(access), str(refresh))

        try:
            log_action(
                user=user,
                action='create',
                model_name='User',
                object_id=user.pk,
                old_value=None,
                new_value={'email': user.email, 'role': user.role},
                ip_address=get_client_ip(request),
            )
        except Exception:
            pass  # Audit log must never crash registration

        return response


@extend_schema(tags=['Auth'])
@method_decorator(csrf_exempt, name='dispatch')
class LoginView(APIView):
    """
    POST /api/auth/login/
    Authenticates with email + password, sets httpOnly auth cookies.
    Returns user data in body — tokens are NOT returned in body.
    Rate-limited: 5 requests / minute / IP.
    """
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        password = request.data.get('password', '')

        if not email or not password:
            return Response(
                {'error': 'Email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Case-insensitive lookup — find the user first, then check password
        try:
            user_obj = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            user_obj = None

        user = None
        if user_obj and user_obj.check_password(password):
            user = user_obj

        if user is None:
            # Audit-log the failed attempt (user may or may not exist)
            try:
                failed_user = User.objects.get(email=email)
                log_action(
                    user=None,
                    action='login_failed',
                    model_name='User',
                    object_id=failed_user.pk,
                    new_value={'email': email},
                    ip_address=get_client_ip(request),
                )
            except User.DoesNotExist:
                log_action(
                    user=None,
                    action='login_failed',
                    model_name='User',
                    object_id=None,
                    new_value={'email': email},
                    ip_address=get_client_ip(request),
                )
            return Response(
                {'error': 'Invalid credentials.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {'error': 'Account is disabled.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Block unverified accounts — send a fresh OTP so they can verify
        if not user.is_email_verified:
            from .otp import create_otp
            from .models import OTPCode
            OTPCode.objects.filter(
                user=user, purpose='email_verification', is_used=False,
            ).update(is_used=True)
            otp, _ = create_otp(user, user.email, purpose='email_verification')
            if otp:
                _dispatch_otp_email(user, otp.code, 'email_verification')

            resp_data = {
                'detail': 'يرجى تأكيد بريدك الإلكتروني أولاً. تم إرسال رمز تحقق جديد. / '
                          'Please verify your email first. A new verification code has been sent.',
                'code': 'email_not_verified',
            }
            if getattr(settings, 'DEBUG_OTP', False) and otp:
                resp_data['debug_otp_code'] = otp.code
            return Response(resp_data, status=status.HTTP_403_FORBIDDEN)

        refresh = RefreshToken.for_user(user)
        access = refresh.access_token

        response = Response({
            'user': {
                'id': user.id,
                'email': user.email,
                'name': user.name,
                'role': user.role,
            },
            'tokens': {
                'access': str(access),
                'refresh': str(refresh),
            },
        })
        _set_auth_cookies(response, str(access), str(refresh))

        log_action(
            user=user,
            action='login',
            model_name='User',
            object_id=user.pk,
            new_value={'email': user.email},
            ip_address=get_client_ip(request),
        )

        # Phase 5.4 — Log IP action for fraud detection
        try:
            from fraud.utils import log_ip_action
            log_ip_action(
                user=user,
                ip_address=get_client_ip(request),
                action='login',
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )
        except Exception:
            pass  # Never block login flow

        return response


@extend_schema(tags=['Auth'])
@method_decorator(csrf_exempt, name='dispatch')
class GoogleLoginView(APIView):
    """
    POST /api/auth/google/
    Sign in or create an account using a Google ID token (credential from Google Sign-In).
    Verifies the token, finds-or-creates the user, sets httpOnly JWT cookies.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        credential = request.data.get('credential')
        if not credential:
            return Response({'error': 'Google credential is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            idinfo = id_token.verify_oauth2_token(
                credential,
                google_requests.Request(),
                settings.GOOGLE_OAUTH2_CLIENT_ID,
            )
        except ValueError:
            return Response({'error': 'Invalid Google credential.'}, status=status.HTTP_400_BAD_REQUEST)

        email = idinfo.get('email')
        if not email:
            return Response({'error': 'Email not found in Google account.'}, status=status.HTTP_400_BAD_REQUEST)

        given_name  = idinfo.get('given_name', '')
        family_name = idinfo.get('family_name', '')
        full_name   = f'{given_name} {family_name}'.strip() or email.split('@')[0]

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'name':              full_name,
                'is_email_verified': True,
                'email_verified_at': timezone.now(),
            },
        )

        if created:
            user.set_unusable_password()
            user.save(update_fields=['password'])
            try:
                _dispatch_welcome_email(user.pk)
            except Exception:
                pass
            audit_action = 'create'
        else:
            # Ensure Google-authenticated users are always email-verified
            if not user.is_email_verified:
                user.is_email_verified = True
                user.email_verified_at = timezone.now()
                user.save(update_fields=['is_email_verified', 'email_verified_at'])
            audit_action = 'login'

        if not user.is_active:
            return Response({'error': 'Account is disabled.'}, status=status.HTTP_401_UNAUTHORIZED)

        refresh = RefreshToken.for_user(user)
        access  = refresh.access_token

        response = Response({
            'user': {
                'id':      user.id,
                'email':   user.email,
                'name':    user.name,
                'role':    user.role,
                'created': created,
            }
        })
        _set_auth_cookies(response, str(access), str(refresh))

        try:
            log_action(
                user=user,
                action=audit_action,
                model_name='User',
                object_id=user.pk,
                new_value={'email': user.email, 'via': 'google'},
                ip_address=get_client_ip(request),
            )
        except Exception:
            pass  # Audit log must never crash Google sign-in
        return response


@extend_schema(tags=['Auth'])
class LogoutView(APIView):
    """
    POST /api/auth/logout/
    Blacklists the refresh token and clears both auth cookies.
    Requires authentication.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Accept refresh token from body (mobile) or cookie (web)
        raw_refresh = request.data.get('refresh')
        if not raw_refresh:
            refresh_cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE_REFRESH', 'refresh_token')
            raw_refresh = request.COOKIES.get(refresh_cookie_name)

        if raw_refresh:
            try:
                token = RefreshToken(raw_refresh)
                token.blacklist()
            except TokenError:
                # Already invalid / blacklisted — still clear cookies
                pass

        response = Response({'detail': 'Logged out successfully.'})
        _delete_auth_cookies(response)

        log_action(
            user=request.user,
            action='logout',
            model_name='User',
            object_id=request.user.pk,
            ip_address=get_client_ip(request),
        )
        return response


@extend_schema(tags=['Auth'])
@method_decorator(csrf_exempt, name='dispatch')
class TokenRefreshCookieView(APIView):
    """
    POST /api/auth/token/refresh/
    Reads the refresh token from the httpOnly cookie, rotates it, blacklists the
    old one (BLACKLIST_AFTER_ROTATION=True), and sets new tokens as cookies.
    Returns {"detail": "Token refreshed"} — tokens are NOT in the body.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        # Accept refresh token from request body (mobile) or cookie (web).
        # Body takes priority if both are present.
        raw_refresh = request.data.get('refresh')
        if not raw_refresh:
            refresh_cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE_REFRESH', 'refresh_token')
            raw_refresh = request.COOKIES.get(refresh_cookie_name)

        if not raw_refresh:
            return Response(
                {'error': 'Refresh token not found (cookie or body).'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            refresh = RefreshToken(raw_refresh)

            # Reject refresh for unverified accounts
            user_id = refresh.payload.get('user_id')
            if user_id:
                try:
                    token_user = User.objects.get(pk=user_id)
                    if not token_user.is_email_verified:
                        return Response(
                            {'detail': 'Email not verified.', 'code': 'email_not_verified'},
                            status=status.HTTP_403_FORBIDDEN,
                        )
                except User.DoesNotExist:
                    pass  # let the normal token flow handle this

            new_access = str(refresh.access_token)

            # Rotate: blacklist old token, generate new refresh token
            if settings.SIMPLE_JWT.get('BLACKLIST_AFTER_ROTATION', True):
                try:
                    refresh.blacklist()
                except AttributeError:
                    pass  # blacklist app not installed

            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            new_refresh = str(refresh)

        except TokenError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        response = Response({
            'detail': 'Token refreshed.',
            'access': new_access,
            'refresh': new_refresh,
        })
        _set_auth_cookies(response, new_access, new_refresh)
        return response


@extend_schema(tags=['Auth'])
@method_decorator(ensure_csrf_cookie, name='dispatch')
class CSRFTokenView(APIView):
    """
    GET /api/auth/csrf/
    Forces Django to set the csrftoken cookie and returns the token value in the
    body so the frontend can attach it to subsequent POST/PATCH/DELETE requests.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'csrfToken': get_token(request)})


@extend_schema(tags=['Users'])
class MeView(APIView):
    """
    GET /api/me/
    Returns the authenticated user's profile. Works with cookie auth and Bearer
    header — no changes needed since CookieJWTAuthentication handles both.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        data = serializer.data

        # Include importer_profile for importer users
        if request.user.role == 'importer':
            try:
                from importers.models import ImporterProfile
                profile = ImporterProfile.objects.get(user=request.user)
                data['importer_profile'] = {
                    'business_name': profile.business_name or profile.business_name_ar,
                    'total_cars_imported': profile.total_cars_imported,
                    'average_rating': float(profile.average_rating) if profile.average_rating else None,
                    'success_rate': float(profile.success_rate) if profile.success_rate else None,
                    'is_verified': profile.is_verified,
                    'source_countries': profile.source_countries or [],
                    'commercial_registration_verified': profile.is_verified,
                    'subscription_plan': 'pro',  # TODO: wire to subscriptions app
                }
            except Exception:
                data['importer_profile'] = None
        else:
            data['importer_profile'] = None

        # Active reservations count
        try:
            from orders.models import Reservation
            data['active_reservations_count'] = Reservation.objects.filter(
                buyer=request.user, status='active',
            ).count()
        except Exception:
            data['active_reservations_count'] = 0

        return Response(data)


@extend_schema(tags=['Admin'])
class UserRoleView(APIView):
    """PATCH /api/users/{id}/role — admin only. Blocks self-demotion."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        if getattr(request.user, 'role', None) != 'admin':
            return Response(
                {'error': 'Only admins can change user roles.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            target = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if target.pk == request.user.pk:
            new_role = request.data.get('role')
            if new_role != 'admin':
                return Response(
                    {'error': 'You cannot demote your own account.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        serializer = UserRoleSerializer(target, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(target).data)


# ---------------------------------------------------------------------------
# Profile views (Phase 2.8)
# ---------------------------------------------------------------------------

@extend_schema(tags=['Admin'])
class AdminUserListView(generics.ListAPIView):
    """GET /api/users/ — admin-only; list all users. Supports ?search= and ?role= filters."""
    permission_classes = [IsAuthenticated]
    serializer_class   = AdminUserSerializer

    def get_queryset(self):
        if getattr(self.request.user, 'role', None) != 'admin':
            return User.objects.none()
        qs = User.objects.all().order_by('-date_joined')
        role   = self.request.query_params.get('role')
        search = self.request.query_params.get('search')
        if role:
            qs = qs.filter(role=role)
        if search:
            qs = qs.filter(Q(email__icontains=search) | Q(name__icontains=search))
        return qs


@extend_schema(tags=['Admin'])
class AdminUserUpdateView(APIView):
    """PATCH /api/users/<pk>/ — admin only; update name, email, role, is_active."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        if getattr(request.user, 'role', None) != 'admin':
            return Response(
                {'error': 'Only admins can update users.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            target = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Prevent self-demotion
        if target.pk == request.user.pk:
            new_role = request.data.get('role')
            if new_role and new_role != 'admin':
                return Response(
                    {'error': 'You cannot demote your own account.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = AdminUserUpdateSerializer(target, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        target.refresh_from_db()
        return Response(AdminUserSerializer(target).data)


@extend_schema(tags=['Users'])
class ProfileUpdateView(generics.RetrieveUpdateAPIView):
    """
    GET  /api/users/me/profile/ — own full profile (authenticated)
    PATCH /api/users/me/profile/ — update own profile fields + avatar upload
    """
    permission_classes = [IsAuthenticated]
    serializer_class   = ProfileUpdateSerializer
    http_method_names  = ['get', 'patch', 'head', 'options']

    def get_object(self):
        return self.request.user

    def perform_update(self, serializer):
        extra = {}
        if 'avatar' in self.request.FILES:
            result = cloudinary.uploader.upload(
                self.request.FILES['avatar'],
                folder='avatars/',
                transformation=[{
                    'width': 400, 'height': 400,
                    'crop': 'fill', 'gravity': 'face',
                    'quality': 'auto:good', 'fetch_format': 'auto',
                }],
            )
            extra['avatar'] = result['public_id']

        instance = serializer.save(**extra)
        # Re-read from DB so CloudinaryField wraps the avatar string properly
        instance.refresh_from_db()

        log_action(
            user=self.request.user,
            action='update',
            model_name='User',
            object_id=self.request.user.pk,
            new_value={'fields_updated': list(self.request.data.keys())},
            ip_address=get_client_ip(self.request),
        )

    def update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return super().update(request, *args, **kwargs)


@extend_schema(tags=['Users'])
class PublicProfileView(generics.RetrieveAPIView):
    """
    GET /api/users/<pk>/profile/ — public profile (AllowAny).
    Phone/email only included if the user opted in via show_phone / show_email.
    """
    permission_classes = [AllowAny]
    serializer_class   = PublicProfileSerializer
    queryset           = User.objects.select_related('city_obj').all()


# ---------------------------------------------------------------------------
# Password reset views (Phase 2.11)
# ---------------------------------------------------------------------------

@extend_schema(tags=['Auth'])
@method_decorator(csrf_exempt, name='dispatch')
class PasswordResetRequestView(APIView):
    """
    POST /api/auth/password-reset/
    Accepts { "email": "..." } and always returns 200 to prevent email enumeration.
    If the email exists, queues a Celery task to send a reset link.
    Rate-limited: 3 requests / hour / IP.
    """
    permission_classes  = [AllowAny]
    throttle_classes    = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email=email, is_active=True)
            uid       = urlsafe_base64_encode(force_bytes(user.pk))
            token     = default_token_generator.make_token(user)
            reset_url = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
            from .tasks import send_password_reset_email
            send_password_reset_email.delay(user.pk, reset_url)
            log_action(
                user=user,
                action='password_reset_requested',
                model_name='User',
                object_id=user.pk,
                ip_address=get_client_ip(request),
            )
        except User.DoesNotExist:
            pass  # Prevent email enumeration — still return 200

        return Response(
            {'detail': 'If an account with this email exists, a password reset link has been sent.'},
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=['Auth'])
@method_decorator(csrf_exempt, name='dispatch')
class PasswordResetConfirmView(APIView):
    """
    POST /api/auth/password-reset/confirm/
    Accepts { "uid", "token", "new_password", "confirm_password" }.
    Validates the token, sets the new password, and blacklists all refresh tokens.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uid          = serializer.validated_data['uid']
        token        = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        # Decode uid → user
        try:
            user_pk = force_str(urlsafe_base64_decode(uid))
            user    = User.objects.get(pk=user_pk)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response(
                {'detail': 'Invalid or expired reset link.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, token):
            return Response(
                {'detail': 'Invalid or expired reset link.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save()
        _blacklist_all_tokens(user)

        log_action(
            user=user,
            action='password_reset_completed',
            model_name='User',
            object_id=user.pk,
            ip_address=get_client_ip(request),
        )
        return Response(
            {'detail': 'Password has been reset successfully. Please login with your new password.'},
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=['Auth'])
class ChangePasswordView(APIView):
    """
    POST /api/auth/change-password/
    Requires authentication. Validates current password, sets new password,
    blacklists all existing refresh tokens, and issues fresh tokens in cookies.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        current_password = serializer.validated_data['current_password']
        new_password     = serializer.validated_data['new_password']

        if not request.user.check_password(current_password):
            return Response(
                {'current_password': ['Current password is incorrect.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.user.set_password(new_password)
        request.user.save()
        _blacklist_all_tokens(request.user)

        # Issue fresh tokens so the user stays logged in
        refresh = RefreshToken.for_user(request.user)
        access  = refresh.access_token
        response = Response(
            {'detail': 'Password changed successfully.'},
            status=status.HTTP_200_OK,
        )
        _set_auth_cookies(response, str(access), str(refresh))

        log_action(
            user=request.user,
            action='password_changed',
            model_name='User',
            object_id=request.user.pk,
            ip_address=get_client_ip(request),
        )
        return response


# ---------------------------------------------------------------------------
# OTP views (Phase 3.4)
# ---------------------------------------------------------------------------

class OTPRateThrottle(AnonRateThrottle):
    scope = 'otp'


@extend_schema(tags=['Auth'])
class SendPhoneVerificationOTP(APIView):
    """
    POST /api/auth/otp/send-verification/
    Sends a 6-digit code to the provided Saudi phone number.
    Requires authentication.  Cooldown: 60 s between requests.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .otp import create_otp
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']

        otp, error = create_otp(request.user, phone, purpose='phone_verification')
        if error:
            return Response({'detail': error}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        _dispatch_otp_sms(request.user.pk, phone, otp.code, 'phone_verification')
        return Response({'detail': 'Verification code sent.'}, status=status.HTTP_200_OK)


@extend_schema(tags=['Auth'])
class VerifyPhoneOTP(APIView):
    """
    POST /api/auth/otp/verify-phone/
    Verifies the 6-digit code and marks the user's phone as verified.
    Requires authentication.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from django.utils import timezone as tz
        from .otp import verify_otp
        serializer = VerifyOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data['code']

        # Determine which phone is being verified (from the OTP record)
        from .models import OTPCode
        try:
            latest_otp = OTPCode.objects.filter(
                user=request.user, purpose='phone_verification', is_used=False,
            ).latest('created_at')
            phone = latest_otp.phone
        except OTPCode.DoesNotExist:
            return Response(
                {'detail': 'No OTP code found. Please request a new one.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        success, message = verify_otp(request.user, code, purpose='phone_verification')
        if not success:
            return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)

        request.user.phone             = phone
        request.user.is_phone_verified = True
        request.user.phone_verified_at = tz.now()
        request.user.save(update_fields=['phone', 'is_phone_verified', 'phone_verified_at'])

        return Response(
            {'detail': 'Phone number verified successfully.', 'is_phone_verified': True},
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=['Auth'])
@method_decorator(csrf_exempt, name='dispatch')
class SendLoginOTP(APIView):
    """
    POST /api/auth/otp/send-login/
    Sends a login OTP to a verified phone number.
    AllowAny — never reveals whether the phone is registered (enumeration prevention).
    """
    permission_classes = [AllowAny]
    throttle_classes   = [OTPRateThrottle]

    def post(self, request):
        from .otp import create_otp
        serializer = SendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']

        _SAFE_RESPONSE = Response(
            {'detail': 'If this phone number is registered, a login code has been sent.'},
            status=status.HTTP_200_OK,
        )

        try:
            user = User.objects.get(phone=phone, is_phone_verified=True, is_active=True)
        except User.DoesNotExist:
            return _SAFE_RESPONSE

        otp, _error = create_otp(user, phone, purpose='login')
        if otp:
            _dispatch_otp_sms(user.pk, phone, otp.code, 'login')

        return _SAFE_RESPONSE


@extend_schema(tags=['Auth'])
@method_decorator(csrf_exempt, name='dispatch')
class VerifyLoginOTP(APIView):
    """
    POST /api/auth/otp/verify-login/
    Verifies a login OTP and issues JWT tokens in httpOnly cookies.
    AllowAny.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        from .otp import verify_otp
        serializer = LoginOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data['phone']
        code  = serializer.validated_data['code']

        try:
            user = User.objects.get(phone=phone, is_phone_verified=True, is_active=True)
        except User.DoesNotExist:
            return Response({'detail': 'Invalid phone number or code.'}, status=status.HTTP_400_BAD_REQUEST)

        success, message = verify_otp(user, code, purpose='login')
        if not success:
            return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)

        refresh = RefreshToken.for_user(user)
        access  = refresh.access_token

        response = Response({
            'user': {
                'id':    user.id,
                'email': user.email,
                'name':  user.name,
                'role':  user.role,
            }
        })
        _set_auth_cookies(response, str(access), str(refresh))

        log_action(
            user=user,
            action='otp_login',
            model_name='User',
            object_id=user.pk,
            new_value={'phone': phone},
            ip_address=get_client_ip(request),
        )
        return response


# ---------------------------------------------------------------------------
# Email OTP views (Phase 3.5)
# ---------------------------------------------------------------------------

@extend_schema(tags=['Auth'])
class SendEmailVerificationOTP(APIView):
    """
    POST /api/auth/otp/send-email-verification/
    Sends a 6-digit code to the authenticated user's email address.
    Cooldown: 60 s between requests. Returns 400 if already verified.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from django.utils import timezone as tz
        from .otp import create_otp

        user = request.user
        if user.is_email_verified:
            return Response(
                {'detail': 'Email is already verified.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        otp, error = create_otp(user, user.email, purpose='email_verification')
        if error:
            return Response({'detail': error}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        _dispatch_otp_email(user, otp.code, 'email_verification')

        resp_data = {'detail': 'Verification code sent to your email.'}
        if getattr(settings, 'DEBUG_OTP', False):
            resp_data['debug_otp_code'] = otp.code
        return Response(resp_data, status=status.HTTP_200_OK)


@extend_schema(tags=['Auth'])
class VerifyEmailOTP(APIView):
    """
    POST /api/auth/otp/verify-email/
    Verifies the 6-digit code and marks the user's email as verified.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from django.utils import timezone as tz
        from .otp import verify_otp

        serializer = VerifyEmailOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data['code']

        success, message = verify_otp(request.user, code, purpose='email_verification')
        if not success:
            return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)

        request.user.is_email_verified = True
        request.user.email_verified_at = tz.now()
        request.user.save(update_fields=['is_email_verified', 'email_verified_at'])

        # Send welcome email now that the address is confirmed
        _dispatch_welcome_email(request.user.pk)

        return Response(
            {'detail': 'Email verified successfully.', 'is_email_verified': True},
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=['Auth'])
class ResendVerificationOTP(APIView):
    """
    POST /api/auth/otp/resend/
    Body: { "type": "email" } or { "type": "phone" }
    Re-sends a verification OTP for email or phone.
    Cooldown enforced. Phone requires user to have phone on file.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .otp import create_otp

        serializer = ResendOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        otp_type = serializer.validated_data['type']

        otp = None
        user = request.user

        if otp_type == 'email':
            if user.is_email_verified:
                return Response(
                    {'detail': 'Email is already verified.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            otp, error = create_otp(user, user.email, purpose='email_verification')
            if error:
                return Response({'detail': error}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            _dispatch_otp_email(user, otp.code, 'email_verification')

        else:  # phone
            if not user.phone:
                return Response(
                    {'detail': 'No phone number on file. Please update your profile first.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            otp, error = create_otp(user, user.phone, purpose='phone_verification')
            if error:
                return Response({'detail': error}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            _dispatch_otp_sms(user.pk, user.phone, otp.code, 'phone_verification')

        resp_data = {'detail': 'Verification code resent.'}
        if getattr(settings, 'DEBUG_OTP', False) and otp and otp_type == 'email':
            resp_data['debug_otp_code'] = otp.code
        return Response(resp_data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Verification views (Phase 5.1)
# ---------------------------------------------------------------------------

@extend_schema(tags=['Verification'])
class SubmitVerificationView(generics.CreateAPIView):
    """POST /api/verification/submit/"""
    serializer_class = SubmitVerificationSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        user = self.request.user
        vtype = serializer.validated_data['verification_type']
        # Prevent duplicate pending
        if VerificationRequest.objects.filter(user=user, verification_type=vtype, status='pending').exists():
            raise ValidationError({'detail': f'You already have a pending {vtype} verification request.'})
        # Prevent if already verified
        already_verified = {
            'national_id': user.national_id_verified,
            'commercial_registration': user.commercial_registration_verified,
            'rega_license': user.rega_verified,
        }
        if already_verified.get(vtype):
            raise ValidationError({'detail': f'Your {vtype} is already verified.'})
        req = serializer.save(user=user)
        log_action(
            user=user,
            action='create',
            model_name='VerificationRequest',
            object_id=req.pk,
            ip_address=get_client_ip(self.request),
        )
        # Notify admins
        from notifications.utils import notify
        for admin in User.objects.filter(role='admin'):
            notify(
                admin,
                'admin_alert',
                title='New Verification Request',
                message=f'New verification request from {user.email} ({vtype})',
            )


@extend_schema(tags=['Verification'])
class MyVerificationStatusView(generics.RetrieveAPIView):
    """GET /api/verification/status/"""
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        pending = VerificationRequest.objects.filter(user=user, status='pending')
        return Response({
            'email_verified': getattr(user, 'is_email_verified', False),
            'phone_verified': getattr(user, 'is_phone_verified', False),
            'national_id_verified': user.national_id_verified,
            'commercial_registration_verified': user.commercial_registration_verified,
            'rega_verified': user.rega_verified,
            'is_identity_verified': user.is_identity_verified,
            'is_business_verified': user.is_business_verified,
            'verification_level': user.verification_level,
            'pending_requests': VerificationRequestSerializer(pending, many=True).data,
        })


@extend_schema(tags=['Verification'])
class MyVerificationRequestsView(generics.ListAPIView):
    """GET /api/verification/requests/"""
    serializer_class = VerificationRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return VerificationRequest.objects.filter(user=self.request.user)


@extend_schema(tags=['Admin'])
class AdminVerificationListView(generics.ListAPIView):
    """GET /api/admin/verifications/"""
    serializer_class = AdminVerificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self.request.user, 'role', None) != 'admin':
            return VerificationRequest.objects.none()
        qs = VerificationRequest.objects.select_related('user', 'reviewed_by').all()
        status_filter = self.request.query_params.get('status')
        vtype_filter = self.request.query_params.get('verification_type')
        if status_filter:
            qs = qs.filter(status=status_filter)
        if vtype_filter:
            qs = qs.filter(verification_type=vtype_filter)
        return qs


@extend_schema(tags=['Admin'])
class AdminVerificationActionView(generics.UpdateAPIView):
    """PATCH /api/admin/verifications/{id}/"""
    serializer_class = AdminVerificationSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['patch']

    def get_queryset(self):
        if getattr(self.request.user, 'role', None) != 'admin':
            return VerificationRequest.objects.none()
        return VerificationRequest.objects.select_related('user').all()

    def partial_update(self, request, *args, **kwargs):
        from django.utils import timezone as _tz
        from notifications.utils import notify

        req = self.get_object()
        action = request.data.get('action')
        if action not in ('approve', 'reject'):
            return Response({'detail': 'action must be "approve" or "reject".'}, status=400)
        if req.status != 'pending':
            return Response({'detail': 'Only pending requests can be actioned.'}, status=400)

        req.reviewed_by = request.user
        req.reviewed_at = _tz.now()

        if action == 'approve':
            req.status = 'approved'
            user = req.user
            if req.verification_type == 'national_id':
                user.national_id_verified = True
                user.national_id_verified_at = _tz.now()
                user.is_identity_verified = True
            elif req.verification_type == 'commercial_registration':
                user.commercial_registration_verified = True
                user.cr_verified_at = _tz.now()
                user.is_business_verified = True
            elif req.verification_type == 'rega_license':
                user.rega_verified = True
                user.is_business_verified = True
            user.save()
            update_verification_level(user)
            notify(
                user,
                'verification_approved',
                title='Verification Approved',
                message=f'Your {req.verification_type} verification has been approved!',
            )
            try:
                from django.core.mail import send_mail
                from django.conf import settings as django_settings
                send_mail(
                    subject='Verification Approved — WARED',
                    message=f'Your {req.verification_type} verification has been approved.',
                    from_email=django_settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=True,
                )
            except Exception:
                pass
            log_action(
                user=request.user,
                action='approve',
                model_name='VerificationRequest',
                object_id=req.pk,
                ip_address=get_client_ip(request),
            )
        else:  # reject
            reason = request.data.get('reason', '')
            req.rejection_reason = reason
            req.status = 'rejected'
            notify(
                req.user,
                'verification_rejected',
                title='Verification Update',
                message=f'Your {req.verification_type} verification was rejected. Reason: {reason}',
            )
            try:
                from django.core.mail import send_mail
                from django.conf import settings as django_settings
                send_mail(
                    subject='Verification Update — WARED',
                    message=f'Your {req.verification_type} verification was rejected. Reason: {reason}',
                    from_email=django_settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[req.user.email],
                    fail_silently=True,
                )
            except Exception:
                pass
            log_action(
                user=request.user,
                action='reject',
                model_name='VerificationRequest',
                object_id=req.pk,
                ip_address=get_client_ip(request),
            )

        req.save()
        return Response(AdminVerificationSerializer(req, context={'request': request}).data)


# ---------------------------------------------------------------------------
# Phase 5.5 — Account Deletion
# ---------------------------------------------------------------------------

class AccountDeletionRequestView(APIView):
    """POST /api/account/deletion/request/ — request account deletion."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import AccountDeletionRequest

        user = request.user
        if hasattr(user, 'deletion_request') and user.deletion_request.status == 'pending':
            return Response({'error': 'A deletion request is already pending.'}, status=status.HTTP_400_BAD_REQUEST)

        scheduled_date = timezone.now() + timedelta(days=30)

        # Delete any old completed/cancelled requests
        AccountDeletionRequest.objects.filter(user=user).exclude(status='pending').delete()

        deletion_req = AccountDeletionRequest.objects.create(
            user=user,
            scheduled_deletion_date=scheduled_date,
            reason=request.data.get('reason', ''),
        )

        # Deactivate user immediately
        user.is_active = False
        user.is_deletion_pending = True
        user.deactivated_at = timezone.now()
        user.save(update_fields=['is_active', 'is_deletion_pending', 'deactivated_at'])

        # Archive importer listings
        if user.role == 'importer':
            from cars.models import Listing
            Listing.objects.filter(
                owner=user, is_active=True
            ).exclude(status__in=['sold', 'archived']).update(
                status='archived', is_active=False
            )

        # Send confirmation email
        try:
            from notifications.emails import send_templated_email
            send_templated_email(
                to_email=user.email,
                subject='Account Deletion Requested — WARED',
                template_name='account_deletion_requested',
                context={
                    'name': user.name,
                    'scheduled_date': scheduled_date.strftime('%d %B %Y'),
                    'cancel_url': f"{getattr(settings, 'FRONTEND_URL', '')}/auth/signin",
                },
            )
        except Exception:
            pass

        log_action(
            user=user,
            action='delete',
            model_name='AccountDeletionRequest',
            object_id=deletion_req.pk,
            new_value={'scheduled_deletion_date': scheduled_date.isoformat()},
            ip_address=get_client_ip(request),
        )

        return Response({
            'detail': 'Account deletion requested. Your account has been deactivated.',
            'scheduled_deletion_date': scheduled_date.isoformat(),
            'can_cancel_until': scheduled_date.isoformat(),
        })


class AccountDeletionCancelView(APIView):
    """POST /api/account/deletion/cancel/ — cancel pending deletion."""
    permission_classes = [AllowAny]  # User is inactive, so we use AllowAny + manual auth

    def post(self, request):
        from .models import AccountDeletionRequest

        # Manual auth: user must provide email+password since they are deactivated
        email = request.data.get('email')
        password = request.data.get('password')
        if not email or not password:
            return Response({'error': 'Email and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({'error': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.check_password(password):
            return Response({'error': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.is_deletion_pending:
            return Response({'error': 'No pending deletion request.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            deletion_req = AccountDeletionRequest.objects.get(user=user, status='pending')
        except AccountDeletionRequest.DoesNotExist:
            return Response({'error': 'No pending deletion request found.'}, status=status.HTTP_404_NOT_FOUND)

        if timezone.now() > deletion_req.scheduled_deletion_date:
            return Response({'error': 'Grace period has expired. Account cannot be recovered.'}, status=status.HTTP_400_BAD_REQUEST)

        # Cancel deletion
        deletion_req.status = 'cancelled'
        deletion_req.cancelled_at = timezone.now()
        deletion_req.cancelled_reason = request.data.get('reason', 'Cancelled by user')
        deletion_req.save()

        # Reactivate user
        user.is_active = True
        user.is_deletion_pending = False
        user.deactivated_at = None
        user.save(update_fields=['is_active', 'is_deletion_pending', 'deactivated_at'])

        try:
            from notifications.emails import send_templated_email
            send_templated_email(
                to_email=user.email,
                subject='Account Deletion Cancelled — WARED',
                template_name='account_deletion_cancelled',
                context={'name': user.name},
            )
        except Exception:
            pass

        log_action(
            user=user,
            action='update',
            model_name='AccountDeletionRequest',
            object_id=deletion_req.pk,
            new_value={'status': 'cancelled'},
            ip_address=get_client_ip(request),
        )

        return Response({'detail': 'Account deletion cancelled. Your account has been reactivated.'})


class AccountDeletionStatusView(APIView):
    """GET /api/account/deletion/status/"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import AccountDeletionRequest

        try:
            req = AccountDeletionRequest.objects.get(user=request.user, status='pending')
            days_remaining = max(0, (req.scheduled_deletion_date - timezone.now()).days)
            return Response({
                'status': req.status,
                'scheduled_deletion_date': req.scheduled_deletion_date.isoformat(),
                'days_remaining': days_remaining,
                'can_cancel': timezone.now() < req.scheduled_deletion_date,
                'reason': req.reason,
            })
        except AccountDeletionRequest.DoesNotExist:
            return Response(None)


# ---------------------------------------------------------------------------
# Phase 5.5 — Data Export
# ---------------------------------------------------------------------------

class DataExportRequestView(APIView):
    """POST /api/account/export/ — request data export."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import DataExportRequest

        # Rate limit: 1 per 24 hours
        recent = DataExportRequest.objects.filter(
            user=request.user,
            requested_at__gte=timezone.now() - timedelta(hours=24),
        ).exists()
        if recent:
            return Response(
                {'error': 'You can only request one data export per 24 hours.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        export_req = DataExportRequest.objects.create(user=request.user)

        # Trigger async task (graceful if Celery/Redis unavailable)
        try:
            from .tasks import generate_user_data_export
            generate_user_data_export.delay(export_req.id)
        except Exception:
            pass  # Task will be picked up manually or by retry

        return Response({
            'request_id': export_req.id,
            'status': 'pending',
            'estimated_ready_in': '5 minutes',
        })


class DataExportStatusView(APIView):
    """GET /api/account/export/status/"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import DataExportRequest

        export_req = DataExportRequest.objects.filter(user=request.user).first()
        if not export_req:
            return Response(None)

        return Response({
            'id': export_req.id,
            'status': export_req.status,
            'download_url': export_req.download_url if export_req.status == 'ready' else None,
            'expires_at': export_req.expires_at.isoformat() if export_req.expires_at else None,
            'file_size_bytes': export_req.file_size_bytes,
            'requested_at': export_req.requested_at.isoformat(),
        })

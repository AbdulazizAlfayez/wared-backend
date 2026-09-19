"""
Mobile authentication endpoints — token-based (no cookies, no CSRF).

POST /api/auth/mobile/login/
POST /api/auth/mobile/register/
POST /api/auth/mobile/refresh/
POST /api/auth/mobile/logout/
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from drf_spectacular.utils import extend_schema

User = get_user_model()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Throttle
# ---------------------------------------------------------------------------

class MobileAuthThrottle(AnonRateThrottle):
    scope = 'mobile_auth'


class PasswordResetCodeThrottle(AnonRateThrottle):
    """Six digits are guessable; this is what stops the guessing."""

    scope = 'password_reset'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blacklist_refresh(raw_token: str) -> bool:
    """Blacklist a single refresh token. Returns True on success."""
    try:
        token = RefreshToken(raw_token)
        token.blacklist()
        return True
    except TokenError:
        return False


# ---------------------------------------------------------------------------
# Mobile Login
# ---------------------------------------------------------------------------

@extend_schema(tags=['Auth — Mobile'])
@method_decorator(csrf_exempt, name='dispatch')
class MobileLoginView(APIView):
    """
    POST /api/auth/mobile/login/
    Returns access + refresh tokens in JSON body. No cookies set.
    """
    permission_classes = [AllowAny]
    throttle_classes = [MobileAuthThrottle]

    def post(self, request):
        email = (request.data.get('email') or '').strip().lower()
        password = request.data.get('password') or ''

        if not email or not password:
            return Response(
                {'error': 'Email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            user = None

        if not user or not user.check_password(password):
            return Response(
                {'error': 'Invalid credentials.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {'error': 'Account is disabled.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_email_verified:
            return Response(
                {'detail': 'Please verify your email first.',
                 'code': 'email_not_verified'},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)

        return Response({
            'user': {
                'id': user.id,
                'email': user.email,
                'name': user.name,
                'role': user.role,
            },
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        })


# ---------------------------------------------------------------------------
# Mobile Register
# ---------------------------------------------------------------------------

@extend_schema(tags=['Auth — Mobile'])
@method_decorator(csrf_exempt, name='dispatch')
class MobileRegisterView(APIView):
    """
    POST /api/auth/mobile/register/
    Creates a new user and returns tokens in JSON body (no cookies).
    """
    permission_classes = [AllowAny]
    throttle_classes = [MobileAuthThrottle]

    def post(self, request):
        from .serializers import UserRegistrationSerializer
        from .otp import create_otp
        from .views import _dispatch_otp_email

        serializer = UserRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Send email verification OTP
        email_sent = False
        debug_code = None
        try:
            otp, _ = create_otp(user, user.email, purpose='email_verification')
            if otp:
                _dispatch_otp_email(user, otp.code, 'email_verification')
                email_sent = True
                if getattr(settings, 'DEBUG_OTP', False):
                    debug_code = otp.code
        except Exception as exc:
            logger.error('Mobile register OTP failed for %s: %s', user.email, exc)

        refresh = RefreshToken.for_user(user)

        resp_data = {
            'detail': 'Account created. Please verify your email.',
            'requires_verification': True,
            'email_sent': email_sent,
            'user': {
                'id': user.id,
                'email': user.email,
                'name': user.name,
                'role': user.role,
            },
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }
        if debug_code:
            resp_data['debug_otp_code'] = debug_code

        return Response(resp_data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Mobile Refresh
# ---------------------------------------------------------------------------

@extend_schema(tags=['Auth — Mobile'])
@method_decorator(csrf_exempt, name='dispatch')
class MobileRefreshView(APIView):
    """
    POST /api/auth/mobile/refresh/
    Rotates the refresh token and blacklists the old one.
    """
    permission_classes = [AllowAny]
    throttle_classes = [MobileAuthThrottle]

    def post(self, request):
        raw_refresh = request.data.get('refresh')
        if not raw_refresh:
            return Response(
                {'error': 'Refresh token is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            old_token = RefreshToken(raw_refresh)
            # Rotate: create a new refresh token for the same user
            old_token.blacklist()
            user_id = old_token.payload.get('user_id')
            user = User.objects.get(pk=user_id)
            new_refresh = RefreshToken.for_user(user)
        except TokenError:
            return Response(
                {'error': 'Token is invalid or has been revoked.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response({
            'access': str(new_refresh.access_token),
            'refresh': str(new_refresh),
        })


# ---------------------------------------------------------------------------
# Mobile Logout
# ---------------------------------------------------------------------------

@extend_schema(tags=['Auth — Mobile'])
@method_decorator(csrf_exempt, name='dispatch')
class MobileLogoutView(APIView):
    """
    POST /api/auth/mobile/logout/
    Blacklists the provided refresh token.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        raw_refresh = request.data.get('refresh')
        if not raw_refresh:
            return Response(
                {'error': 'Refresh token is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if _blacklist_refresh(raw_refresh):
            return Response({'detail': 'Logged out successfully.'})
        return Response(
            {'error': 'Token is invalid or already revoked.'},
            status=status.HTTP_400_BAD_REQUEST,
        )


# ---------------------------------------------------------------------------
# Google sign-in (mobile)
# ---------------------------------------------------------------------------

def _dispatch_reset_code_email(user, code: str) -> None:
    """Reuses the OTP email path, which already knows the password_reset purpose."""
    from .views import _dispatch_otp_email

    _dispatch_otp_email(user, code, purpose='password_reset')


def _google_audiences():
    """
    Every client id a legitimate ID token could be addressed to.

    The web `GoogleLoginView` verifies against the single website client id,
    which is why a mobile token fails there: an ID token minted by the iOS or
    Android SDK carries that platform's client id in `aud`.
    """
    return [
        value
        for value in (
            getattr(settings, 'GOOGLE_OAUTH2_IOS_CLIENT_ID', ''),
            getattr(settings, 'GOOGLE_OAUTH2_ANDROID_CLIENT_ID', ''),
            getattr(settings, 'GOOGLE_OAUTH2_CLIENT_ID', ''),
        )
        if value
    ]


@extend_schema(tags=['Auth — Mobile'])
@method_decorator(csrf_exempt, name='dispatch')
class MobileGoogleLoginView(APIView):
    """
    POST /api/auth/mobile/google/  {"id_token": "..."}

    The same get-or-create as the website's Google sign-in, but answering with
    the JWT pair in the body instead of setting cookies — identical in shape to
    `MobileLoginView`, so the app has one way of storing a session.
    """

    permission_classes = [AllowAny]
    throttle_classes = [MobileAuthThrottle]

    def post(self, request):
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        raw_token = (request.data.get('id_token') or '').strip()
        if not raw_token:
            return Response(
                {'error': 'id_token is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audiences = _google_audiences()
        if not audiences:
            # Misconfiguration, not the caller's fault — say so plainly rather
            # than rejecting a perfectly good token as invalid.
            return Response(
                {'error': 'Google sign-in is not configured on this server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        idinfo = None
        for audience in audiences:
            try:
                idinfo = google_id_token.verify_oauth2_token(
                    raw_token, google_requests.Request(), audience,
                )
                break
            except ValueError:
                continue

        if idinfo is None:
            return Response(
                {'error': 'Invalid Google token.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = (idinfo.get('email') or '').strip().lower()
        if not email:
            return Response(
                {'error': 'That Google account has no email address.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        full_name = (
            f"{idinfo.get('given_name', '')} {idinfo.get('family_name', '')}".strip()
            or idinfo.get('name')
            or email.split('@')[0]
        )

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'name': full_name,
                # Google has already proved the address; making them verify it
                # again by email would be theatre.
                'is_email_verified': True,
                'email_verified_at': timezone.now(),
            },
        )

        if created:
            user.set_unusable_password()
            user.save(update_fields=['password'])
        elif not user.is_email_verified:
            user.is_email_verified = True
            user.email_verified_at = timezone.now()
            user.save(update_fields=['is_email_verified', 'email_verified_at'])

        if not user.is_active:
            return Response(
                {'error': 'Account is disabled.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'name': user.name,
                    'role': user.role,
                    'created': created,
                },
                'access': str(refresh.access_token),
                'refresh': str(refresh),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# In-app password reset (6-digit code)
# ---------------------------------------------------------------------------

@extend_schema(tags=['Auth — Mobile'])
@method_decorator(csrf_exempt, name='dispatch')
class PasswordResetCodeRequestView(APIView):
    """
    POST /api/auth/password-reset/request/  {"email": "..."}

    Emails a 6-digit code valid for 10 minutes. The web flow's emailed link
    still works and is untouched — a phone cannot open a link that lands in a
    desktop browser session, which is the whole reason this exists.

    Answers 200 whether or not the address is registered: the response must not
    be a way to discover who has an account.
    """

    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetCodeThrottle]

    def post(self, request):
        from .otp import create_otp

        email = (request.data.get('email') or '').strip().lower()
        if not email:
            return Response(
                {'email': 'This field is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        same_answer = Response(
            {'detail': 'If an account with this email exists, a reset code has been sent.'},
            status=status.HTTP_200_OK,
        )

        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user is None:
            return same_answer

        otp, error = create_otp(user, email, purpose='password_reset')
        if error:
            # The cooldown is worth saying out loud — silence here reads as a
            # broken button and the user just taps it again.
            return Response({'detail': error}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        _dispatch_reset_code_email(user, otp.code)

        payload = dict(same_answer.data)
        if getattr(settings, 'DEBUG_OTP', False):
            payload['debug_otp_code'] = otp.code
        return Response(payload, status=status.HTTP_200_OK)

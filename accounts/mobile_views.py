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

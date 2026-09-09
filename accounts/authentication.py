from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


class CookieJWTAuthentication(JWTAuthentication):
    """
    JWT authentication that reads the access token from an httpOnly cookie first,
    falling back to the standard Authorization: Bearer header so API clients
    (curl, Postman) continue to work without changes.

    Priority:
        1. Cookie named settings.SIMPLE_JWT['AUTH_COOKIE'] (default: 'access_token')
        2. Authorization: Bearer <token> header

    After authentication, banned/suspended users receive a 403 PermissionDenied.
    Expired suspensions are automatically lifted.
    """

    def authenticate(self, request):
        cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access_token')
        raw_token = request.COOKIES.get(cookie_name)

        if raw_token is not None:
            # Cookie path: validate and return (user, token) or None on failure
            try:
                validated_token = self.get_validated_token(raw_token)
            except InvalidToken:
                # Cookie present but invalid/expired — treat as unauthenticated
                return None
            user = self.get_user(validated_token)
            self._check_moderation(user)
            return user, validated_token

        # No cookie — fall back to Bearer header
        result = super().authenticate(request)
        if result is not None:
            user, token = result
            self._check_moderation(user)
            return user, token
        return result

    def _check_moderation(self, user):
        """Raise PermissionDenied for banned/suspended users; auto-lift expired suspensions."""
        if user is None or getattr(user, 'is_staff', False):
            return

        now = timezone.now()

        if getattr(user, 'is_banned', False):
            reason = getattr(user, 'ban_reason', '') or 'Violation of terms of service'
            raise PermissionDenied(
                f"Your account has been permanently banned: {reason}"
            )

        if getattr(user, 'is_suspended', False):
            suspended_until = getattr(user, 'suspended_until', None)
            if suspended_until and suspended_until > now:
                reason = getattr(user, 'suspension_reason', '') or 'Policy violation'
                raise PermissionDenied(
                    f"Your account is temporarily suspended until "
                    f"{suspended_until.strftime('%Y-%m-%d %H:%M UTC')}: {reason}"
                )
            else:
                # Suspension has expired — auto-lift
                user.is_suspended = False
                user.suspended_until = None
                user.save(update_fields=['is_suspended', 'suspended_until'])

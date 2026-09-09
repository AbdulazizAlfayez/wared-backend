from rest_framework.permissions import BasePermission


class IsEmailVerified(BasePermission):
    """
    Allows access only to users whose email address has been verified.
    Not applied globally — import and add to permission_classes on specific views
    when email verification is required.
    """
    message = 'Please verify your email address first.'

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and getattr(request.user, 'is_email_verified', False)
        )

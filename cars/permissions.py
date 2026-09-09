"""Custom permissions for cars app."""
from rest_framework import permissions


class IsOwnerOrAdmin(permissions.BasePermission):
    """
    Allow write only for owner or admin. List/retrieve use AllowAny; create needs auth; update/destroy check this.
    """

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if getattr(user, 'role', None) == 'admin':
            return True
        return getattr(obj, 'owner', None) == user or getattr(obj, 'seller', None) == user


class IsImporter(permissions.BasePermission):
    """Allow only users with role='importer'."""
    message = "Only verified importers can perform this action."

    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            getattr(request.user, 'role', '') == 'importer'
        )


class IsImporterOrAdmin(permissions.BasePermission):
    """Allow importers and admins."""
    message = "Only verified importers or admins can perform this action."

    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            getattr(request.user, 'role', '') in ('importer', 'admin')
        )


class IsAdminRole(permissions.BasePermission):
    """Allow only admin users."""

    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            (request.user.is_staff or getattr(request.user, 'role', '') == 'admin')
        )

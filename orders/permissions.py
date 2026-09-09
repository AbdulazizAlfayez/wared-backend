from rest_framework.permissions import BasePermission


class IsOrderParticipant(BasePermission):
    """Buyer, importer, or admin."""

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff or getattr(user, 'role', '') == 'admin':
            return True
        return obj.buyer_id == user.id or obj.importer_id == user.id


class IsOrderImporter(BasePermission):
    """Importer on the order or admin."""

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_staff or getattr(user, 'role', '') == 'admin':
            return True
        return obj.importer_id == user.id


class IsOrderBuyer(BasePermission):
    """Buyer on the order."""

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return obj.buyer_id == user.id

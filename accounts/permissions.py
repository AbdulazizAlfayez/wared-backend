from rest_framework.exceptions import PermissionDenied
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


def shares_a_deal(importer, buyer):
    """
    True when these two are already doing business.

    A reservation, an order or a conversation — any one of them means the
    importer has met this buyer through the platform and may see who they are
    dealing with. Imported inside the function: `accounts` is imported by
    almost everything, and reaching for `orders` or `messaging` at module load
    would close the circle.
    """
    from messaging.models import Conversation
    from orders.models import ImportOrder, Reservation

    if importer is None or buyer is None:
        return False
    return (
        Reservation.objects.filter(importer=importer, buyer=buyer).exists()
        or ImportOrder.objects.filter(importer=importer, buyer=buyer).exists()
        or Conversation.objects.filter(seller=importer, buyer=buyer).exists()
    )


def is_staff_or_admin(user):
    """The check every admin branch in this codebase makes, in one place."""
    return bool(
        user and user.is_authenticated
        and (user.is_staff or getattr(user, 'role', '') == 'admin')
    )


class CanSeeBuyerProfile(BasePermission):
    """
    A buyer's profile is for the importer they are buying from.

    Not public, and not "any importer": only one who shares a reservation, an
    order or a conversation with this buyer, plus staff. Everyone else is
    refused with 403 — including anonymous callers, who would otherwise get
    401 and learn that the endpoint exists and is merely gated. A 403 for
    every outsider tells them the same thing whether or not the id is real.
    """
    message = 'You can only view the profile of a buyer you are dealing with.'

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            raise PermissionDenied(self.message)
        return True

    def has_object_permission(self, request, view, obj):
        if is_staff_or_admin(request.user):
            return True
        if request.user.pk == obj.pk:
            return True
        if shares_a_deal(request.user, obj):
            return True
        raise PermissionDenied(self.message)

"""
Taking an approved car off the market, and putting it back.

Withdrawal is not moderation and it is not deletion. The listing stays
approved — nobody has to review it again — and it stays in the owner's own
lists; it simply stops being shown to buyers. `relist` is the exact reverse,
which is why neither touches `status`.

Three things make this awkward enough to deserve its own module:

1. `is_active=False` already means "soft-deleted" (`ListingViewSet.destroy`).
   A withdrawn car and a deleted one would be the same row, so `withdrawn_at`
   is what tells them apart — set on withdraw, cleared on relist, and never
   set by a delete. Without it the owner's "Withdrawn" filter would list the
   cars they deleted and offer to relist them.
2. A car somebody has paid to reserve, or that is inside an order, cannot be
   withdrawn: the buyer's claim outlives the importer's second thoughts. That
   is the same lock `public_market_q` applies, read from the same constants,
   so the two cannot drift.
3. The refusal has to say which of those it is, in both languages, because
   "you can't do that" is not an answer an importer can act on.
"""
from django.utils import timezone

from .visibility import ACTIVE_ORDER_STATUSES, LIVE_RESERVATION_STATUSES

#: 409 bodies, in the shape the rest of the API already uses for a refusal
#: that is about state rather than about the request (`SHIPMENT_NUMBER_REQUIRED`,
#: the payment gate): a machine-readable `code`, and the sentence in both
#: languages so the client never has to translate a server decision.
NOT_APPROVED = {
    'code': 'listing_not_approved',
    'detail': 'Only an approved listing can be withdrawn from the market.',
    'detail_ar': 'يمكن سحب الإعلانات المعتمدة فقط من السوق.',
}

ALREADY_WITHDRAWN = {
    'code': 'already_withdrawn',
    'detail': 'This listing is already withdrawn.',
    'detail_ar': 'هذا الإعلان مسحوب بالفعل.',
}

NOT_WITHDRAWN = {
    'code': 'not_withdrawn',
    'detail': 'This listing is not withdrawn, so there is nothing to relist.',
    'detail_ar': 'هذا الإعلان ليس مسحوباً، فلا شيء لإعادة نشره.',
}

HAS_RESERVATION = {
    'code': 'listing_reserved',
    'detail': 'A buyer holds a reservation on this car. Cancel it first.',
    'detail_ar': 'لدى أحد المشترين حجز على هذه السيارة. ألغِ الحجز أولاً.',
}

HAS_ORDER = {
    'code': 'listing_in_deal',
    'detail': 'This car is part of an active order and cannot be withdrawn.',
    'detail_ar': 'هذه السيارة ضمن طلب قائم ولا يمكن سحبها.',
}


def is_withdrawn(listing) -> bool:
    """
    Off the market by its owner's hand, rather than deleted or unapproved.

    Reads `withdrawn_at` rather than `is_active` alone, which is what keeps a
    soft-deleted listing out of the owner's withdrawn list.
    """
    return listing.withdrawn_at is not None and not listing.is_active


def blocking_deal(listing):
    """
    The 409 body for a car somebody else has a claim on, or None.

    Reservations first: a paid hold is the more surprising of the two to be
    stopped by, and an order usually has a reservation behind it, so naming
    the order when both exist would answer a question the importer did not
    ask.
    """
    if listing.reservations.filter(status__in=LIVE_RESERVATION_STATUSES).exists():
        return HAS_RESERVATION
    if listing.import_orders.filter(status__in=ACTIVE_ORDER_STATUSES).exists():
        return HAS_ORDER
    return None


def refusal_to_withdraw(listing):
    """The 409 body for `withdraw`, or None when it may go ahead."""
    if is_withdrawn(listing):
        return ALREADY_WITHDRAWN
    # An unapproved listing is not on the market to be taken off it. Said
    # before the deal check because it is the more basic fact about the row.
    if listing.status != 'approved' or not listing.is_active:
        return NOT_APPROVED
    return blocking_deal(listing)


def refusal_to_relist(listing):
    """The 409 body for `relist`, or None when it may go ahead."""
    if not is_withdrawn(listing):
        return NOT_WITHDRAWN
    # Relisting puts the car back in front of buyers, so the same lock
    # applies: a car that acquired a deal while it was away stays away.
    return blocking_deal(listing)


def withdraw(listing):
    """Take it off the market. Caller has already checked `refusal_to_withdraw`."""
    listing.is_active = False
    listing.withdrawn_at = timezone.now()
    listing.save(update_fields=['is_active', 'withdrawn_at'])
    return listing


def relist(listing):
    """Put it back, with no second review: `status` was never touched."""
    listing.is_active = True
    listing.withdrawn_at = None
    listing.save(update_fields=['is_active', 'withdrawn_at'])
    return listing

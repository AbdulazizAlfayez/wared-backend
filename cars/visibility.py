"""
Single source of truth for which listings are publicly visible ("on-market").

Business rule (WARED):
  - Only approved + active listings are ever public.
  - Reserved or sold cars are NEVER shown in public browse/search/homepage.
  - A car with ANY live or completed deal (an ImportOrder that wasn't
    cancelled/refunded) is in a private transaction between the buyer,
    the importer and WARED admin — hidden from everyone else.
  - Detail pages stay reachable for the parties involved
    (owner/importer, the buyer with an order on the car, and admins).
"""
from django.db.models import Q

# Order statuses that mean "this car is in an active or completed deal".
# Everything except the terminal cancelled/refunded states.
ACTIVE_ORDER_STATUSES = (
    'pending', 'deposit_requested', 'deposit_paid', 'confirmed',
    'sourcing', 'purchased', 'preparing_shipment', 'shipped',
    'arrived_port', 'in_customs', 'customs_cleared', 'inspection',
    'ready', 'delivered', 'completed',
)

# import_status values that are off-market even without an order row.
OFF_MARKET_IMPORT_STATUSES = ('reserved', 'sold')


def public_market_q():
    """Q object selecting listings the anonymous public may see."""
    return (
        Q(status='approved') & Q(is_active=True)
        & ~Q(import_status__in=OFF_MARKET_IMPORT_STATUSES)
        & ~Q(import_orders__status__in=ACTIVE_ORDER_STATUSES)
    )


def party_q(user):
    """
    Q object selecting listings a specific authenticated user may ALSO see
    beyond the public set: their own listings (importer) and cars they have
    an order on (buyer). Admins are handled by callers (they see everything).
    """
    return (
        (Q(owner=user) & Q(is_active=True))
        | Q(import_orders__buyer=user)
    )


def browse_queryset(base_qs, user):
    """
    Apply public browse visibility to *base_qs* for *user*.
    Public browse is strictly on-market for everyone — reserved/sold/deal
    cars never appear in browse, even for the parties (they use their
    dashboard/orders pages instead). Admins are NOT special-cased here on
    purpose: browse should show admins what the public sees.
    """
    return base_qs.filter(public_market_q()).distinct()


def detail_queryset(base_qs, user):
    """
    Apply detail-page visibility: public cars for everyone, plus the
    parties involved (owner, buyer-with-order) and admins.
    """
    if user is not None and getattr(user, 'is_authenticated', False):
        if getattr(user, 'role', None) == 'admin' or user.is_staff:
            return base_qs
        return base_qs.filter(public_market_q() | party_q(user)).distinct()
    return base_qs.filter(public_market_q()).distinct()

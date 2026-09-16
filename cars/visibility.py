"""
Single source of truth for which listings are publicly visible ("on-market").

Business rule (WARED):
  - Only approved + active listings are ever public.
  - From the moment a reservation is PAID (pending_review) until it is
    cancelled, rejected or expired, the car is invisible to everyone except
    the buyer who reserved it, the importer who owns it, and staff.
  - A car with ANY live or completed deal (an ImportOrder that wasn't
    cancelled/refunded) is likewise private to its parties.
  - When the reservation/order ends without a purchase the lock is released
    (orders.models) and the car satisfies this rule again automatically.

Every public listing queryset — list, detail, count, compare, search,
filter-options, by-country, nearby/map, featured/popular, showroom and
importer inventories, saved searches, favorites, recently viewed, social —
filters on `public_market_q(user)`. Do not add a second rule anywhere else.
"""
from django.db.models import Exists, OuterRef, Q

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

#: Reservation statuses in which the fee is paid and the car is locked.
#: ('active' is retained only for rows created before the
#: pending_payment → pending_review split.)
PAID_RESERVATION_STATUSES = ('pending_review', 'active')

#: Reservation statuses that still give the buyer a claim on the car.
LIVE_RESERVATION_STATUSES = ('pending_payment',) + PAID_RESERVATION_STATUSES


def is_staff_user(user):
    return bool(
        user is not None
        and getattr(user, 'is_authenticated', False)
        and (user.is_staff or getattr(user, 'role', None) == 'admin')
    )


def public_market_q(user=None):
    """
    Q object selecting the listings *user* may see in any public surface.

    - Anonymous / user=None: on-market cars only.
    - Authenticated: on-market cars, plus their own listings, plus cars they
      hold a live reservation or an active order on.
    - Staff: everything.

    Built from EXISTS subqueries rather than joins, so it never duplicates
    rows and callers don't need .distinct().
    """
    if is_staff_user(user):
        return Q()

    # Lazy import: orders.models imports cars.models.
    from orders.models import ImportOrder, Reservation

    paid_reservation = Reservation.objects.filter(
        car=OuterRef('pk'), status__in=PAID_RESERVATION_STATUSES,
    )
    active_order = ImportOrder.objects.filter(
        car=OuterRef('pk'), status__in=ACTIVE_ORDER_STATUSES,
    )
    q = (
        Q(status='approved') & Q(is_active=True)
        & Q(is_reserved=False)
        & ~Q(import_status__in=OFF_MARKET_IMPORT_STATUSES)
        & ~Exists(paid_reservation)
        & ~Exists(active_order)
    )

    if user is not None and getattr(user, 'is_authenticated', False):
        q |= (
            (Q(owner=user) & Q(is_active=True))
            | Exists(Reservation.objects.filter(
                car=OuterRef('pk'), buyer=user,
                status__in=LIVE_RESERVATION_STATUSES,
            ))
            | Exists(active_order.filter(buyer=user))
        )
    return q


def reservation_state(listing, user):
    """
    The requesting user's view of a listing's reservation lock:

      None               — not locked (or the user is not a party)
      'reserved_by_you'  — the user is the buyer holding the lock
      'reserved'         — importer (owner) or staff view of a locked car

    Only touches the database when the car is actually locked.
    """
    if not (listing.is_reserved or listing.current_reservation_id):
        return None
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    reservation = listing.current_reservation
    if reservation is not None and reservation.buyer_id == user.pk:
        return 'reserved_by_you'
    if listing.owner_id == user.pk or is_staff_user(user):
        return 'reserved'
    return None

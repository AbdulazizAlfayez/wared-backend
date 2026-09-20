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

Every public listing queryset filters on `public_market_q(user, browse=...)`.
Do not add a second rule anywhere else.

  browse=True  — feeds and grids: list (Home, Discover, search, count),
                 compare, autocomplete, popular, featured, nearby, map pins,
                 imported-cars list/arriving, showroom and importer
                 inventories, saved searches, the assistant's search.
                 A reserved car is shown to NO ONE here but staff — not even
                 its buyer or importer, who reach it through detail and their
                 reservations/orders pages.
  browse=False — opening a specific car: detail, images, likes/comments,
                 favorites, recently viewed. The buyer, the importer and staff
                 keep access; everyone else gets a 404.

filter-options and by-country are shared caches and use public_market_q()
with no user.
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


def moderation_q(user=None):
    """
    The moderation gate on its own: may this user be shown, or act on, this
    listing at all?

    `status='approved'` is what the admin queue controls. Staff see
    everything; an owner sees their own listing whatever state it is in, so
    they can preview and edit it before it is approved.

    Deliberately says nothing about reservations. Endpoints that create
    something against a car — a reservation, an order, a conversation, a lead,
    a report — have their own availability rules and their own error codes for
    a car someone else holds, and they must not lose those by borrowing the
    market filter. They use this; feeds and grids use `public_market_q`,
    which is this plus the lock.
    """
    if is_staff_user(user):
        return Q()

    q = Q(status='approved') & Q(is_active=True)
    if user is None or not getattr(user, 'is_authenticated', False):
        return q
    return q | (Q(owner=user) & Q(is_active=True))


def public_market_q(user=None, browse=False):
    """
    Q object selecting the listings *user* may see.

    - Anonymous / user=None: on-market cars only.
    - Authenticated, browse=False: on-market cars, plus their own listings,
      plus cars they hold a live reservation or an active order on.
    - Authenticated, browse=True: on-market cars, plus their own listings that
      are NOT locked by a reservation or deal (e.g. drafts awaiting approval).
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
    unlocked = (
        Q(is_reserved=False)
        & ~Q(import_status__in=OFF_MARKET_IMPORT_STATUSES)
        & ~Exists(paid_reservation)
        & ~Exists(active_order)
    )
    q = Q(status='approved') & Q(is_active=True) & unlocked

    if user is None or not getattr(user, 'is_authenticated', False):
        return q
    if browse:
        # Browse is the public market, and an unapproved car is not on it —
        # not even for the importer who submitted it. This used to add the
        # caller's own listings whatever their moderation status, which put a
        # pending car in the public grid, badged "Available" (the card reads
        # import_status) with a Reserve link. Owners see their own drafts and
        # pending cars on their own surfaces: `?mine=1` and
        # `/api/listings/my/`.
        return q
    return q | (
        (Q(owner=user) & Q(is_active=True))
        | Exists(Reservation.objects.filter(
            car=OuterRef('pk'), buyer=user,
            status__in=LIVE_RESERVATION_STATUSES,
        ))
        | Exists(active_order.filter(buyer=user))
    )


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
    if reservation is not None:
        if reservation.buyer_id == user.pk:
            return 'reserved_by_you'
    else:
        # Lock without a current_reservation pointer (rows written before
        # pay/ went through activate()): fall back to the reservation rows.
        from orders.models import Reservation
        if Reservation.objects.filter(
            car_id=listing.pk, buyer_id=user.pk,
            status__in=PAID_RESERVATION_STATUSES + ('converted_to_order',),
        ).exists():
            return 'reserved_by_you'
    if listing.owner_id == user.pk or is_staff_user(user):
        return 'reserved'
    return None

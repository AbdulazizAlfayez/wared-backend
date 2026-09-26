"""
GET /api/importers/me/desk/ — everything the importer Home screen shows.

One call, because the screen is one glance: how the business stands, what
needs answering, and the cars just listed. Assembled here rather than left to
the client to stitch from six endpoints, which is how two screens end up
disagreeing about how many reservations are waiting.

The `attention` list is ordered by what it costs the importer to ignore it:
a reservation about to lapse loses a sale that is already paid for, an
unacknowledged payment holds up a car the buyer has paid for in full, and a
listing sent back for changes is simply not selling. Silence on a message and
a stale order follow.
"""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

#: At most this many items in `attention`.
ATTENTION_LIMIT = 10

#: How long an order may sit in one status before the desk mentions it.
STALE_ORDER_DAYS = 7

#: How long a buyer's message may go unanswered before the desk mentions it.
UNANSWERED_HOURS = 24

#: Ordering weight per item type — lower is more urgent.
URGENCY = {
    'reservation_expiring': 0,
    'payment_to_acknowledge': 1,
    'changes_requested': 2,
    'unanswered_message': 3,
    'order_status_due': 4,
}

#: The i18n key each item type maps to for the one-line strip.
STRIP_KEYS = {
    'reservation_expiring': 'desk.strip.reservationExpiring',
    'payment_to_acknowledge': 'desk.strip.paymentToAcknowledge',
    'changes_requested': 'desk.strip.changesRequested',
    'unanswered_message': 'desk.strip.unansweredMessage',
    'order_status_due': 'desk.strip.orderStatusDue',
}


def _first_name(user):
    if user is None:
        return ''
    parts = [part for part in (user.name or '').split() if part]
    return parts[0] if parts else (user.email or '').split('@')[0]


def _counts(user):
    from cars.models import Listing
    from messaging.models import Conversation, Message
    from orders.models import ImportOrder, Reservation
    from payments.models import PaymentTransaction
    from cars.visibility import ACTIVE_ORDER_STATUSES

    mine = Listing.objects.filter(owner=user, is_active=True)

    unread = Message.objects.filter(
        conversation__in=Conversation.objects.filter(seller=user, is_active=True),
        is_read=False,
    ).exclude(sender=user).count()

    # Money the buyer has transferred and WARED has confirmed, on orders this
    # importer has not yet moved forward. It is what they are owed, not what
    # has been paid out — WARED settles separately (see MyPayoutsView).
    payouts_pending = PaymentTransaction.objects.filter(
        order__importer=user,
        payment_type='balance',
        status='succeeded',
    ).exclude(order__status__in=('completed', 'cancelled', 'refunded')).count()

    return {
        'live': mine.filter(status='approved').count(),
        'in_review': mine.filter(status__in=('pending', 'changes_requested')).count(),
        'drafts': mine.filter(status='draft').count(),
        'reservations_pending': Reservation.objects.filter(
            importer=user, status='pending_review',
        ).count(),
        'orders_active': ImportOrder.objects.filter(
            importer=user, status__in=ACTIVE_ORDER_STATUSES,
        ).exclude(status='completed').count(),
        'unread_messages': unread,
        'payouts_pending': payouts_pending,
    }


def _reservations_expiring(user, now):
    from orders.models import Reservation

    items = []
    for res in (
        Reservation.objects
        .filter(importer=user, status='pending_review')
        .select_related('car', 'buyer')
        .order_by('created_at')
    ):
        items.append({
            'type': 'reservation_expiring',
            'reservation_id': res.pk,
            'car_title': res.car.title if res.car else '',
            'buyer_first_name': _first_name(res.buyer),
            'hours_remaining': res.hours_remaining,
        })
    # Closest to lapsing first.
    items.sort(key=lambda item: item['hours_remaining'])
    return items


def _payments_to_acknowledge(user, now):
    """
    Orders whose balance WARED has confirmed while the order still sits at
    the status it had before the money arrived. The buyer has paid in full and
    is waiting to see the car move.
    """
    from orders.models import ImportOrder
    from payments.models import PaymentTransaction

    paid_order_ids = set(
        PaymentTransaction.objects
        .filter(order__importer=user, payment_type='balance', status='succeeded')
        .values_list('order_id', flat=True)
    )
    if not paid_order_ids:
        return []

    return [
        {
            'type': 'payment_to_acknowledge',
            'order_id': order.pk,
            'car_title': order.car.title if order.car else '',
        }
        for order in (
            ImportOrder.objects
            .filter(pk__in=paid_order_ids, status__in=('confirmed', 'sourcing'))
            .select_related('car')
            .order_by('updated_at')
        )
    ]


def _changes_requested(user, now):
    from cars.models import Listing

    return [
        {
            'type': 'changes_requested',
            'listing_id': listing.pk,
            'car_title': listing.title,
            'note': (listing.admin_notes or '').strip(),
        }
        for listing in (
            Listing.objects
            .filter(owner=user, is_active=True, status='changes_requested')
            .order_by('-status_changed_at', '-created_at')
        )
    ]


def _unanswered_messages(user, now):
    from messaging.models import Conversation

    cutoff = now - timedelta(hours=UNANSWERED_HOURS)
    items = []
    for conversation in (
        Conversation.objects
        .filter(seller=user, is_active=True, seller_archived=False)
        .select_related('listing', 'buyer')
        .prefetch_related('messages')
        .order_by('last_message_at')
    ):
        last = conversation.messages.order_by('-created_at').first()
        if last is None or last.is_system or last.sender_id == user.pk:
            continue
        if last.created_at > cutoff:
            continue
        items.append({
            'type': 'unanswered_message',
            'conversation_id': conversation.pk,
            'buyer_first_name': _first_name(conversation.buyer),
            'car_title': conversation.listing.title if conversation.listing else '',
            'hours_since': round((now - last.created_at).total_seconds() / 3600, 1),
        })
    items.sort(key=lambda item: -item['hours_since'])
    return items


def _orders_due(user, now):
    """
    Orders parked in one status long enough that the buyer is owed an update.
    `updated_at` is the best available proxy for "when this status was set" —
    every status change writes the row.
    """
    from orders.models import ImportOrder
    from cars.visibility import ACTIVE_ORDER_STATUSES

    cutoff = now - timedelta(days=STALE_ORDER_DAYS)
    items = []
    for order in (
        ImportOrder.objects
        .filter(importer=user, status__in=ACTIVE_ORDER_STATUSES, updated_at__lt=cutoff)
        .exclude(status='completed')
        .select_related('car')
        .order_by('updated_at')
    ):
        items.append({
            'type': 'order_status_due',
            'order_id': order.pk,
            'car_title': order.car.title if order.car else '',
            'current_status': order.status,
            'days_in_status': (now - order.updated_at).days,
        })
    items.sort(key=lambda item: -item['days_in_status'])
    return items


def build_attention(user, now=None):
    """The attention list, most urgent first, capped at ATTENTION_LIMIT."""
    now = now or timezone.now()
    items = (
        _reservations_expiring(user, now)
        + _payments_to_acknowledge(user, now)
        + _changes_requested(user, now)
        + _unanswered_messages(user, now)
        + _orders_due(user, now)
    )
    items.sort(key=lambda item: URGENCY[item['type']])
    return items[:ATTENTION_LIMIT]


def build_strip(attention):
    """
    The single line the Home screen leads with: the most urgent item, with an
    i18n key and the params it needs. None when there is nothing to say.
    """
    if not attention:
        return None
    item = attention[0]
    params = {key: value for key, value in item.items() if key != 'type'}
    return {'type': item['type'], 'text_key': STRIP_KEYS[item['type']], 'params': params}


def build_desk(user, request=None, now=None):
    """The whole payload. `request` is passed through for image URLs."""
    from cars.models import Listing
    from cars.serializers import ListingSerializer
    from cars.stats import owner_stats_bulk

    now = now or timezone.now()
    attention = build_attention(user, now)

    recent = list(
        Listing.objects
        # Withdrawn cars belong here: this is the importer's own shelf, and a
        # car that vanished from it the moment they took it off the market
        # would read as deleted. The counts above stay live-only — withdrawn is
        # precisely what "not live" means — and the card's own pill says
        # `Withdrawn`, because `ListingSerializer` gives the owner that word.
        .filter(owner=user)
        .filter(Q(is_active=True) | Q(withdrawn_at__isnull=False))
        .select_related('owner', 'city_obj', 'current_reservation')
        .prefetch_related('images')
        .order_by('-created_at')[:6]
    )
    serializer = ListingSerializer(recent, many=True, context={'request': request})
    # One round of counts for all six rather than five queries per card.
    serializer.child._owner_stats_bulk = owner_stats_bulk(recent)

    return {
        'counts': _counts(user),
        'attention': attention,
        'strip': build_strip(attention),
        'recent_listings': serializer.data,
    }

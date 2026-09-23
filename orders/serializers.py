"""
Serializers for the Import Order & Tracking system (Phase C) + Reservations (Phase M3).
"""
from rest_framework import serializers
from rest_framework.exceptions import APIException

from cars.models import Listing
from .models import ImportOrder, ImportTimeline, OrderDocument, Reservation


def _order_fully_paid(order) -> bool:
    """True once the full balance payment on this order has been confirmed.
    Contact details between buyer and importer are only revealed after this."""
    try:
        from payments.models import PaymentTransaction
        return PaymentTransaction.objects.filter(
            order=order, payment_type='balance', status='succeeded',
        ).exists()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Minimal car serializer for order list/detail
# ---------------------------------------------------------------------------

class CarMiniSerializer(serializers.ModelSerializer):
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = [
            'id', 'make', 'model', 'year', 'price',
            'final_price_sar', 'primary_image', 'import_status',
            'source_country', 'port_of_origin', 'port_of_entry',
            'vessel_name', 'estimated_arrival_date',
        ]

    def get_primary_image(self, obj):
        img = obj.images.filter(is_primary=True).first() or obj.images.first()
        if img and img.image:
            try:
                return img.image.url
            except Exception:
                return None
        return None


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

class ImportTimelineSerializer(serializers.ModelSerializer):
    # Map event_type → status so frontend can reference event.status
    status     = serializers.CharField(source='event_type', read_only=True)
    is_current = serializers.SerializerMethodField()
    location   = serializers.SerializerMethodField()

    class Meta:
        model = ImportTimeline
        fields = [
            'id', 'status', 'title', 'description',
            'location', 'created_at', 'is_current', 'is_public',
        ]

    def get_is_current(self, obj):
        """True for the most recent public event on this order."""
        last = (
            obj.order.timeline_events
            .filter(is_public=True)
            .order_by('-date', '-created_at')
            .values_list('pk', flat=True)
            .first()
        )
        return last is not None and last == obj.pk

    def get_location(self, obj):
        return None   # model has no location field; reserved for future use


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

class OrderDocumentSerializer(serializers.ModelSerializer):
    file_url             = serializers.SerializerMethodField()
    document_type_display = serializers.CharField(
        source='get_document_type_display', read_only=True
    )
    # Alias created_at → uploaded_at for frontend compatibility
    uploaded_at = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = OrderDocument
        fields = [
            'id', 'order', 'document_type', 'document_type_display',
            'title', 'file', 'file_url', 'uploaded_by',
            'is_buyer_visible', 'notes', 'created_at', 'uploaded_at',
        ]
        read_only_fields = [
            'id', 'order', 'uploaded_by', 'created_at',
            'file_url', 'document_type_display', 'uploaded_at',
        ]

    def get_file_url(self, obj):
        if obj.file:
            try:
                return obj.file.url
            except Exception:
                return None
        return None


# ---------------------------------------------------------------------------
# Order list serializer
# ---------------------------------------------------------------------------

class ImportOrderListSerializer(serializers.ModelSerializer):
    car            = CarMiniSerializer(read_only=True)
    importer       = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    # Alias deposit_amount → reservation_fee for frontend
    reservation_fee = serializers.DecimalField(
        source='deposit_amount', max_digits=12, decimal_places=2,
        allow_null=True, read_only=True
    )
    payment_status = serializers.SerializerMethodField()
    notes          = serializers.CharField(source='importer_notes', read_only=True)
    can_cancel     = serializers.SerializerMethodField()
    latest_event   = serializers.SerializerMethodField()
    balance_due_sar        = serializers.SerializerMethodField()
    can_exchange_contacts  = serializers.SerializerMethodField()
    allowed_next_statuses  = serializers.SerializerMethodField()

    class Meta:
        model = ImportOrder
        fields = [
            'id', 'order_number', 'car', 'importer',
            'status', 'status_display',
            'reservation_fee', 'deposit_amount', 'deposit_paid',
            'total_price', 'payment_status',
            'balance_due_sar', 'can_exchange_contacts', 'allowed_next_statuses',
            'estimated_delivery_date', 'actual_delivery_date',
            'shipment_number', 'carrier',
            'notes', 'can_cancel',
            'latest_event', 'created_at', 'updated_at',
        ]

    def get_allowed_next_statuses(self, obj):
        """
        Only transitions that will succeed right now: the payment-gated ones
        are left out until the balance is confirmed, so the importer app never
        offers a move that would come back 409.
        """
        return obj.unlocked_next_statuses()

    def get_status_display(self, obj):
        return obj.get_status_display()

    def get_importer(self, obj):
        u = obj.importer
        if not u:
            return None
        avatar_url = None
        if hasattr(u, 'avatar') and u.avatar:
            try:
                avatar_url = u.avatar.url
            except Exception:
                pass
        return {
            'id':         u.id,
            'name':       getattr(u, 'name', '') or u.email,
            'avatar_url': avatar_url,
            'phone':      getattr(u, 'phone', '') or '',
        }

    def get_payment_status(self, obj):
        """
        Balance-payment lifecycle: none → under_review (transfer submitted)
        → paid, or rejected if the transfer was refused.

        NOTE: these values replaced the previous
        awaiting_payment/deposit_paid/refunded set. 'none' covers both
        "nothing submitted yet" and "only the reservation fee is paid" — the
        SAR 99 fee is WARED's revenue, not a payment toward the car, so it
        never moves this field.
        """
        try:
            from payments.models import PaymentTransaction
            balance = PaymentTransaction.objects.filter(
                order=obj, payment_type='balance',
            ).order_by('-created_at').first()
            if balance:
                if balance.status == 'succeeded':
                    return 'paid'
                if balance.status == 'pending':
                    return 'under_review'
                if balance.status in ('failed', 'rejected'):
                    return 'rejected'
        except Exception:
            pass
        return 'none'

    def get_balance_due_sar(self, obj):
        """
        What the buyer still owes for the car.

        This is the FULL total_price. The SAR 99 reservation fee is NOT
        deducted: it is a non-refundable platform service fee and WARED's
        revenue, not a deposit credited toward the car.
        """
        return float(obj.total_price or 0)

    def get_can_exchange_contacts(self, obj):
        """
        Buyer and importer may swap phone/email only once the balance is
        confirmed paid. Until then all contact goes through WARED's chat.
        """
        return _order_fully_paid(obj)

    def get_can_cancel(self, obj):
        """Mirrors what the cancel endpoint will actually accept, so the UI
        never shows a button that 400s."""
        return 'cancelled' in obj.allowed_next_statuses()

    def get_latest_event(self, obj):
        event = (
            obj.timeline_events
            .filter(is_public=True)
            .order_by('-date', '-created_at')
            .first()
        )
        return event.title if event else None


# ---------------------------------------------------------------------------
# Order detail serializer
# ---------------------------------------------------------------------------

class ImportOrderDetailSerializer(ImportOrderListSerializer):
    timeline_events = serializers.SerializerMethodField()
    documents       = serializers.SerializerMethodField()
    buyer_info      = serializers.SerializerMethodField()
    importer_info   = serializers.SerializerMethodField()
    vessel_position = serializers.SerializerMethodField()

    balance_payment      = serializers.SerializerMethodField()
    payment_instructions = serializers.SerializerMethodField()
    importer_payout      = serializers.SerializerMethodField()

    class Meta(ImportOrderListSerializer.Meta):
        fields = ImportOrderListSerializer.Meta.fields + [
            'delivery_method', 'delivery_address', 'actual_delivery_date',
            'buyer_notes', 'importer_notes', 'cancellation_reason',
            'cancelled_at', 'remaining_balance',
            'timeline_events', 'documents', 'buyer_info', 'importer_info',
            'vessel_position',
            'balance_payment', 'payment_instructions', 'importer_payout',
        ]

    def get_balance_payment(self, obj):
        try:
            from payments.models import PaymentTransaction
            txn = PaymentTransaction.objects.filter(
                order=obj, payment_type='balance',
            ).order_by('-created_at').first()
            if not txn:
                return None
            return {
                'status':    txn.status,          # pending | succeeded | failed
                'amount':    float(txn.amount),
                'reference': txn.provider_transaction_id,
                'method':    txn.method,
                'created_at': txn.created_at.isoformat(),
            }
        except Exception:
            return None

    def get_payment_instructions(self, obj):
        """Bank details the buyer transfers to. Only for the buyer, and only
        while the balance is unpaid."""
        request = self.context.get('request')
        if not request or request.user.id != obj.buyer_id:
            return None
        from django.conf import settings as dj_settings
        return {
            'method':      'bank_transfer',
            'bank_name':   getattr(dj_settings, 'WARED_BANK_NAME', ''),
            'beneficiary': getattr(dj_settings, 'WARED_BANK_BENEFICIARY', ''),
            'iban':        getattr(dj_settings, 'WARED_BANK_IBAN', ''),
            'amount_sar':  float(obj.remaining_balance or obj.total_price or 0),
            'note':        f'Include your order number {obj.order_number} in the transfer note.',
        }

    def get_importer_payout(self, obj):
        """Importer-only: what they receive after WARED's commission."""
        request = self.context.get('request')
        if not request or request.user.id != obj.importer_id:
            return None
        from django.conf import settings as dj_settings
        pct = float(getattr(dj_settings, 'PLATFORM_COMMISSION_PCT', 1.0))
        total = float(obj.total_price or 0)
        return {
            'commission_pct': pct,
            'payout_pct':     100.0 - pct,
            'payout_sar':     round(total * (100.0 - pct) / 100.0, 2),
        }

    def _is_importer_or_admin(self):
        request = self.context.get('request')
        if not request or not request.user or not request.user.is_authenticated:
            return False
        user = request.user
        return user.is_staff or getattr(user, 'role', '') in ('admin', 'importer')

    def get_timeline_events(self, obj):
        qs = obj.timeline_events.all()
        if not self._is_importer_or_admin():
            qs = qs.filter(is_public=True)
        return ImportTimelineSerializer(qs, many=True).data

    def get_documents(self, obj):
        qs = obj.documents.all()
        if not self._is_importer_or_admin():
            qs = qs.filter(is_buyer_visible=True)
        return OrderDocumentSerializer(qs, many=True).data

    def get_buyer_info(self, obj):
        if not obj.buyer:
            return None
        info: dict = {
            'id':   obj.buyer.id,
            'profile_url_id': obj.buyer.id,
            'name': obj.buyer.name,
            # `city_name` is a serializer method elsewhere, never an attribute
            # on User, so this read always returned '' — the city_obj relation
            # is where the name actually lives.
            'city': obj.buyer.city_obj.name_en if obj.buyer.city_obj_id else '',
        }
        # Contact-exchange policy: the importer sees the buyer's phone/email
        # ONLY after the full balance payment is confirmed (or for staff).
        request = self.context.get('request')
        is_staff = bool(request and request.user.is_authenticated and request.user.is_staff)
        is_order_importer = bool(request and request.user.id == obj.importer_id)
        if is_staff or (is_order_importer and _order_fully_paid(obj)):
            info['email'] = obj.buyer.email
            info['phone'] = obj.buyer.phone or ''
        return info

    def get_importer_info(self, obj):
        if not obj.importer:
            return None
        info: dict = {
            'id':   obj.importer.id,
            'name': obj.importer.name,
        }
        try:
            profile = obj.importer.importer_profile
            info['business_name'] = profile.business_name
        except Exception:
            info['business_name'] = obj.importer.name
        # Contact-exchange policy: the buyer sees the importer's phone ONLY
        # after the full balance payment is confirmed (or for staff).
        request = self.context.get('request')
        is_staff = bool(request and request.user.is_authenticated and request.user.is_staff)
        is_order_buyer = bool(request and request.user.id == obj.buyer_id)
        if is_staff or (is_order_buyer and _order_fully_paid(obj)):
            info['phone'] = obj.importer.phone or ''
        return info

    def get_vessel_position(self, obj):
        """Mock vessel position for mobile app map display.
        Will be replaced with real MarineTraffic API data in a future phase."""
        car = obj.car
        if not car or obj.status not in ('shipping', 'shipped', 'at_port'):
            return None
        return {
            'latitude': 22.4,
            'longitude': 64.8,
            'last_updated': obj.updated_at.isoformat() if obj.updated_at else None,
            'vessel_name': car.vessel_name or 'Unknown',
            'speed_knots': 18.4,
        }


# ---------------------------------------------------------------------------
# Create order
# ---------------------------------------------------------------------------

class CreateOrderSerializer(serializers.Serializer):
    car_id           = serializers.IntegerField()
    buyer_notes      = serializers.CharField(required=False, allow_blank=True, default='')
    delivery_method  = serializers.ChoiceField(
        choices=[('pickup', 'Pickup'), ('delivery', 'Delivery')],
        required=False,
        default='pickup',
    )
    delivery_address = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_car_id(self, value):
        try:
            # Same moderation gate as the reservation path: an unapproved car
            # is not orderable by id.
            car = Listing.objects.moderated(
                self.context['request'].user
            ).get(pk=value, is_active=True)
        except Listing.DoesNotExist:
            raise serializers.ValidationError("Car not found or not available.")
        if car.import_status != 'available':
            raise serializers.ValidationError(
                f"This car is not available for ordering (status: {car.import_status})."
            )
        self._car = car
        return value

    def validate(self, data):
        request = self.context['request']
        car = getattr(self, '_car', None)
        if car is None:
            car = Listing.objects.moderated(request.user).get(pk=data['car_id'])
        if car.owner_id == request.user.id:
            raise serializers.ValidationError("You cannot order your own listing.")
        existing = ImportOrder.objects.filter(
            car=car,
            buyer=request.user,
        ).exclude(status__in=['cancelled', 'refunded', 'completed']).exists()
        if existing:
            raise serializers.ValidationError("You already have an active order for this car.")
        data['_car'] = car
        return data

    def create(self, validated_data):
        from django.utils import timezone
        car    = validated_data.pop('_car')
        buyer  = self.context['request'].user
        price  = car.final_price_sar or car.price or 0
        order  = ImportOrder.objects.create(
            car=car,
            buyer=buyer,
            importer=car.owner,
            total_price=price,
            remaining_balance=price,
            status='pending',
            buyer_notes=validated_data.get('buyer_notes', ''),
            delivery_method=validated_data.get('delivery_method', 'pickup'),
            delivery_address=validated_data.get('delivery_address', ''),
        )
        # Reserve the car (one writer for the lock — see orders.locks)
        from .locks import lock_listing_for_order
        lock_listing_for_order(order)
        # First timeline event
        ImportTimeline.objects.create(
            order=order,
            event_type='order_placed',
            title='Order placed by buyer',
            description=f"Order {order.order_number} created.",
            date=timezone.now(),
            created_by=buyer,
            is_public=True,
        )
        return order


# ---------------------------------------------------------------------------
# Update order status
# ---------------------------------------------------------------------------

def default_cancellation_reason(user) -> str:
    """
    Fallback reason so the website's and mobile app's plain "Cancel" buttons —
    which send no body — keep working. A supplied reason always wins.
    """
    role = getattr(user, 'role', '') or 'user'
    if getattr(user, 'is_staff', False) or role == 'admin':
        label = 'admin'
    elif role == 'importer':
        label = 'importer'
    else:
        label = 'buyer'
    return f'Cancelled by {label}'


#: 400 body for shipping a car without saying how it can be followed.
#:
#: Hand-built rather than raised as a DRF field error, because DRF wraps every
#: value in a ValidationError dict in a list — `code` would arrive as
#: `['shipment_number_required']`. The `shipment_number` key is kept in the
#: familiar field-error shape so a form can still map the message to its input,
#: and the flat `detail` / `detail_ar` match IMPORTER_CANNOT_RESERVE and the
#: 409 payment gate.
SHIPMENT_NUMBER_REQUIRED = {
    'code': 'shipment_number_required',
    'detail': 'Shipment number is required when marking as shipped.',
    'detail_ar': 'رقم الشحنة مطلوب عند تحديد الحالة كمشحونة.',
    'shipment_number': ['Shipment number is required when marking as shipped.'],
}


def shipment_number_missing(order, data):
    """True when this status change would ship a car nobody could follow."""
    if data.get('status') != 'shipped' or data.get('_forced'):
        return False
    supplied = (data.get('shipment_number') or '').strip()
    return not supplied and not (order.shipment_number or '').strip()


class UpdateOrderStatusSerializer(serializers.Serializer):
    status                  = serializers.ChoiceField(choices=ImportOrder.STATUS_CHOICES)
    notes                   = serializers.CharField(required=False, allow_blank=True, default='')
    cancellation_reason     = serializers.CharField(required=False, allow_blank=True, default='')
    estimated_delivery_date = serializers.DateField(required=False, allow_null=True)
    #: Required on the one edge that sets 'shipped'. Not `required=True` on the
    #: field, because every other status change would then have to send it.
    shipment_number         = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=60,
    )
    carrier                 = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=60,
    )
    # Staff-only escape hatch for correcting a stuck order.
    force                   = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        order = self.context['order']
        user = self.context.get('user')
        new_status = data['status']

        forced = bool(data.get('force')) and bool(self.context.get('can_force'))
        if not forced:
            try:
                order.validate_status_transition(new_status)
            except Exception as e:
                raise serializers.ValidationError(str(e))

        if new_status == 'cancelled' and not data.get('cancellation_reason'):
            data['cancellation_reason'] = default_cancellation_reason(user)

        data['_forced'] = forced
        return data


# ---------------------------------------------------------------------------
# Create timeline event (input)
# ---------------------------------------------------------------------------

class CreateTimelineEventSerializer(serializers.Serializer):
    event_type  = serializers.ChoiceField(choices=ImportTimeline.EVENT_TYPE_CHOICES)
    title       = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    date        = serializers.DateTimeField(required=False)
    is_public   = serializers.BooleanField(required=False, default=True)

    def validate(self, data):
        from django.utils import timezone
        if 'date' not in data:
            data['date'] = timezone.now()
        return data


# ---------------------------------------------------------------------------
# Upload document (input)
# ---------------------------------------------------------------------------

class UploadDocumentSerializer(serializers.Serializer):
    document_type    = serializers.ChoiceField(choices=OrderDocument.DOCUMENT_TYPE_CHOICES)
    title            = serializers.CharField(max_length=255)
    file             = serializers.FileField()
    is_buyer_visible = serializers.BooleanField(required=False, default=True)
    notes            = serializers.CharField(required=False, allow_blank=True, default='')


# ---------------------------------------------------------------------------
# Reservation serializers (Phase M3)
# ---------------------------------------------------------------------------

def person_brief(user):
    """
    The little the other party needs to render a row: an id, a first name and
    initials for the avatar disc. Full names, emails and phones are contact
    details, and those stay masked until the balance is paid.
    """
    if user is None:
        return None
    parts = [part for part in (user.name or '').split() if part]
    first = parts[0] if parts else (user.email or '').split('@')[0]
    initials = ''.join(part[0] for part in parts[:2]).upper() or first[:1].upper()
    return {
        'id': user.id,
        'first_name': first,
        'initials': initials,
        # What to open when this row is tapped. Same value as `id`; named for
        # what a client does with it, so nobody has to know which profile
        # endpoint takes a user id and which takes a profile id — the importer
        # profile at /api/importers/{pk}/ takes the PROFILE id, and guessing
        # wrong is a 404.
        'profile_url_id': user.id,
    }


class ReservationCarSerializer(serializers.ModelSerializer):
    primary_image = serializers.SerializerMethodField()
    # Same URL as `primary_image`, under the name the mobile client uses for
    # every other car thumbnail. Both are kept: renaming would break the web.
    primary_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = [
            'id', 'title', 'make', 'model', 'year', 'price',
            'final_price_sar', 'primary_image', 'primary_image_url',
            'import_status', 'source_country', 'is_reserved',
        ]

    def get_primary_image_url(self, obj):
        return self.get_primary_image(obj)

    def get_primary_image(self, obj):
        img = obj.images.filter(is_primary=True).first() or obj.images.first()
        if img and img.image:
            try:
                return img.image.url
            except Exception:
                return None
        return None


#: Statuses in which a reservation is still live and can be acted on.
OPEN_RESERVATION_STATUSES = ('pending_payment', 'pending_review')


class ReservationListSerializer(serializers.ModelSerializer):
    car                = ReservationCarSerializer(read_only=True)
    status_display     = serializers.CharField(source='get_status_display', read_only=True)
    importer_name      = serializers.SerializerMethodField()
    buyer              = serializers.SerializerMethodField()
    expires_at         = serializers.DateTimeField(read_only=True)
    hours_remaining    = serializers.SerializerMethodField()
    # Same number under the name /reservations/pending-for-me/ has always
    # used, so one client model reads both endpoints.
    time_remaining_hours = serializers.SerializerMethodField()
    can_cancel         = serializers.SerializerMethodField()
    can_pay            = serializers.SerializerMethodField()
    # The SAR 99 fee is a non-refundable platform service fee and WARED's
    # revenue — it is never credited toward the car price and never refunded.
    fee_refundable     = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            'id', 'reservation_number', 'car', 'status', 'status_display',
            'platform_fee_sar', 'fee_refundable',
            'payment_method', 'payment_status',
            'paid_at', 'cancelled_at', 'importer_id', 'importer_name',
            'buyer', 'buyer_notes',
            'expires_at', 'hours_remaining', 'time_remaining_hours',
            'can_cancel', 'can_pay',
            'created_at', 'updated_at',
        ]

    def _request_user(self):
        request = self.context.get('request')
        if not request or not getattr(request, 'user', None):
            return None
        return request.user if request.user.is_authenticated else None

    def get_fee_refundable(self, obj):
        return False

    def get_hours_remaining(self, obj):
        """0 once the reservation is no longer awaiting a decision."""
        if obj.status not in OPEN_RESERVATION_STATUSES:
            return 0.0
        return obj.hours_remaining

    def get_time_remaining_hours(self, obj):
        return self.get_hours_remaining(obj)

    def get_buyer(self, obj):
        return person_brief(obj.buyer)

    def get_can_cancel(self, obj):
        user = self._request_user()
        if user is None or obj.status not in OPEN_RESERVATION_STATUSES:
            return False
        is_party = obj.buyer_id == user.id or obj.importer_id == user.id
        is_admin = user.is_staff or getattr(user, 'role', '') == 'admin'
        return bool(is_party or is_admin)

    def get_can_pay(self, obj):
        """Only the buyer, only while payment is still outstanding."""
        user = self._request_user()
        if user is None or obj.buyer_id != user.id:
            return False
        return obj.status == 'pending_payment' and obj.payment_status != 'succeeded'

    def get_importer_name(self, obj):
        if obj.importer:
            try:
                profile = obj.importer.importer_profile
                return profile.business_name or profile.business_name_ar or obj.importer.name
            except Exception:
                return obj.importer.name
        return None


class ReservationDetailSerializer(ReservationListSerializer):
    importer_contact = serializers.SerializerMethodField()
    buyer_info       = serializers.SerializerMethodField()
    # Id of the ImportOrder this reservation became on accept, else null.
    converted_order  = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta(ReservationListSerializer.Meta):
        fields = ReservationListSerializer.Meta.fields + [
            'payment_reference', 'cancellation_reason',
            'importer_contact', 'buyer_info', 'converted_order',
        ]

    def get_importer_contact(self, obj):
        # Contact-exchange policy: NO phone/whatsapp at reservation stage —
        # full payment hasn't happened yet. Communication goes through
        # WARED's in-app chat until the balance is paid on the order.
        if not obj.importer:
            return None
        return {
            'name':     obj.importer.name,
            'phone':    None,
            'whatsapp': None,
        }

    def get_buyer_info(self, obj):
        # Same policy: no buyer email/phone before full payment.
        return {
            'id':    obj.buyer.id,
            'profile_url_id': obj.buyer.id,
            'name':  obj.buyer.name,
        }


class CarCurrentlyReserved(APIException):
    """409 — another buyer's reservation holds this car."""
    status_code = 409
    default_detail = 'This car is currently reserved.'
    default_code = 'car_reserved'


class CreateReservationSerializer(serializers.Serializer):
    car_id         = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=Reservation.PAYMENT_METHOD_CHOICES, required=False, default='mada')
    buyer_notes    = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_car_id(self, value):
        from django.utils.translation import gettext as _
        try:
            # `.moderated()` is the moderation gate: a car the admin queue has
            # not approved cannot be reserved, even by id, even though the
            # lock checks below would happily let it through. It deliberately
            # does NOT apply the reservation lock, so a car another buyer
            # holds still reaches the 409 below rather than becoming
            # "not found".
            car = Listing.objects.moderated(
                self.context['request'].user
            ).get(pk=value, is_active=True)
        except Listing.DoesNotExist:
            raise serializers.ValidationError(_('Car not found or not available.'))
        self._car = car
        return value

    def validate(self, data):
        from django.utils.translation import gettext as _
        request = self.context['request']
        car = getattr(self, '_car', None)
        if car and car.owner_id == request.user.id:
            raise serializers.ValidationError(_('You cannot reserve your own listing.'))

        # Car-wide, not per-buyer. A reservation sitting in pending_payment has
        # not set car.is_reserved yet, so without this two different buyers
        # could each open one on the same car and both pay the SAR 99 fee.
        blocking = Reservation.objects.filter(
            car_id=data['car_id'],
            # 'active' is retained only for rows created before the
            # pending_payment → pending_review split.
            status__in=['pending_payment', 'pending_review', 'active'],
        )
        if blocking.filter(buyer=request.user).exists():
            raise serializers.ValidationError(
                _('You already have an active reservation for this car.')
            )
        # Someone else holds the car. The app should never get here — a
        # reserved car is hidden from other buyers — so this is a safety net,
        # and a 409 rather than a validation error.
        if car.is_reserved or blocking.exists():
            raise CarCurrentlyReserved()
        if car.import_status not in ('available', 'ready_for_delivery'):
            raise serializers.ValidationError({'car_id': [
                _('This car is not available for reservation (status: %(status)s).') % {'status': car.import_status}
            ]})
        data['_car'] = car
        return data

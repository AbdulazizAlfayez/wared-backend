"""
Serializers for the Import Order & Tracking system (Phase C) + Reservations (Phase M3).
"""
from rest_framework import serializers

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

    class Meta:
        model = ImportOrder
        fields = [
            'id', 'order_number', 'car', 'importer',
            'status', 'status_display',
            'reservation_fee', 'deposit_amount', 'deposit_paid',
            'total_price', 'payment_status',
            'estimated_delivery_date', 'actual_delivery_date',
            'notes', 'can_cancel',
            'latest_event', 'created_at', 'updated_at',
        ]

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
        Balance-payment lifecycle shown to both parties:
        awaiting_payment → under_review (transfer submitted) → paid.
        """
        if obj.status == 'refunded':
            return 'refunded'
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
        except Exception:
            pass
        if obj.deposit_paid:
            return 'deposit_paid'
        return 'awaiting_payment'

    def get_can_cancel(self, obj):
        non_cancellable = obj.TERMINAL_STATUSES | {'delivered', 'completed'}
        return obj.status not in non_cancellable

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
            'name': obj.buyer.name,
            'city': getattr(obj.buyer, 'city_name', None) or '',
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
            car = Listing.objects.get(pk=value, is_active=True)
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
            car = Listing.objects.get(pk=data['car_id'])
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
        # Reserve the car
        car.import_status = 'reserved'
        car.save(update_fields=['import_status'])
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

class UpdateOrderStatusSerializer(serializers.Serializer):
    status                  = serializers.ChoiceField(choices=ImportOrder.STATUS_CHOICES)
    notes                   = serializers.CharField(required=False, allow_blank=True, default='')
    cancellation_reason     = serializers.CharField(required=False, allow_blank=True, default='')
    estimated_delivery_date = serializers.DateField(required=False, allow_null=True)

    def validate(self, data):
        order      = self.context['order']
        new_status = data['status']
        try:
            order.validate_status_transition(new_status)
        except Exception as e:
            raise serializers.ValidationError(str(e))
        if new_status == 'cancelled' and not data.get('cancellation_reason'):
            raise serializers.ValidationError(
                {"cancellation_reason": "Cancellation reason is required."}
            )
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

class ReservationCarSerializer(serializers.ModelSerializer):
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = [
            'id', 'title', 'make', 'model', 'year', 'price',
            'final_price_sar', 'primary_image', 'import_status',
            'source_country', 'is_reserved',
        ]

    def get_primary_image(self, obj):
        img = obj.images.filter(is_primary=True).first() or obj.images.first()
        if img and img.image:
            try:
                return img.image.url
            except Exception:
                return None
        return None


class ReservationListSerializer(serializers.ModelSerializer):
    car                = ReservationCarSerializer(read_only=True)
    status_display     = serializers.CharField(source='get_status_display', read_only=True)
    importer_name      = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            'id', 'reservation_number', 'car', 'status', 'status_display',
            'platform_fee_sar', 'payment_method', 'payment_status',
            'paid_at', 'cancelled_at', 'importer_name',
            'created_at', 'updated_at',
        ]

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

    class Meta(ReservationListSerializer.Meta):
        fields = ReservationListSerializer.Meta.fields + [
            'payment_reference', 'buyer_notes', 'cancellation_reason',
            'importer_contact', 'buyer_info',
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
            'name':  obj.buyer.name,
        }


class CreateReservationSerializer(serializers.Serializer):
    car_id         = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=Reservation.PAYMENT_METHOD_CHOICES, required=False, default='mada')
    buyer_notes    = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_car_id(self, value):
        from django.utils.translation import gettext as _
        try:
            car = Listing.objects.get(pk=value, is_active=True)
        except Listing.DoesNotExist:
            raise serializers.ValidationError(_('Car not found or not available.'))
        if car.is_reserved:
            raise serializers.ValidationError(_('This car is currently reserved.'))
        if car.import_status not in ('available', 'ready_for_delivery'):
            raise serializers.ValidationError(
                _('This car is not available for reservation (status: %(status)s).') % {'status': car.import_status}
            )
        self._car = car
        return value

    def validate(self, data):
        from django.utils.translation import gettext as _
        request = self.context['request']
        car = getattr(self, '_car', None)
        if car and car.owner_id == request.user.id:
            raise serializers.ValidationError(_('You cannot reserve your own listing.'))
        if Reservation.objects.filter(
            car_id=data['car_id'],
            buyer=request.user,
            status__in=['pending_payment', 'active'],
        ).exists():
            raise serializers.ValidationError(_('You already have an active reservation for this car.'))
        data['_car'] = car
        return data

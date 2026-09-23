"""
Import Order & Tracking models (Phase C) + Reservation system (Phase M3).
"""
import datetime
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


class CarAlreadyReserved(Exception):
    """Another paid reservation already holds the lock on this car."""


class ImportOrder(models.Model):
    STATUS_CHOICES = [
        ('pending',             'Pending'),
        ('deposit_requested',   'Deposit Requested'),
        ('deposit_paid',        'Deposit Paid'),
        ('confirmed',           'Confirmed'),
        ('sourcing',            'Sourcing'),
        ('purchased',           'Purchased'),
        ('preparing_shipment',  'Preparing Shipment'),
        ('shipped',             'Shipped'),
        ('arrived_port',        'Arrived at Port'),
        ('in_customs',          'In Customs'),
        ('customs_cleared',     'Customs Cleared'),
        ('inspection',          'Inspection'),
        ('ready',               'Ready for Delivery'),
        ('delivered',           'Delivered'),
        ('completed',           'Completed'),
        ('cancelled',           'Cancelled'),
        ('refunded',            'Refunded'),
        ('disputed',            'Disputed'),
    ]

    DELIVERY_METHOD_CHOICES = [
        ('pickup',   'Pickup'),
        ('delivery', 'Delivery'),
    ]

    VALID_TRANSITIONS = {
        'pending':            ['deposit_requested', 'confirmed', 'cancelled'],
        'deposit_requested':  ['deposit_paid', 'cancelled'],
        'deposit_paid':       ['confirmed', 'refunded'],
        'confirmed':          ['sourcing', 'cancelled'],
        'sourcing':           ['purchased', 'cancelled'],
        'purchased':          ['preparing_shipment'],
        'preparing_shipment': ['shipped'],
        'shipped':            ['arrived_port'],
        'arrived_port':       ['in_customs'],
        'in_customs':         ['customs_cleared'],
        'customs_cleared':    ['inspection'],
        'inspection':         ['ready'],
        'ready':              ['delivered'],
        'delivered':          ['completed'],
        # terminal: completed, cancelled, refunded
    }
    TERMINAL_STATUSES = {'completed', 'cancelled', 'refunded'}

    # Everything from 'purchased' onward means WARED has committed real money
    # on the buyer's behalf. None of it may happen before the buyer's balance
    # payment is confirmed (a succeeded 'balance' PaymentTransaction).
    PAYMENT_GATED_STATUSES = {
        'purchased', 'preparing_shipment', 'shipped', 'arrived_port',
        'in_customs', 'customs_cleared', 'inspection', 'ready',
        'delivered', 'completed',
    }

    # -----------------------------------------------------------------------
    # Core FK relations
    # -----------------------------------------------------------------------
    car = models.ForeignKey(
        'cars.Listing',
        on_delete=models.PROTECT,
        related_name='import_orders',
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='orders_as_buyer',
    )
    importer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='orders_as_importer',
        null=True,
        blank=True,
    )

    # -----------------------------------------------------------------------
    # Order identification
    # -----------------------------------------------------------------------
    order_number = models.CharField(max_length=30, unique=True, blank=True)

    # -----------------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------------
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default='pending', db_index=True
    )

    # -----------------------------------------------------------------------
    # Financial
    # -----------------------------------------------------------------------
    deposit_amount    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    deposit_paid      = models.BooleanField(default=False)
    deposit_paid_at   = models.DateTimeField(null=True, blank=True)
    total_price       = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    remaining_balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # -----------------------------------------------------------------------
    # Delivery
    # -----------------------------------------------------------------------
    delivery_method         = models.CharField(
        max_length=10, choices=DELIVERY_METHOD_CHOICES, default='pickup'
    )
    delivery_address        = models.TextField(blank=True, default='')
    estimated_delivery_date = models.DateField(null=True, blank=True)
    actual_delivery_date    = models.DateField(null=True, blank=True)

    # -----------------------------------------------------------------------
    # Shipment
    # -----------------------------------------------------------------------
    #: What the buyer types into the carrier's website. Blank until the car
    #: ships — `update-status` refuses to set 'shipped' without it — and kept
    #: blank-allowed at the model level so an order that never ships, or one a
    #: staff member forces past the gate, is still a valid row.
    shipment_number = models.CharField(max_length=60, blank=True, default='')
    #: Who is carrying it. Optional: plenty of shipments arrive with a number
    #: and no name worth recording. `Listing.shipping_line` is the same idea
    #: one level up, describing the route rather than this consignment.
    carrier         = models.CharField(max_length=60, blank=True, default='')

    # -----------------------------------------------------------------------
    # Notes & cancellation
    # -----------------------------------------------------------------------
    buyer_notes         = models.TextField(blank=True, default='')
    importer_notes      = models.TextField(blank=True, default='')
    cancellation_reason = models.TextField(blank=True, default='')
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders_cancelled',
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    # -----------------------------------------------------------------------
    # Timestamps
    # -----------------------------------------------------------------------
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['buyer']),
            models.Index(fields=['importer']),
            models.Index(fields=['order_number']),
        ]

    def __str__(self):
        return f"Order {self.order_number} — {self.status}"

    # -----------------------------------------------------------------------
    # Order number generation
    # -----------------------------------------------------------------------
    def _generate_order_number(self):
        prefix = getattr(settings, 'ORDER_NUMBER_PREFIX', 'MKB')
        year = datetime.datetime.now().year
        last = ImportOrder.objects.filter(
            order_number__startswith=f'{prefix}-{year}-'
        ).order_by('-order_number').first()
        if last:
            try:
                seq = int(last.order_number.split('-')[-1]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1
        return f'{prefix}-{year}-{seq:05d}'

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    # -----------------------------------------------------------------------
    # Status transition validation
    # -----------------------------------------------------------------------
    def allowed_next_statuses(self):
        """The statuses this order may legally move to right now."""
        if self.status in self.TERMINAL_STATUSES:
            return []
        return list(self.VALID_TRANSITIONS.get(self.status, []))

    def unlocked_next_statuses(self):
        """
        `allowed_next_statuses()` minus the ones the payment gate would reject
        with a 409 right now.

        Transitions from 'purchased' onward need the buyer's balance confirmed
        (PAYMENT_GATED_STATUSES). The raw list is what
        validate_status_transition enforces; this is what a client should
        offer, so the importer is never shown a button that cannot work yet.
        """
        allowed = self.allowed_next_statuses()
        if self.balance_payment_confirmed():
            return allowed
        return [s for s in allowed if s not in self.PAYMENT_GATED_STATUSES]

    def validate_status_transition(self, new_status):
        """
        Enforce VALID_TRANSITIONS. Rules:
          1. Terminal states (completed/cancelled/refunded) are locked.
          2. The new status must be a real status.
          3. The new status must be in VALID_TRANSITIONS[current].

        Previously this deliberately allowed any status from any status so an
        importer could skip or rewind stages. That let an order reach
        'delivered' straight from 'confirmed' with no sourcing, purchase or
        shipping — and no payment. Staff can still override via `force=true`
        on the update-status endpoint.
        """
        if self.status == new_status:
            return  # no-op
        if self.status in self.TERMINAL_STATUSES:
            raise ValidationError(
                f"Cannot change status from terminal state '{self.status}'."
            )
        valid = {choice[0] for choice in self.STATUS_CHOICES}
        if new_status not in valid:
            raise ValidationError(f"Unknown status '{new_status}'.")

        allowed = self.allowed_next_statuses()
        if new_status not in allowed:
            allowed_text = ', '.join(allowed) if allowed else 'none'
            raise ValidationError(
                f"Cannot move an order from '{self.status}' to '{new_status}'. "
                f"Allowed next statuses: {allowed_text}."
            )

    def balance_payment_confirmed(self) -> bool:
        """True once the buyer's full balance payment has been confirmed."""
        try:
            from payments.models import PaymentTransaction
            return PaymentTransaction.objects.filter(
                order=self, payment_type='balance', status='succeeded',
            ).exists()
        except Exception:
            return False

    def release_car(self):
        """
        This order was cancelled/refunded: put the car back on the market.
        orders.locks.release_listing() leaves it alone if another live deal or
        a newer paid reservation still holds it.
        """
        from orders.locks import release_listing

        if not self.car_id:
            return
        release_listing(self.car, order=self)


# ---------------------------------------------------------------------------
# ImportTimeline
# ---------------------------------------------------------------------------

class ImportTimeline(models.Model):
    EVENT_TYPE_CHOICES = [
        ('order_placed',         'Order Placed'),
        ('deposit_requested',    'Deposit Requested'),
        ('deposit_paid',         'Deposit Paid'),
        ('order_confirmed',      'Order Confirmed'),
        ('sourcing_started',     'Sourcing Started'),
        ('car_purchased',        'Car Purchased'),
        ('preparing_shipment',   'Preparing Shipment'),
        ('shipped',              'Shipped'),
        ('arrived_port',         'Arrived at Port'),
        ('customs_started',      'Customs Started'),
        ('customs_cleared',      'Customs Cleared'),
        ('inspection_scheduled', 'Inspection Scheduled'),
        ('inspection_passed',    'Inspection Passed'),
        ('inspection_failed',    'Inspection Failed'),
        ('ready_for_pickup',     'Ready for Pickup'),
        ('delivered',            'Delivered'),
        ('completed',            'Completed'),
        ('cancelled',            'Cancelled'),
        ('refunded',             'Refunded'),
        ('disputed',             'Disputed'),
        ('note',                 'Note'),
        ('document_uploaded',    'Document Uploaded'),
        ('status_update',        'Status Update'),
        ('payment',              'Payment'),
    ]

    order       = models.ForeignKey(
        ImportOrder, on_delete=models.CASCADE, related_name='timeline_events'
    )
    event_type  = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES)
    title       = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    date        = models.DateTimeField()
    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='timeline_events_created',
    )
    is_public  = models.BooleanField(default=True)   # False = importer/admin-only
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'created_at']

    def __str__(self):
        return f"[{self.order.order_number}] {self.event_type}: {self.title}"


# ---------------------------------------------------------------------------
# OrderDocument
# ---------------------------------------------------------------------------

class OrderDocument(models.Model):
    DOCUMENT_TYPE_CHOICES = [
        ('invoice',             'Invoice'),
        ('bill_of_lading',      'Bill of Lading'),
        ('customs_declaration', 'Customs Declaration'),
        ('customs_clearance',   'Customs Clearance Certificate'),
        ('inspection_report',   'Inspection Report'),
        ('conformity_cert',     'Conformity Certificate'),
        ('title_deed',          'Title Deed'),
        ('delivery_receipt',    'Delivery Receipt'),
        ('other',               'Other'),
    ]

    order         = models.ForeignKey(
        ImportOrder, on_delete=models.CASCADE, related_name='documents'
    )
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPE_CHOICES)
    title         = models.CharField(max_length=255)
    file          = models.FileField(upload_to='orders/documents/')
    uploaded_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_order_documents',
    )
    is_buyer_visible = models.BooleanField(default=True)
    notes            = models.TextField(blank=True, default='')
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.order.order_number}] {self.document_type}: {self.title}"


# ---------------------------------------------------------------------------
# Reservation model (Phase M3)
# ---------------------------------------------------------------------------

class Reservation(models.Model):
    """
    A 99 SAR platform-fee reservation that locks a car for a buyer.
    Not a deposit toward the car price — it's a platform service fee.
    """

    STATUS_CHOICES = [
        ('pending_payment',       'Pending Payment'),
        ('pending_review',        'Pending Importer Review'),
        ('active',                'Active (deprecated)'),
        ('cancelled_by_buyer',    'Cancelled by Buyer'),
        ('cancelled_by_importer', 'Cancelled by Importer'),
        ('converted_to_order',    'Converted to Order'),
        ('expired',               'Expired'),
    ]

    RESERVATION_EXPIRY_DAYS = 7

    PAYMENT_METHOD_CHOICES = [
        ('mada',            'Mada'),
        ('apple_pay',       'Apple Pay'),
        ('stc_pay',         'STC Pay'),
        ('visa_mastercard', 'Visa / Mastercard'),
    ]

    PAYMENT_STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('succeeded', 'Succeeded'),
        ('failed',    'Failed'),
        ('refunded',  'Refunded'),
    ]

    reservation_number = models.CharField(max_length=30, unique=True, blank=True)

    car = models.ForeignKey(
        'cars.Listing',
        on_delete=models.PROTECT,
        related_name='reservations',
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reservations_as_buyer',
    )
    importer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reservations_as_importer',
        null=True, blank=True,
    )

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='pending_payment')

    # Payment
    platform_fee_sar = models.DecimalField(
        max_digits=10, decimal_places=2,
        default=99.00,
    )
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='mada')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    payment_reference = models.CharField(max_length=100, blank=True, default='')
    paid_at = models.DateTimeField(null=True, blank=True)

    # Cancellation
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default='')

    # Notes
    buyer_notes = models.TextField(blank=True, default='')

    # Importer notification tracking
    importer_notified_at = models.DateTimeField(null=True, blank=True)

    # The car's import_status immediately before this reservation locked it.
    # Captured in activate() so expiry/cancellation can put the car back the
    # way it was instead of guessing 'available' — a car reserved while in
    # 'ready_for_delivery' must return to 'ready_for_delivery', not to
    # 'available'.
    car_import_status_before = models.CharField(
        max_length=25, blank=True, default='',
    )

    # Linked order (populated when converted)
    converted_order = models.ForeignKey(
        'orders.ImportOrder',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='source_reservation',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['buyer']),
            models.Index(fields=['importer']),
            models.Index(fields=['car']),
            models.Index(fields=['reservation_number']),
        ]

    def __str__(self):
        return f"Reservation {self.reservation_number} — {self.status}"

    def _generate_reservation_number(self):
        year = datetime.datetime.now().year
        last = Reservation.objects.filter(
            reservation_number__startswith=f'RES-{year}-'
        ).order_by('-reservation_number').first()
        if last:
            try:
                seq = int(last.reservation_number.split('-')[-1]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1
        return f'RES-{year}-{seq:05d}'

    def save(self, *args, **kwargs):
        if not self.reservation_number:
            self.reservation_number = self._generate_reservation_number()
        super().save(*args, **kwargs)

    # -----------------------------------------------------------------------
    # Car lock / release — one implementation, used by every path that ends a
    # reservation, so activate/cancel/expire can never drift apart.
    # -----------------------------------------------------------------------

    def _locked_car(self):
        """The car row, locked FOR UPDATE. Call inside transaction.atomic()."""
        from cars.models import Listing
        car = Listing.objects.select_for_update().get(pk=self.car_id)
        self.car = car
        return car

    def _lock_car(self):
        """Take the car off the market — see orders.locks.lock_listing."""
        from orders.locks import lock_listing

        lock_listing(self)

    def _release_car(self):
        """Put the car back on the market — see orders.locks.release_listing."""
        from orders.locks import release_listing

        release_listing(self.car, reservation=self)

    def activate(self):
        """
        Mark as pending_review (awaiting importer decision), lock the car, and
        make sure the buyer↔importer conversation exists.

        The status change and the car lock commit together or not at all, with
        the car row locked so two buyers paying at once cannot both win.
        Raises CarAlreadyReserved if a different reservation holds the car.
        """
        with transaction.atomic():
            car = self._locked_car()
            if car.is_reserved and car.current_reservation_id not in (None, self.pk):
                raise CarAlreadyReserved(car.pk)
            self.status = 'pending_review'
            self.save(update_fields=['status', 'updated_at'])
            self._lock_car()

        # Imported lazily: the helper lives with the reservation views and
        # importing it at module scope would create a models↔views cycle.
        try:
            from orders.reservation_views import _create_reservation_conversation
            _create_reservation_conversation(self)
        except Exception:
            pass  # Messaging must never block a paid reservation.

    def cancel(self, by: str, reason: str = ''):
        """Cancel and unlock the car."""
        with transaction.atomic():
            self._locked_car()
            self.status = f'cancelled_by_{by}'
            self.cancelled_at = timezone.now()
            self.cancellation_reason = reason
            self.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'updated_at'])
            self._release_car()

    def convert_to_order(self, order):
        """Mark as converted and link to the ImportOrder."""
        self.status = 'converted_to_order'
        self.converted_order = order
        self.save(update_fields=['status', 'converted_order', 'updated_at'])

    def expire(self):
        """Mark as expired and release the car back to the market."""
        with transaction.atomic():
            self._locked_car()
            self.status = 'expired'
            self.cancelled_at = timezone.now()
            self.cancellation_reason = 'انتهت صلاحية الحجز — لم يستجب المستورد خلال 7 أيام'
            self.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'updated_at'])
            self._release_car()

    @property
    def expires_at(self):
        """When an unanswered reservation lapses — created_at + 7 days."""
        if not self.created_at:
            return None
        return self.created_at + datetime.timedelta(days=self.RESERVATION_EXPIRY_DAYS)

    @property
    def hours_remaining(self) -> float:
        """Hours until expiry, floored at 0. Only meaningful while pending."""
        expiry = self.expires_at
        if expiry is None:
            return 0.0
        return round(max(0.0, (expiry - timezone.now()).total_seconds() / 3600), 1)

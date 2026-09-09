"""
Import Order & Tracking models (Phase C) + Reservation system (Phase M3).
"""
import datetime
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


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
    def validate_status_transition(self, new_status):
        """
        Flexible transitions: the importer/admin may move the order to ANY
        stage — forward to skip steps or backward to correct a mistake.
        VALID_TRANSITIONS is kept as the *recommended* next-step chain (the
        UI highlights it), but is no longer enforced. Only two hard rules:
          1. Terminal states (completed/cancelled/refunded) are locked.
          2. The new status must be a real status.
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

    def activate(self):
        """Mark as pending_review (awaiting importer decision) and lock the car."""
        self.status = 'pending_review'
        self.save(update_fields=['status', 'updated_at'])
        # Lock the listing
        car = self.car
        car.is_reserved = True
        car.current_reservation = self
        car.save(update_fields=['is_reserved', 'current_reservation'])

    def cancel(self, by: str, reason: str = ''):
        """Cancel and unlock the car."""
        self.status = f'cancelled_by_{by}'
        self.cancelled_at = timezone.now()
        self.cancellation_reason = reason
        self.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'updated_at'])
        # Unlock the listing
        car = self.car
        car.is_reserved = False
        car.current_reservation = None
        car.save(update_fields=['is_reserved', 'current_reservation'])

    def convert_to_order(self, order):
        """Mark as converted and link to the ImportOrder."""
        self.status = 'converted_to_order'
        self.converted_order = order
        self.save(update_fields=['status', 'converted_order', 'updated_at'])

    def expire(self):
        """Mark as expired and unlock the car."""
        self.status = 'expired'
        self.cancelled_at = timezone.now()
        self.cancellation_reason = 'انتهت صلاحية الحجز — لم يستجب المستورد خلال 7 أيام'
        self.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'updated_at'])
        car = self.car
        car.is_reserved = False
        car.current_reservation = None
        car.save(update_fields=['is_reserved', 'current_reservation'])

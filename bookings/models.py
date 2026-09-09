from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


# ---------------------------------------------------------------------------
# Valid status transitions for the Appointment workflow
# ---------------------------------------------------------------------------

_APPOINTMENT_VALID_TRANSITIONS = {
    'pending':   {'confirmed', 'rejected', 'cancelled'},
    'confirmed': {'cancelled', 'completed', 'no_show'},
    'rejected':  set(),   # terminal
    'cancelled': set(),   # terminal
    'completed': set(),   # terminal
    'no_show':   set(),   # terminal
}


class Appointment(models.Model):
    """
    Booking/appointment model for buyers and sellers.

    Lifecycle:
        buyer creates (pending)
        → seller confirms / rejects / cancels
        → completed / no_show / cancelled
    """

    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('confirmed', 'Confirmed'),
        ('rejected',  'Rejected'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
        ('no_show',   'No Show'),
    ]

    listing = models.ForeignKey(
        'cars.Listing',
        on_delete=models.CASCADE,
        related_name='appointments',
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='appointments_as_buyer',
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='appointments_as_seller',
    )

    appointment_date = models.DateField()
    appointment_time = models.TimeField()
    end_time         = models.TimeField(null=True, blank=True)
    location         = models.CharField(max_length=255, blank=True, default='')
    notes            = models.TextField(max_length=500, blank=True, default='')
    seller_notes     = models.TextField(max_length=500, blank=True, default='')

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
    )
    cancellation_reason = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'appointments'
        ordering = ['-appointment_date', '-appointment_time']
        indexes = [
            models.Index(fields=['listing']),
            models.Index(fields=['buyer']),
            models.Index(fields=['seller']),
            models.Index(fields=['status']),
            models.Index(fields=['appointment_date']),
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def clean(self):
        from django.utils import timezone

        # 1. Appointment date must be today or in the future.
        if self.appointment_date and self.appointment_date < timezone.now().date():
            raise ValidationError(
                {'appointment_date': 'Appointment date cannot be in the past.'}
            )

        if not self.pk:
            # 2. Buyer cannot book their own listing (seller is auto-set to listing.owner).
            if self.buyer_id and self.seller_id and self.buyer_id == self.seller_id:
                raise ValidationError(
                    'You cannot book an appointment for your own listing.'
                )

            # 3. Max 3 pending appointments per buyer per listing (spam prevention).
            if self.buyer_id and self.listing_id:
                pending_count = Appointment.objects.filter(
                    buyer_id=self.buyer_id,
                    listing_id=self.listing_id,
                    status='pending',
                ).count()
                if pending_count >= 3:
                    raise ValidationError(
                        'Maximum 3 pending appointments per listing. '
                        'Please wait for existing ones to be resolved.'
                    )

        # 4. Status transition validation (existing appointments only).
        if self.pk:
            try:
                old_status = Appointment.objects.values_list(
                    'status', flat=True
                ).get(pk=self.pk)
            except Appointment.DoesNotExist:
                return

            if old_status != self.status:
                allowed = _APPOINTMENT_VALID_TRANSITIONS.get(old_status, set())
                if self.status not in allowed:
                    if not allowed:
                        raise ValidationError(
                            f"Status '{old_status}' is terminal — no further changes allowed."
                        )
                    raise ValidationError(
                        f"Invalid status transition from '{old_status}' to '{self.status}'. "
                        f"Allowed next states: {sorted(allowed)}."
                    )

        # 5. No double-booking: seller cannot have 2 confirmed appts at same date+time.
        if (
            self.status == 'confirmed'
            and self.seller_id
            and self.appointment_date
            and self.appointment_time
        ):
            conflict = Appointment.objects.filter(
                seller_id=self.seller_id,
                appointment_date=self.appointment_date,
                appointment_time=self.appointment_time,
                status='confirmed',
            ).exclude(pk=self.pk if self.pk else 0)
            if conflict.exists():
                raise ValidationError(
                    'Seller already has a confirmed appointment at this date and time.'
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"Appointment #{self.pk} — {self.buyer} → {self.listing} "
            f"on {self.appointment_date} [{self.status}]"
        )


# ---------------------------------------------------------------------------
# Phase 4.2 — ServiceBooking (workshop service appointments)
# ---------------------------------------------------------------------------

_SERVICE_BOOKING_VALID_TRANSITIONS = {
    'pending':     {'confirmed', 'cancelled'},
    'confirmed':   {'in_progress', 'cancelled'},
    'in_progress': {'completed', 'cancelled'},
    'completed':   set(),  # terminal
    'cancelled':   set(),  # terminal
}


class ServiceBooking(models.Model):
    """A customer booking for a workshop service."""

    STATUS_CHOICES = [
        ('pending',     'Pending'),
        ('confirmed',   'Confirmed'),
        ('in_progress', 'In Progress'),
        ('completed',   'Completed'),
        ('cancelled',   'Cancelled'),
    ]

    workshop = models.ForeignKey(
        'cars.Workshop',
        on_delete=models.CASCADE,
        related_name='service_bookings',
    )
    service = models.ForeignKey(
        'cars.WorkshopService',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='bookings',
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='service_bookings',
    )

    vehicle_make  = models.CharField(max_length=100)
    vehicle_model = models.CharField(max_length=100)
    vehicle_year  = models.PositiveIntegerField()
    vehicle_plate = models.CharField(max_length=20, blank=True, default='')

    booking_date  = models.DateField()
    booking_time  = models.TimeField()
    description   = models.TextField(max_length=1000, blank=True, default='')

    status              = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    estimated_cost      = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    final_cost          = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes               = models.TextField(blank=True, default='')
    cancellation_reason = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'service_bookings'
        ordering = ['-booking_date', '-booking_time']
        indexes  = [
            models.Index(fields=['workshop']),
            models.Index(fields=['customer']),
            models.Index(fields=['status']),
            models.Index(fields=['booking_date']),
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def clean(self):
        from django.utils import timezone

        # 1. Booking date must be today or future.
        if self.booking_date and self.booking_date < timezone.now().date():
            raise ValidationError(
                {'booking_date': 'Booking date cannot be in the past.'}
            )

        if not self.pk:
            # 2. Customer cannot book their own workshop.
            if self.customer_id and self.workshop_id:
                from cars.models import Workshop
                try:
                    ws = Workshop.objects.get(pk=self.workshop_id)
                    if ws.owner_id and ws.owner_id == self.customer_id:
                        raise ValidationError(
                            'You cannot book your own workshop.'
                        )
                except Workshop.DoesNotExist:
                    pass

            # 3. Max 3 pending bookings per customer per workshop.
            if self.customer_id and self.workshop_id:
                pending_count = ServiceBooking.objects.filter(
                    customer_id=self.customer_id,
                    workshop_id=self.workshop_id,
                    status='pending',
                ).count()
                if pending_count >= 3:
                    raise ValidationError(
                        'Maximum 3 pending bookings per workshop. '
                        'Please wait for existing ones to be resolved.'
                    )

        # 4. Status transition validation (existing bookings only).
        if self.pk:
            try:
                old_status = ServiceBooking.objects.values_list(
                    'status', flat=True
                ).get(pk=self.pk)
            except ServiceBooking.DoesNotExist:
                return

            if old_status != self.status:
                allowed = _SERVICE_BOOKING_VALID_TRANSITIONS.get(old_status, set())
                if self.status not in allowed:
                    if not allowed:
                        raise ValidationError(
                            f"Status '{old_status}' is terminal — no further changes allowed."
                        )
                    raise ValidationError(
                        f"Invalid status transition from '{old_status}' to '{self.status}'. "
                        f"Allowed next states: {sorted(allowed)}."
                    )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"ServiceBooking #{self.pk} — {self.customer} @ {self.workshop} "
            f"on {self.booking_date} [{self.status}]"
        )

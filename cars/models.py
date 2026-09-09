import re

from cloudinary.models import CloudinaryField
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def validate_vin(value):
    """VIN must be exactly 17 alphanumeric characters (if provided)."""
    if value and not re.match(r'^[A-Za-z0-9]{17}$', value):
        raise ValidationError('VIN must be exactly 17 alphanumeric characters.')


class Car(models.Model):
    """Car listing model."""

    FUEL_TYPE_CHOICES = [
        ('PETROL', 'Petrol'),
        ('DIESEL', 'Diesel'),
        ('ELECTRIC', 'Electric'),
        ('HYBRID', 'Hybrid'),
        ('CNG', 'CNG'),
        ('LPG', 'LPG'),
    ]

    TRANSMISSION_CHOICES = [
        ('MANUAL', 'Manual'),
        ('AUTOMATIC', 'Automatic'),
        ('CVT', 'CVT'),
    ]

    CONDITION_CHOICES = [
        ('NEW', 'New'),
        ('USED', 'Used'),
        ('CERTIFIED_PRE_OWNED', 'Certified Pre-owned'),
    ]

    STATUS_CHOICES = [
        ('AVAILABLE', 'Available'),
        ('SOLD', 'Sold'),
        ('PENDING', 'Pending'),
        ('DRAFT', 'Draft'),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    make = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    year = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    mileage = models.IntegerField()
    color = models.CharField(max_length=50, blank=True, null=True)
    fuel_type = models.CharField(max_length=20, choices=FUEL_TYPE_CHOICES)
    transmission = models.CharField(max_length=20, choices=TRANSMISSION_CHOICES)
    condition = models.CharField(max_length=20, choices=CONDITION_CHOICES, default='USED')
    location = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='AVAILABLE')

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='cars',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cars'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['make']),
            models.Index(fields=['model']),
            models.Index(fields=['price']),
            models.Index(fields=['year']),
            models.Index(fields=['status']),
            models.Index(fields=['seller']),
        ]

    def __str__(self):
        return f"{self.year} {self.make} {self.model}"


class CarImage(models.Model):
    """Car image — stored on Cloudinary."""

    car = models.ForeignKey(
        Car,
        on_delete=models.CASCADE,
        related_name='images',
    )
    image = CloudinaryField('image', folder='cars/')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'car_images'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['car']),
        ]

    def __str__(self):
        return f"Image for {self.car}"


class Showroom(models.Model):
    """Showroom model for production marketplace."""

    # --- Original fields (kept for backward compatibility) ---
    name    = models.CharField(max_length=255)
    city    = models.CharField(max_length=100, blank=True, default='')
    address = models.TextField(blank=True, default='')
    phone   = models.CharField(max_length=20, blank=True, default='')
    verified = models.BooleanField(default=False)  # kept for legacy; use is_verified instead
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='showrooms',
    )
    city_obj = models.ForeignKey(
        'locations.City',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='showrooms',
    )
    latitude    = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    logo        = CloudinaryField('logo', folder='showrooms/logos/', null=True, blank=True)
    cover_photo = CloudinaryField('cover', folder='showrooms/covers/', null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    # --- Phase 4.1 — New profile fields ---
    name_ar        = models.CharField(max_length=255, blank=True, default='')
    description    = models.TextField(max_length=2000, blank=True, default='')
    description_ar = models.TextField(max_length=2000, blank=True, default='')
    address_ar     = models.CharField(max_length=300, blank=True, default='')
    whatsapp       = models.CharField(max_length=20, blank=True, default='')
    email          = models.EmailField(blank=True, default='')
    website        = models.URLField(blank=True, default='')

    # Social media
    instagram = models.URLField(blank=True, default='')
    twitter   = models.URLField(blank=True, default='')
    snapchat  = models.CharField(max_length=100, blank=True, default='')
    tiktok    = models.URLField(blank=True, default='')

    # Business info
    commercial_registration = models.CharField(max_length=20, blank=True, default='')
    is_verified    = models.BooleanField(default=False)
    verified_at    = models.DateTimeField(null=True, blank=True)
    is_active      = models.BooleanField(default=True)
    established_year = models.PositiveIntegerField(null=True, blank=True)
    specializations = models.JSONField(default=list, blank=True)

    # Denormalized stats (fast reads)
    total_listings  = models.PositiveIntegerField(default=0)
    active_listings = models.PositiveIntegerField(default=0)
    total_sold      = models.PositiveIntegerField(default=0)
    average_rating  = models.DecimalField(max_digits=3, decimal_places=2, default=0.00)
    total_reviews   = models.PositiveIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'showrooms'
        ordering = ['name']
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['is_verified']),
            models.Index(fields=['city_obj']),
        ]

    def clean(self):
        if self.owner_id and getattr(self.owner, 'role', None) not in ('admin', 'importer'):
            raise ValidationError({'owner': 'Showroom owner must have importer or admin role.'})

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Showroom stats helper — called when listings under a showroom change
# ---------------------------------------------------------------------------

def update_showroom_stats(showroom) -> None:
    """Recalculate and persist denormalized listing counters on *showroom*."""
    listings = Listing.objects.filter(showroom=showroom, is_active=True)
    showroom.total_listings  = listings.count()
    showroom.active_listings = listings.filter(status='approved').count()
    showroom.total_sold      = listings.filter(status='sold').count()
    showroom.save(update_fields=['total_listings', 'active_listings', 'total_sold'])


# ---------------------------------------------------------------------------
# Phase 4.1 — ShowroomWorkingHours
# ---------------------------------------------------------------------------

class ShowroomWorkingHours(models.Model):
    """Opening hours for each day of the week (Sunday=0 per Saudi calendar)."""

    DAY_CHOICES = [
        (0, 'Sunday'),
        (1, 'Monday'),
        (2, 'Tuesday'),
        (3, 'Wednesday'),
        (4, 'Thursday'),
        (5, 'Friday'),
        (6, 'Saturday'),
    ]

    showroom     = models.ForeignKey(Showroom, on_delete=models.CASCADE, related_name='working_hours')
    day          = models.IntegerField(choices=DAY_CHOICES)
    opening_time = models.TimeField(null=True, blank=True)
    closing_time = models.TimeField(null=True, blank=True)
    is_closed    = models.BooleanField(default=False)

    class Meta:
        db_table       = 'showroom_working_hours'
        unique_together = ('showroom', 'day')
        ordering        = ['day']

    def clean(self):
        if not self.is_closed:
            if not self.opening_time or not self.closing_time:
                raise ValidationError(
                    'opening_time and closing_time are required when the showroom is open.'
                )
            if self.opening_time >= self.closing_time:
                raise ValidationError('opening_time must be before closing_time.')

    def __str__(self):
        day_name = dict(self.DAY_CHOICES).get(self.day, self.day)
        if self.is_closed:
            return f"{self.showroom.name} — {day_name}: Closed"
        return f"{self.showroom.name} — {day_name}: {self.opening_time}–{self.closing_time}"


# ---------------------------------------------------------------------------
# Phase 4.1 — ShowroomBranch
# ---------------------------------------------------------------------------

class ShowroomBranch(models.Model):
    """A physical branch of a showroom (max 10 per showroom)."""

    showroom   = models.ForeignKey(Showroom, on_delete=models.CASCADE, related_name='branches')
    name       = models.CharField(max_length=200)
    name_ar    = models.CharField(max_length=200, blank=True, default='')
    city_obj   = models.ForeignKey(
        'locations.City', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='showroom_branches',
    )
    address    = models.CharField(max_length=300, blank=True, default='')
    address_ar = models.CharField(max_length=300, blank=True, default='')
    phone      = models.CharField(max_length=20, blank=True, default='')
    latitude   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_main    = models.BooleanField(default=False)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'showroom_branches'
        ordering = ['-is_main', 'name']

    def save(self, *args, **kwargs):
        # Enforce single main branch per showroom
        if self.is_main:
            ShowroomBranch.objects.filter(
                showroom=self.showroom, is_main=True,
            ).exclude(pk=self.pk).update(is_main=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.showroom.name} — {self.name}"


# ---------------------------------------------------------------------------
# Phase 4.1 — ShowroomReview
# ---------------------------------------------------------------------------

def _recalculate_review_stats(showroom) -> None:
    """Recompute average_rating and total_reviews on *showroom*."""
    from django.db.models import Avg
    reviews = ShowroomReview.objects.filter(showroom=showroom, is_approved=True)
    total   = reviews.count()
    avg     = reviews.aggregate(avg=Avg('rating'))['avg'] or 0
    showroom.average_rating = round(float(avg), 2)
    showroom.total_reviews  = total
    showroom.save(update_fields=['average_rating', 'total_reviews'])


class ShowroomReview(models.Model):
    """A user review/rating for a showroom. One per user per showroom."""

    showroom    = models.ForeignKey(Showroom, on_delete=models.CASCADE, related_name='reviews')
    user        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='showroom_reviews',
    )
    rating      = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    title       = models.CharField(max_length=200, blank=True, default='')
    comment     = models.TextField(max_length=1000, blank=True, default='')
    is_approved = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table       = 'showroom_reviews'
        unique_together = ('showroom', 'user')
        ordering        = ['-created_at']

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        _recalculate_review_stats(self.showroom)

    def delete(self, *args, **kwargs):
        showroom = self.showroom  # capture before deletion
        super().delete(*args, **kwargs)
        _recalculate_review_stats(showroom)

    def __str__(self):
        return f"Review by {self.user} on {self.showroom} — {self.rating}★"


class Workshop(models.Model):
    """Workshop model for production marketplace."""

    # --- Original fields (kept for backward compatibility) ---
    name      = models.CharField(max_length=255)
    city      = models.CharField(max_length=100)
    services  = models.TextField(blank=True, null=True)  # legacy comma-separated; use WorkshopService instead
    phone     = models.CharField(max_length=20, blank=True, null=True)
    rating    = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)  # legacy; use average_rating
    latitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    city_obj  = models.ForeignKey(
        'locations.City',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='workshops',
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workshops',
    )
    logo        = CloudinaryField('logo', folder='workshops/logos/', null=True, blank=True)
    cover_photo = CloudinaryField('cover', folder='workshops/covers/', null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    # --- Phase 4.2 — New profile fields ---
    name_ar        = models.CharField(max_length=255, blank=True, default='')
    description    = models.TextField(max_length=2000, blank=True, default='')
    description_ar = models.TextField(max_length=2000, blank=True, default='')
    address        = models.CharField(max_length=300, blank=True, default='')
    address_ar     = models.CharField(max_length=300, blank=True, default='')
    whatsapp       = models.CharField(max_length=20, blank=True, default='')
    email          = models.EmailField(blank=True, default='')
    website        = models.URLField(blank=True, default='')

    # Social media
    instagram = models.URLField(blank=True, default='')
    twitter   = models.URLField(blank=True, default='')
    snapchat  = models.CharField(max_length=100, blank=True, default='')

    # Business info
    commercial_registration = models.CharField(max_length=20, blank=True, default='')
    is_verified      = models.BooleanField(default=False)
    verified_at      = models.DateTimeField(null=True, blank=True)
    is_active        = models.BooleanField(default=True)
    established_year = models.PositiveIntegerField(null=True, blank=True)
    specializations  = models.JSONField(default=list, blank=True)

    # Denormalized stats
    average_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0.00)
    total_reviews  = models.PositiveIntegerField(default=0)
    total_bookings = models.PositiveIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'workshops'
        ordering = ['name']
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['is_verified']),
            models.Index(fields=['city_obj']),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Phase 4.2 — Workshop stats helpers
# ---------------------------------------------------------------------------

def _recalculate_workshop_review_stats(workshop) -> None:
    """Recompute average_rating and total_reviews on *workshop*."""
    from django.db.models import Avg
    reviews = WorkshopReview.objects.filter(workshop=workshop, is_approved=True)
    total   = reviews.count()
    avg     = reviews.aggregate(avg=Avg('rating'))['avg'] or 0
    workshop.average_rating = round(float(avg), 2)
    workshop.total_reviews  = total
    workshop.save(update_fields=['average_rating', 'total_reviews'])


def update_workshop_booking_stats(workshop) -> None:
    """Recount total_bookings on *workshop*."""
    from bookings.models import ServiceBooking
    workshop.total_bookings = ServiceBooking.objects.filter(workshop=workshop).count()
    workshop.save(update_fields=['total_bookings'])


# ---------------------------------------------------------------------------
# Phase 4.2 — WorkshopWorkingHours
# ---------------------------------------------------------------------------

class WorkshopWorkingHours(models.Model):
    """Opening hours for each day of the week (Sunday=0 per Saudi calendar)."""

    DAY_CHOICES = [
        (0, 'Sunday'),
        (1, 'Monday'),
        (2, 'Tuesday'),
        (3, 'Wednesday'),
        (4, 'Thursday'),
        (5, 'Friday'),
        (6, 'Saturday'),
    ]

    workshop     = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='working_hours')
    day          = models.IntegerField(choices=DAY_CHOICES)
    opening_time = models.TimeField(null=True, blank=True)
    closing_time = models.TimeField(null=True, blank=True)
    is_closed    = models.BooleanField(default=False)

    class Meta:
        db_table        = 'workshop_working_hours'
        unique_together = ('workshop', 'day')
        ordering        = ['day']

    def clean(self):
        if not self.is_closed:
            if not self.opening_time or not self.closing_time:
                raise ValidationError(
                    'opening_time and closing_time are required when the workshop is open.'
                )
            if self.opening_time >= self.closing_time:
                raise ValidationError('opening_time must be before closing_time.')

    def __str__(self):
        day_name = dict(self.DAY_CHOICES).get(self.day, self.day)
        if self.is_closed:
            return f"{self.workshop.name} — {day_name}: Closed"
        return f"{self.workshop.name} — {day_name}: {self.opening_time}–{self.closing_time}"


# ---------------------------------------------------------------------------
# Phase 4.2 — WorkshopService
# ---------------------------------------------------------------------------

class WorkshopService(models.Model):
    """A service offered by a workshop (e.g. Oil Change, Brake Replacement)."""

    CATEGORY_CHOICES = [
        ('maintenance',  'Maintenance'),
        ('repair',       'Repair'),
        ('bodywork',     'Body Work'),
        ('electrical',   'Electrical'),
        ('ac',           'AC & Cooling'),
        ('tires',        'Tires & Wheels'),
        ('engine',       'Engine'),
        ('transmission', 'Transmission'),
        ('diagnostics',  'Diagnostics'),
        ('detailing',    'Detailing'),
        ('other',        'Other'),
    ]

    PRICE_TYPE_CHOICES = [
        ('fixed',          'Fixed'),
        ('starting_from',  'Starting From'),
        ('hourly',         'Hourly'),
        ('contact',        'Contact for Price'),
    ]

    workshop           = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='service_items')
    name               = models.CharField(max_length=200)
    name_ar            = models.CharField(max_length=200, blank=True, default='')
    description        = models.TextField(max_length=500, blank=True, default='')
    description_ar     = models.TextField(max_length=500, blank=True, default='')
    category           = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='other')
    price              = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_type         = models.CharField(max_length=20, choices=PRICE_TYPE_CHOICES, default='fixed')
    duration_minutes   = models.PositiveIntegerField(null=True, blank=True)
    is_active          = models.BooleanField(default=True)
    order              = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'workshop_services'
        ordering = ['order', 'name']

    def __str__(self):
        return f"{self.workshop.name} — {self.name}"


# ---------------------------------------------------------------------------
# Phase 4.2 — WorkshopReview
# ---------------------------------------------------------------------------

class WorkshopReview(models.Model):
    """A user review/rating for a workshop. One per user per workshop."""

    workshop    = models.ForeignKey(Workshop, on_delete=models.CASCADE, related_name='reviews')
    user        = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workshop_reviews',
    )
    rating      = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    title       = models.CharField(max_length=200, blank=True, default='')
    comment     = models.TextField(max_length=1000, blank=True, default='')
    service     = models.ForeignKey(
        WorkshopService, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='reviews',
    )
    is_approved = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'workshop_reviews'
        unique_together = ('workshop', 'user')
        ordering        = ['-created_at']

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        _recalculate_workshop_review_stats(self.workshop)

    def delete(self, *args, **kwargs):
        workshop = self.workshop
        super().delete(*args, **kwargs)
        _recalculate_workshop_review_stats(workshop)

    def __str__(self):
        return f"Review by {self.user} on {self.workshop} — {self.rating}★"


# ---------------------------------------------------------------------------
# Valid status transitions for the Listing workflow.
# ---------------------------------------------------------------------------
_LISTING_VALID_TRANSITIONS = {
    'draft':              {'pending'},
    'pending':            {'approved', 'rejected', 'changes_requested'},
    'approved':           {'sold'},
    'rejected':           {'pending'},
    'changes_requested':  {'pending'},
    'sold':               set(),  # terminal — no further transitions allowed
}


class Listing(models.Model):
    """Listing model for /api/listings."""

    STATUS_CHOICES = [
        ('draft',              'Draft'),
        ('pending',            'Pending'),
        ('approved',           'Approved'),
        ('rejected',           'Rejected'),
        ('changes_requested',  'Changes Requested'),
        ('sold',               'Sold'),
    ]

    BODY_TYPE_CHOICES = [
        ('sedan',       'Sedan'),
        ('suv',         'SUV'),
        ('coupe',       'Coupe'),
        ('hatchback',   'Hatchback'),
        ('truck',       'Truck'),
        ('van',         'Van'),
        ('wagon',       'Wagon'),
        ('convertible', 'Convertible'),
        ('crossover',   'Crossover'),
        ('pickup',      'Pickup'),
        ('other',       'Other'),
    ]

    DRIVE_TYPE_CHOICES = [
        ('fwd', 'Front-Wheel Drive'),
        ('rwd', 'Rear-Wheel Drive'),
        ('awd', 'All-Wheel Drive'),
        ('4wd', '4-Wheel Drive'),
    ]

    FUEL_TYPE_CHOICES = [
        ('petrol',   'Petrol'),
        ('diesel',   'Diesel'),
        ('electric', 'Electric'),
        ('hybrid',   'Hybrid'),
        ('cng',      'CNG'),
        ('lpg',      'LPG'),
    ]

    TRANSMISSION_CHOICES = [
        ('automatic', 'Automatic'),
        ('manual',    'Manual'),
        ('cvt',       'CVT'),
    ]

    CONDITION_CHOICES = [
        ('new',  'New'),
        ('used', 'Used'),
        ('cpo',  'Certified Pre-Owned'),
    ]

    IMPORT_SOURCE_CHOICES = [
        ('local',    'Saudi (Local)'),
        ('gcc',      'GCC Countries'),
        ('american', 'American Import'),
        ('european', 'European Import'),
        ('korean',   'Korean Import'),
        ('japanese', 'Japanese Import'),
        ('other',    'Other'),
    ]

    # --- Core fields ---
    title       = models.CharField(max_length=255)
    make        = models.CharField(max_length=100)
    model       = models.CharField(max_length=100)
    year        = models.IntegerField()
    price       = models.DecimalField(max_digits=12, decimal_places=2)
    mileage     = models.IntegerField()
    city        = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    color       = models.CharField(max_length=50, blank=True)

    # --- Arabic / bilingual fields (Phase 2.12) — all optional ---
    make_ar          = models.CharField(max_length=100, blank=True, default='')
    model_ar         = models.CharField(max_length=100, blank=True, default='')
    description_ar   = models.TextField(blank=True, default='')
    color_ar         = models.CharField(max_length=50,  blank=True, default='')
    color_interior_ar = models.CharField(max_length=50, blank=True, default='')
    city_ar          = models.CharField(max_length=100, blank=True, default='')

    # --- Spec fields ---
    body_type    = models.CharField(max_length=20, choices=BODY_TYPE_CHOICES, blank=True)
    drive_type   = models.CharField(max_length=5,  choices=DRIVE_TYPE_CHOICES, blank=True)
    fuel_type    = models.CharField(max_length=20, choices=FUEL_TYPE_CHOICES, blank=True)
    transmission = models.CharField(max_length=20, choices=TRANSMISSION_CHOICES, blank=True)
    condition    = models.CharField(max_length=10, choices=CONDITION_CHOICES, blank=True)
    engine_size  = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    horsepower   = models.PositiveIntegerField(null=True, blank=True)
    cylinders    = models.PositiveIntegerField(null=True, blank=True)
    seats        = models.PositiveIntegerField(null=True, blank=True, default=5)
    doors        = models.PositiveIntegerField(null=True, blank=True, default=4)
    color_interior = models.CharField(max_length=50, blank=True)

    # --- Location ---
    city_obj  = models.ForeignKey(
        'locations.City',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='listings',
    )
    latitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # --- Saudi-specific fields ---
    imported_from         = models.CharField(max_length=20, choices=IMPORT_SOURCE_CHOICES, default='local', blank=True)
    customs_cleared       = models.BooleanField(default=True)
    accident_history      = models.BooleanField(default=False)
    accident_description  = models.TextField(blank=True)
    warranty_remaining    = models.BooleanField(default=False)
    service_history       = models.BooleanField(default=False)
    negotiable            = models.BooleanField(default=True)

    vin = models.CharField(
        max_length=17,
        unique=True,
        null=True,
        blank=True,
        validators=[validate_vin],
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # --- Admin / status tracking (Phase 2.14) ---
    rejection_reason  = models.TextField(blank=True, default='')
    admin_notes       = models.TextField(blank=True, default='')
    status_changed_at = models.DateTimeField(null=True, blank=True)
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='status_changes',
    )

    # --- View counters (Phase 2.13 — denormalized for fast reads) ---
    view_count        = models.PositiveIntegerField(default=0)
    unique_view_count = models.PositiveIntegerField(default=0)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='listings',
    )
    showroom = models.ForeignKey(
        'Showroom',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='listings',
    )
    workshop = models.ForeignKey(
        'Workshop',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='listings',
    )
    is_active = models.BooleanField(default=True)  # soft delete: False = hidden
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='approved_listings',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    # --- Phase 4.6 — Promotion flags (denormalized for fast queries) ---
    is_featured          = models.BooleanField(default=False)
    is_highlighted       = models.BooleanField(default=False)
    is_top_search        = models.BooleanField(default=False)
    is_homepage          = models.BooleanField(default=False)
    promotion_expires_at = models.DateTimeField(null=True, blank=True)
    promotion_priority   = models.PositiveIntegerField(default=0)

    # ---------------------------------------------------------------------------
    # Import-specific fields
    # ---------------------------------------------------------------------------

    SOURCE_COUNTRY_CHOICES = [
        ('usa', 'USA'), ('uae', 'UAE'), ('japan', 'Japan'), ('korea', 'South Korea'),
        ('qatar', 'Qatar'), ('europe', 'Europe'), ('canada', 'Canada'), ('other', 'Other'),
    ]
    AUCTION_SOURCE_CHOICES = [
        ('copart', 'Copart'), ('iaai', 'IAAI'), ('uss_japan', 'USS Japan'),
        ('manheim', 'Manheim'), ('private', 'Private Sale'), ('dealer', 'Dealer'), ('other', 'Other'),
    ]
    SOURCE_CURRENCY_CHOICES = [
        ('usd', 'USD'), ('aed', 'AED'), ('jpy', 'JPY'), ('krw', 'KRW'),
        ('eur', 'EUR'), ('cad', 'CAD'), ('gbp', 'GBP'), ('qar', 'QAR'),
    ]
    IMPORT_STATUS_CHOICES = [
        ('sourcing', 'Sourcing'), ('available', 'Available'), ('reserved', 'Reserved'),
        ('purchased', 'Purchased'), ('preparing', 'Preparing'), ('shipping', 'Shipping'),
        ('at_port', 'At Port'), ('in_customs', 'In Customs'), ('customs_cleared', 'Customs Cleared'),
        ('inspection', 'Inspection'), ('ready_for_delivery', 'Ready for Delivery'),
        ('delivered', 'Delivered'), ('completed', 'Completed'),
    ]
    PORT_CHOICES = [
        ('jeddah_islamic_port', 'Jeddah Islamic Port'),
        ('king_abdulaziz_port_dammam', 'King Abdulaziz Port (Dammam)'),
        ('jubail_port', 'Jubail Commercial Port'),
    ]
    SPEC_ORIGIN_CHOICES = [
        ('gcc', 'GCC'), ('american', 'American'), ('european', 'European'),
        ('japanese', 'Japanese'), ('korean', 'Korean'),
    ]
    INSPECTION_RESULT_CHOICES = [
        ('pending', 'Pending'), ('passed', 'Passed'), ('failed', 'Failed'), ('not_required', 'Not Required'),
    ]

    # Group 1: Source Information
    source_country       = models.CharField(max_length=20, choices=SOURCE_COUNTRY_CHOICES, blank=True, default='')
    source_country_obj   = models.ForeignKey(
        "source_countries.SourceCountry",
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="listings",
        help_text="FK replacement for source_country CharField (two-phase migration).",
    )
    source_city          = models.CharField(max_length=100, blank=True, default='')
    auction_source       = models.CharField(max_length=20, choices=AUCTION_SOURCE_CHOICES, blank=True, default='')
    auction_lot_number   = models.CharField(max_length=50, blank=True, default='')
    original_listing_url = models.URLField(blank=True, default='')
    source_price         = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    source_currency      = models.CharField(max_length=5, choices=SOURCE_CURRENCY_CHOICES, blank=True, default='usd')

    # Group 2: Cost Breakdown (SAR)
    shipping_cost        = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    customs_duty_amount  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    vat_amount           = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    inspection_fee       = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    transportation_cost  = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_landed_cost    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    final_price_sar      = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Group 3: Import Status
    import_status = models.CharField(max_length=25, choices=IMPORT_STATUS_CHOICES, default='available', blank=True)

    # Group 4: Shipping Information
    vessel_name            = models.CharField(max_length=100, blank=True, default='')
    shipping_line          = models.CharField(max_length=100, blank=True, default='')
    bill_of_lading_number  = models.CharField(max_length=100, blank=True, default='')
    container_number       = models.CharField(max_length=50, blank=True, default='')
    port_of_origin         = models.CharField(max_length=100, blank=True, default='')
    port_of_entry          = models.CharField(max_length=50, choices=PORT_CHOICES, blank=True, default='')
    estimated_arrival_date = models.DateField(null=True, blank=True)
    actual_arrival_date    = models.DateField(null=True, blank=True)

    # Group 5: Customs & Compliance
    customs_declaration_number    = models.CharField(max_length=100, blank=True, default='')
    customs_clearance_date        = models.DateField(null=True, blank=True)
    conformity_certificate_number = models.CharField(max_length=100, blank=True, default='')
    vehicle_inspection_result     = models.CharField(max_length=20, choices=INSPECTION_RESULT_CHOICES, blank=True, default='pending')
    vehicle_inspection_date       = models.DateField(null=True, blank=True)
    gcc_specs                     = models.BooleanField(default=False)
    spec_origin                   = models.CharField(max_length=20, choices=SPEC_ORIGIN_CHOICES, blank=True, default='')
    odometer_verified             = models.BooleanField(default=False)
    emissions_compliant           = models.BooleanField(default=True)

    # Group 6: History & Condition
    carfax_url        = models.URLField(blank=True, default='')
    autocheck_url     = models.URLField(blank=True, default='')
    has_salvage_title = models.BooleanField(default=False)
    has_flood_damage  = models.BooleanField(default=False)
    has_frame_damage  = models.BooleanField(default=False)
    damage_description = models.TextField(blank=True, default='')

    # Reservation lock (Phase M3)
    is_reserved = models.BooleanField(default=False)
    current_reservation = models.ForeignKey(
        'orders.Reservation',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'listings'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['make']),
            models.Index(fields=['model']),
            models.Index(fields=['price']),
            models.Index(fields=['year']),
            models.Index(fields=['city']),
            models.Index(fields=['status']),
            models.Index(fields=['owner']),
            models.Index(fields=['body_type']),
            models.Index(fields=['drive_type']),
            models.Index(fields=['fuel_type']),
            models.Index(fields=['transmission']),
            models.Index(fields=['imported_from']),
            # Phase 4.6 — Promotion indexes for fast featured queries
            models.Index(fields=['is_featured']),
            models.Index(fields=['is_top_search']),
            models.Index(fields=['promotion_priority']),
            models.Index(fields=['source_country_obj', 'import_status']),
        ]

    def clean(self):
        """Enforce valid status transitions. Skipped for new (unsaved) listings."""
        if not self.pk:
            return

        try:
            old_status = Listing.objects.values_list('status', flat=True).get(pk=self.pk)
        except Listing.DoesNotExist:
            return

        new_status = self.status
        if old_status == new_status:
            return

        allowed = _LISTING_VALID_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            if old_status == 'sold':
                raise ValidationError(
                    "Cannot change status from 'sold' — it is a terminal state."
                )
            raise ValidationError(
                f"Invalid status transition from '{old_status}' to '{new_status}'. "
                f"Allowed next states: {sorted(allowed) if allowed else 'none'}."
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def calculate_total_landed_cost(self):
        """Sum all import cost components (source_price treated as SAR equivalent here)."""
        from decimal import Decimal
        total = Decimal('0')
        for field in [
            self.source_price, self.shipping_cost, self.customs_duty_amount,
            self.vat_amount, self.inspection_fee, self.transportation_cost,
        ]:
            if field is not None:
                total += field
        return total

    def __str__(self):
        return f"{self.title} ({self.year} {self.make} {self.model})"


class ListingImage(models.Model):
    """
    Image attached to a Listing — stored on Cloudinary.

    Rules enforced here:
    - Max 20 images per listing (checked in the view before creation).
    - If is_primary=True on save, all sibling images are demoted to is_primary=False.
    - Promotion of the next image to primary (when primary is deleted) is handled in the view.
    """

    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name='images',
    )
    image = CloudinaryField('image', folder='listings/')
    is_primary = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'listing_images'
        ordering = ['order', '-is_primary']
        unique_together = [('listing', 'order')]

    def save(self, *args, **kwargs):
        # Demote all other images for this listing when this one becomes primary.
        if self.is_primary and self.listing_id:
            ListingImage.objects.filter(
                listing_id=self.listing_id,
                is_primary=True,
            ).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Image for {self.listing.title} (primary: {self.is_primary})"


# ---------------------------------------------------------------------------
# Phase 2.9 — Advanced Search
# ---------------------------------------------------------------------------

class SavedSearch(models.Model):
    """Persisted filter set a user can name and replay later."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_searches',
    )
    name = models.CharField(max_length=100)
    filters = models.JSONField(default=dict)
    notify = models.BooleanField(default=False)       # placeholder for Phase 3 notifications
    last_checked = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'saved_searches'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user_id}: {self.name}"


class RecentlyViewed(models.Model):
    """Tracks which listings a user has recently viewed."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recently_viewed',
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name='viewed_by',
    )
    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'recently_viewed'
        ordering = ['-viewed_at']
        unique_together = [('user', 'listing')]

    def __str__(self):
        return f"User {self.user_id} viewed listing {self.listing_id}"


# ---------------------------------------------------------------------------
# Phase 2.13 — View Counter
# ---------------------------------------------------------------------------

class ViewLog(models.Model):
    """
    Immutable record of a single listing page view.

    Unique-view logic (enforced in code, not DB):
    - Authenticated: one entry per user per listing per day.
    - Anonymous:     one entry per IP per listing per day.
    """

    SOURCE_CHOICES = [
        ('search',   'Search'),
        ('direct',   'Direct'),
        ('featured', 'Featured'),
        ('compare',  'Compare'),
        ('shared',   'Shared'),
        ('other',    'Other'),
    ]

    listing    = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name='view_logs',
    )
    user       = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='listing_views',
    )
    ip_address = models.GenericIPAddressField()
    user_agent = models.CharField(max_length=500, blank=True, default='')
    source     = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default='direct'
    )
    viewed_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'view_logs'
        ordering = ['-viewed_at']
        indexes  = [
            models.Index(fields=['listing']),
            models.Index(fields=['user']),
            models.Index(fields=['ip_address']),
            models.Index(fields=['viewed_at']),
        ]

    def __str__(self):
        actor = f"user {self.user_id}" if self.user_id else f"anon {self.ip_address}"
        return f"ViewLog: listing {self.listing_id} by {actor} at {self.viewed_at}"


# ---------------------------------------------------------------------------
# Phase 4.5 — Bulk Operations
# ---------------------------------------------------------------------------

class BulkUpload(models.Model):
    """Tracks a CSV bulk-upload job submitted by a dealer."""

    STATUS_CHOICES = [
        ('pending',    'Pending'),
        ('processing', 'Processing'),
        ('done',       'Done'),
        ('failed',     'Failed'),
    ]

    dealer       = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bulk_uploads',
    )
    file_name    = models.CharField(max_length=255)
    file         = models.FileField(upload_to='bulk_uploads/')
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    total_rows   = models.PositiveIntegerField(default=0)
    successful_rows = models.PositiveIntegerField(default=0)
    failed_rows  = models.PositiveIntegerField(default=0)
    errors       = models.JSONField(default=list)
    created_at   = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'bulk_uploads'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['dealer']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"BulkUpload #{self.pk} by {self.dealer_id} — {self.status}"


# ---------------------------------------------------------------------------
# Phase 4.6 — Promotion Package & Listing Promotion
# ---------------------------------------------------------------------------

class PromotionPackage(models.Model):
    """A purchasable promotion package a dealer can apply to a listing."""

    PROMOTION_TYPE_CHOICES = [
        ('featured',    'Featured'),       # appears in "Featured" section on homepage
        ('highlighted', 'Highlighted'),    # highlighted border/badge in search results
        ('top_search',  'Top of Search'),  # pinned at top of search results
        ('homepage',    'Homepage Banner'),# large banner on homepage
    ]

    name           = models.CharField(max_length=100)
    slug           = models.SlugField(unique=True)
    description    = models.TextField(blank=True, default='')
    duration_days  = models.PositiveIntegerField()
    price          = models.DecimalField(max_digits=10, decimal_places=2)
    promotion_type = models.CharField(max_length=30, choices=PROMOTION_TYPE_CHOICES)
    priority       = models.PositiveIntegerField(default=0)   # higher = more prominent
    is_active      = models.BooleanField(default=True)
    display_order  = models.PositiveIntegerField(default=0)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'promotion_packages'
        ordering = ['display_order']

    def __str__(self):
        return self.name


class ListingPromotion(models.Model):
    """A single paid promotion applied to a listing."""

    STATUS_CHOICES = [
        ('active',          'Active'),
        ('expired',         'Expired'),
        ('cancelled',       'Cancelled'),
        ('pending_payment', 'Pending Payment'),
    ]

    listing     = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name='promotions',
    )
    package     = models.ForeignKey(
        PromotionPackage,
        on_delete=models.PROTECT,
        related_name='promotions',
    )
    dealer      = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='listing_promotions',
    )
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    started_at  = models.DateTimeField(auto_now_add=True)
    expires_at  = models.DateTimeField()
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at  = models.DateTimeField(auto_now_add=True)

    @property
    def is_active_now(self) -> bool:
        return self.status == 'active' and timezone.now() < self.expires_at

    @property
    def days_remaining(self) -> int:
        if self.status != 'active':
            return 0
        delta = self.expires_at - timezone.now()
        return max(0, delta.days)

    class Meta:
        db_table = 'listing_promotions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['listing']),
            models.Index(fields=['dealer']),
            models.Index(fields=['status']),
            models.Index(fields=['expires_at']),
            models.Index(fields=['package']),
        ]

    def __str__(self):
        return f"Promotion #{self.pk} for listing {self.listing_id} — {self.package.name} ({self.status})"

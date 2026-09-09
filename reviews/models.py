from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class Review(models.Model):
    REVIEW_TYPE_CHOICES = [
        ('buyer_to_seller',  'Buyer to Seller'),
        ('seller_to_buyer',  'Seller to Buyer'),
        ('showroom',         'Showroom Review'),
        ('workshop',         'Workshop Review'),
        ('importer_order',   'Importer Order Review'),
    ]
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('approved', 'Approved'),
        ('flagged',  'Flagged'),
        ('rejected', 'Rejected'),
    ]

    reviewer      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='written_reviews')
    reviewed_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_reviews')
    listing       = models.ForeignKey('cars.Listing',  null=True, blank=True, on_delete=models.SET_NULL, related_name='unified_reviews')
    showroom      = models.ForeignKey('cars.Showroom', null=True, blank=True, on_delete=models.SET_NULL, related_name='unified_reviews')
    workshop      = models.ForeignKey('cars.Workshop', null=True, blank=True, on_delete=models.SET_NULL, related_name='unified_reviews')
    order         = models.OneToOneField(
        'orders.ImportOrder',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='review',
    )
    review_type   = models.CharField(max_length=20, choices=REVIEW_TYPE_CHOICES)
    rating        = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    title         = models.CharField(max_length=100)
    comment       = models.TextField(max_length=1000)

    # 4-dimension ratings (only used for importer_order type)
    communication_rating   = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='1–5 rating for importer communication',
    )
    accuracy_rating        = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='1–5 rating for information accuracy',
    )
    delivery_speed_rating  = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='1–5 rating for delivery speed',
    )
    overall_rating         = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='1–5 overall satisfaction rating',
    )

    is_verified_purchase = models.BooleanField(default=False)
    status        = models.CharField(max_length=10, choices=STATUS_CHOICES, default='approved')
    admin_notes   = models.TextField(blank=True, default='')
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['reviewer', 'listing'],
                condition=models.Q(listing__isnull=False),
                name='unique_review_per_listing',
            ),
            models.UniqueConstraint(
                fields=['reviewer', 'order'],
                condition=models.Q(order__isnull=False),
                name='unique_review_per_order',
            ),
        ]
        indexes = [
            models.Index(fields=['reviewed_user']),
            models.Index(fields=['listing']),
            models.Index(fields=['order']),
            models.Index(fields=['review_type']),
            models.Index(fields=['status']),
            models.Index(fields=['rating']),
            models.Index(fields=['-created_at']),
        ]

    def _compute_importer_rating(self):
        """Return the average of the 4 dimension scores, rounded to nearest int."""
        dims = [
            self.communication_rating,
            self.accuracy_rating,
            self.delivery_speed_rating,
            self.overall_rating,
        ]
        provided = [d for d in dims if d is not None]
        if not provided:
            return None
        return round(sum(provided) / len(provided))

    def save(self, *args, **kwargs):
        # Auto-compute composite rating for importer_order reviews
        if self.review_type == 'importer_order':
            computed = self._compute_importer_rating()
            if computed is not None:
                self.rating = computed
        super().save(*args, **kwargs)


class ReviewReply(models.Model):
    review    = models.OneToOneField(Review, on_delete=models.CASCADE, related_name='reply')
    author    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    comment   = models.TextField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

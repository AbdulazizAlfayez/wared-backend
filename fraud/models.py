from django.conf import settings
from django.db import models


class FraudFlag(models.Model):
    FLAG_TYPE_CHOICES = [
        ('duplicate_vin',       'Duplicate VIN'),
        ('suspicious_price',    'Suspicious Price'),
        ('excessive_listings',  'Excessive Listings'),
        ('rapid_posting',       'Rapid Posting'),
        ('banned_keywords',     'Banned Keywords'),
        ('ip_abuse',            'IP Abuse'),
        ('fake_images',         'Fake Images'),
        ('price_manipulation',  'Price Manipulation'),
    ]
    SEVERITY_CHOICES = [
        ('low',      'Low'),
        ('medium',   'Medium'),
        ('high',     'High'),
        ('critical', 'Critical'),
    ]

    listing      = models.ForeignKey('cars.Listing',  null=True, blank=True, on_delete=models.CASCADE, related_name='fraud_flags')
    user         = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='fraud_flags')
    flag_type    = models.CharField(max_length=30, choices=FLAG_TYPE_CHOICES)
    severity     = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='medium')
    details      = models.JSONField(default=dict, blank=True)
    is_resolved  = models.BooleanField(default=False)
    resolved_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='resolved_fraud_flags')
    resolved_at  = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True, default='')
    auto_detected = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['listing']),
            models.Index(fields=['user']),
            models.Index(fields=['flag_type']),
            models.Index(fields=['severity']),
            models.Index(fields=['is_resolved']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f"FraudFlag({self.flag_type}, {self.severity}, resolved={self.is_resolved})"


class IPLog(models.Model):
    ACTION_CHOICES = [
        ('login',          'Login'),
        ('register',       'Register'),
        ('create_listing', 'Create Listing'),
        ('submit_lead',    'Submit Lead'),
        ('submit_report',  'Submit Report'),
    ]

    user       = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='ip_logs')
    ip_address = models.GenericIPAddressField()
    action     = models.CharField(max_length=20, choices=ACTION_CHOICES)
    user_agent = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['ip_address']),
            models.Index(fields=['action']),
            models.Index(fields=['-created_at']),
        ]


class ListingLimit(models.Model):
    user            = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='listing_limit')
    daily_limit     = models.PositiveIntegerField(default=5)
    listings_today  = models.PositiveIntegerField(default=0)
    last_reset      = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"ListingLimit(user={self.user_id}, {self.listings_today}/{self.daily_limit})"

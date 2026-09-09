from django.conf import settings
from django.db import models


class ImporterProfile(models.Model):
    user                    = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='importer_profile')
    business_name           = models.CharField(max_length=200)
    business_name_ar        = models.CharField(max_length=200, blank=True, default='')
    commercial_registration = models.CharField(max_length=50, blank=True, default='')
    customs_broker_license  = models.CharField(max_length=100, blank=True, default='')
    import_license_number   = models.CharField(max_length=100, blank=True, default='')
    specializations         = models.JSONField(default=list, blank=True)
    source_countries        = models.JSONField(default=list, blank=True)
    years_in_business       = models.PositiveIntegerField(default=0)
    total_cars_imported     = models.PositiveIntegerField(default=0)
    average_delivery_days   = models.PositiveIntegerField(default=0)
    success_rate            = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    description             = models.TextField(blank=True, default='')
    description_ar          = models.TextField(blank=True, default='')
    phone                   = models.CharField(max_length=20, blank=True, default='')
    whatsapp                = models.CharField(max_length=20, blank=True, default='')
    email                   = models.EmailField(blank=True, default='')
    website                 = models.URLField(blank=True, default='')
    logo                    = models.CharField(max_length=500, blank=True, default='')
    cover_photo             = models.CharField(max_length=500, blank=True, default='')
    instagram               = models.URLField(blank=True, default='')
    twitter                 = models.URLField(blank=True, default='')
    snapchat                = models.CharField(max_length=100, blank=True, default='')
    tiktok                  = models.URLField(blank=True, default='')
    address                 = models.TextField(blank=True, default='')
    address_ar              = models.TextField(blank=True, default='')
    city                    = models.ForeignKey('locations.City', null=True, blank=True, on_delete=models.SET_NULL)
    latitude                = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude               = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_verified             = models.BooleanField(default=False)
    verified_at             = models.DateTimeField(null=True, blank=True)
    average_rating              = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    total_reviews               = models.PositiveIntegerField(default=0)
    avg_communication_rating    = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    avg_accuracy_rating         = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    avg_delivery_speed_rating   = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    avg_overall_rating          = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    created_at                  = models.DateTimeField(auto_now_add=True)
    updated_at                  = models.DateTimeField(auto_now=True)

    # CR Lifecycle (Phase B)
    cr_issue_date           = models.DateField(null=True, blank=True)
    cr_expiry_date          = models.DateField(null=True, blank=True)
    cr_last_confirmed_date  = models.DateField(null=True, blank=True)
    cr_document             = models.FileField(upload_to='cr_documents/', null=True, blank=True)
    CR_STATUS_CHOICES = [
        ('pending_initial', 'Pending Initial Verification'),
        ('verified', 'Verified & Active'),
        ('expiring_soon', 'Expiring Soon'),
        ('expired', 'Expired'),
        ('suspended', 'Suspended (CR Expired)'),
        ('renewal_under_review', 'Renewal Under Review'),
    ]
    cr_verification_status  = models.CharField(max_length=30, choices=CR_STATUS_CHOICES, default='pending_initial')
    cr_suspended_at         = models.DateTimeField(null=True, blank=True)
    cr_reactivated_at       = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.business_name


class CRVerificationHistory(models.Model):
    ACTION_CHOICES = [
        ('initial_submission', 'Initial CR Submitted'),
        ('initial_verified', 'Initial CR Verified'),
        ('renewal_submitted', 'Renewal Submitted'),
        ('renewal_approved', 'Renewal Approved'),
        ('renewal_rejected', 'Renewal Rejected'),
        ('auto_suspended', 'Auto-Suspended (Expired)'),
        ('manual_suspended', 'Manually Suspended'),
        ('reactivated', 'Reactivated'),
        ('reminder_sent_60', '60-Day Reminder Sent'),
        ('reminder_sent_30', '30-Day Reminder Sent'),
        ('reminder_sent_7', '7-Day Reminder Sent'),
        ('reminder_sent_expired', 'Expiry-Day Reminder Sent'),
    ]

    importer_profile = models.ForeignKey(ImporterProfile, on_delete=models.CASCADE, related_name='cr_history')
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    old_status = models.CharField(max_length=30, blank=True, default='')
    new_status = models.CharField(max_length=30, blank=True, default='')
    cr_expiry_date = models.DateField(null=True, blank=True)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='cr_actions_performed',
    )
    notes = models.TextField(blank=True, default='')
    document_uploaded = models.FileField(upload_to='cr_renewals/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.importer_profile.business_name} — {self.get_action_display()}"

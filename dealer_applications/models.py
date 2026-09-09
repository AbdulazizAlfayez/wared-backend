from django.conf import settings
from django.db import models


class ImporterApplication(models.Model):
    """
    A request from a registered user to become an importer.
    Submitted via POST /api/importer-applications/.
    """

    BUSINESS_TYPE_CHOICES = [
        ('individual', 'Individual'),
        ('dealership', 'Dealership'),
        ('showroom',   'Showroom'),
    ]

    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    applicant         = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='importer_applications',
    )
    business_name     = models.CharField(max_length=255)
    business_type     = models.CharField(max_length=20, choices=BUSINESS_TYPE_CHOICES, default='individual')
    city              = models.CharField(max_length=100)
    phone             = models.CharField(max_length=20)
    email             = models.EmailField(blank=True, default='')
    address           = models.CharField(max_length=300, blank=True, default='')
    description       = models.TextField(blank=True, default='')
    expected_listings = models.CharField(max_length=20, blank=True, default='')
    status            = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_notes       = models.TextField(blank=True, default='')
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dealer_applications'
        ordering = ['-created_at']
        verbose_name = 'Importer Application'
        verbose_name_plural = 'Importer Applications'
        indexes  = [
            models.Index(fields=['status']),
            models.Index(fields=['applicant']),
        ]

    def __str__(self):
        return f"{self.applicant.email} — {self.business_name} ({self.status})"

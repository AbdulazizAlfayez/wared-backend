from django.conf import settings
from django.db import models
from django.utils import timezone


class Report(models.Model):
    REPORT_TYPE_CHOICES = [
        ('listing',  'Listing'),
        ('user',     'User'),
        ('showroom', 'Showroom'),
        ('workshop', 'Workshop'),
        ('review',   'Review'),
        ('message',  'Message'),
    ]
    REASON_CHOICES = [
        ('fake_listing',   'Fake Listing'),
        ('wrong_price',    'Wrong/Misleading Price'),
        ('wrong_info',     'Incorrect Information'),
        ('duplicate',      'Duplicate Listing'),
        ('scam',           'Scam/Fraud'),
        ('inappropriate',  'Inappropriate Content'),
        ('harassment',     'Harassment'),
        ('spam',           'Spam'),
        ('stolen_photos',  'Stolen Photos'),
        ('sold_vehicle',   'Vehicle Already Sold'),
        ('impersonation',  'Impersonation'),
        ('other',          'Other'),
    ]
    STATUS_CHOICES = [
        ('pending',       'Pending Review'),
        ('investigating', 'Under Investigation'),
        ('resolved',      'Resolved'),
        ('dismissed',     'Dismissed'),
    ]
    PRIORITY_CHOICES = [
        ('low',    'Low'),
        ('medium', 'Medium'),
        ('high',   'High'),
        ('urgent', 'Urgent'),
    ]
    ACTION_CHOICES = [
        ('none',              'No Action'),
        ('warning_issued',    'Warning Issued'),
        ('listing_removed',   'Listing Removed'),
        ('listing_suspended', 'Listing Suspended'),
        ('user_warned',       'User Warned'),
        ('user_suspended',    'User Suspended'),
        ('user_banned',       'User Banned'),
    ]

    # Auto-priority by reason severity
    _REASON_PRIORITY = {
        'harassment':    'urgent',
        'scam':          'high',
        'impersonation': 'high',
        'stolen_photos': 'high',
        'fake_listing':  'medium',
        'inappropriate': 'medium',
        'other':         'medium',
        'wrong_price':   'low',
        'wrong_info':    'low',
        'duplicate':     'low',
        'spam':          'low',
        'sold_vehicle':  'low',
    }

    reporter    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reports_filed',
    )
    report_type = models.CharField(max_length=20, choices=REPORT_TYPE_CHOICES)
    reason      = models.CharField(max_length=50, choices=REASON_CHOICES)
    description = models.TextField(max_length=1000)

    listing = models.ForeignKey(
        'cars.Listing',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reports',
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reports_against',
    )
    reported_showroom = models.ForeignKey(
        'cars.Showroom',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reports',
    )
    reported_workshop = models.ForeignKey(
        'cars.Workshop',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reports',
    )
    evidence = models.FileField(upload_to='reports/evidence/', null=True, blank=True)

    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    priority     = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    admin_notes  = models.TextField(blank=True, default='')
    action_taken = models.CharField(max_length=50, choices=ACTION_CHOICES, default='none')

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='resolved_reports',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['reporter']),
            models.Index(fields=['status']),
            models.Index(fields=['priority']),
            models.Index(fields=['report_type']),
            models.Index(fields=['created_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.pk:
            self.priority = self._REASON_PRIORITY.get(self.reason, 'medium')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Report #{self.pk} [{self.report_type}/{self.reason}] by {self.reporter}"


class UserModeration(models.Model):
    ACTION_CHOICES = [
        ('warning',    'Warning'),
        ('suspension', 'Temporary Suspension'),
        ('ban',        'Permanent Ban'),
        ('lifted',     'Restriction Lifted'),
    ]

    user   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='moderation_actions',
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    reason = models.TextField()
    related_report = models.ForeignKey(
        Report,
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='moderation_actions_issued',
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_action_display()} on {self.user} by {self.issued_by}"

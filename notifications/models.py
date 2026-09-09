from django.conf import settings
from django.db import models


class NotificationPreference(models.Model):
    """
    Per-user opt-in/opt-out for each notification type.
    Created on first access via get_or_create.
    email_notifications and sms_notifications are reserved for future phases.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preferences',
    )

    # In-app notification toggles — map to notification_type values
    listing_approved      = models.BooleanField(default=True)
    listing_rejected      = models.BooleanField(default=True)
    new_lead              = models.BooleanField(default=True)
    lead_status_changed   = models.BooleanField(default=True)
    new_message           = models.BooleanField(default=True)
    conversation_started  = models.BooleanField(default=True)
    appointment_confirmed = models.BooleanField(default=True)
    appointment_rejected  = models.BooleanField(default=True)
    appointment_cancelled = models.BooleanField(default=True)
    price_drop            = models.BooleanField(default=True)
    listing_expiring      = models.BooleanField(default=True)
    system                = models.BooleanField(default=True)

    # Order status notification toggles (Phase C)
    order_updates_email = models.BooleanField(default=True)
    order_updates_sms   = models.BooleanField(default=True)
    order_updates_push  = models.BooleanField(default=True)

    # Channel toggles (future phases)
    email_notifications = models.BooleanField(default=True)
    sms_notifications   = models.BooleanField(default=False)

    class Meta:
        db_table = 'notification_preferences'

    def __str__(self):
        return f"Preferences for {self.user}"


class Notification(models.Model):
    NOTIFICATION_TYPE_CHOICES = [
        ('listing_approved',      'Listing Approved'),
        ('listing_rejected',      'Listing Rejected'),
        ('new_lead',              'New Lead'),
        ('lead_status_changed',   'Lead Status Changed'),
        ('appointment_confirmed', 'Appointment Confirmed'),
        ('appointment_rejected',  'Appointment Rejected'),
        ('appointment_cancelled', 'Appointment Cancelled'),
        ('price_drop',            'Price Drop'),
        ('listing_expiring',      'Listing Expiring'),
        ('system',                'System Notification'),
        # Phase 3.1 — Messaging
        ('new_message',           'New Message'),
        ('conversation_started',  'Conversation Started'),
        ('user_blocked',          'User Blocked'),
        # Phase 5.1 — Verification
        ('verification_approved', 'Verification Approved'),
        ('verification_rejected', 'Verification Rejected'),
        ('admin_alert',           'Admin Alert'),
    ]

    recipient         = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPE_CHOICES)
    title             = models.CharField(max_length=200)
    message           = models.TextField()
    is_read           = models.BooleanField(default=False)
    listing           = models.ForeignKey(
        'cars.Listing',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='notifications',
    )
    lead              = models.ForeignKey(
        'leads.Lead',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='notifications',
    )
    appointment       = models.ForeignKey(
        'bookings.Appointment',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='notifications',
    )
    metadata          = models.JSONField(default=dict, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['recipient']),
            models.Index(fields=['is_read']),
            models.Index(fields=['notification_type']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.notification_type} → user {self.recipient_id}: {self.title[:60]}"

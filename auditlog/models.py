from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    ACTION_CHOICES = [
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('status_change', 'Status Change'),
        # Written by cars.views.request_changes since that action shipped; it
        # was missing here, so the row persisted (choices are not enforced at
        # the database level) but `get_action_display()` had nothing to show.
        ('request_changes', 'Request Changes'),
        # An admin opening a record in the inspector. Reading a person's file
        # is itself an event worth being able to account for.
        ('view', 'View'),
        ('login', 'Login'),
        ('logout', 'Logout'),
        ('login_failed', 'Login Failed'),
        ('password_reset_requested', 'Password Reset Requested'),
        ('password_reset_completed', 'Password Reset Completed'),
        ('password_changed', 'Password Changed'),
        ('otp_login', 'OTP Login'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='audit_logs',
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=100)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    #: Device string, where the caller had one. Truncated by `log_action`.
    user_agent = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['model_name']),
            # The inspector's per-object trail. `model_name` alone is barely
            # selective — most rows are listings — so the composite is what
            # keeps "every event for this record" cheap.
            models.Index(fields=['model_name', 'object_id', '-timestamp']),
        ]

    def __str__(self):
        return f"{self.user} {self.action} {self.model_name} #{self.object_id} at {self.timestamp}"

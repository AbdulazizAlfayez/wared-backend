from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


_LEAD_VALID_TRANSITIONS = {
    'new':       {'contacted', 'closed', 'spam'},
    'contacted': {'closed', 'spam'},
    'closed':    set(),   # terminal
    'spam':      set(),   # terminal
}


class Lead(models.Model):
    STATUS_CHOICES = [
        ('new',        'New'),
        ('contacted',  'Contacted'),
        ('closed',     'Closed'),
        ('spam',       'Spam'),
    ]

    SOURCE_CHOICES = [
        ('search_result',  'Search Result'),
        ('listing_page',   'Listing Page'),
        ('featured',       'Featured'),
        ('compare',        'Compare'),
        ('homepage',       'Homepage'),
        ('other',          'Other'),
    ]

    listing = models.ForeignKey(
        'cars.Listing',
        on_delete=models.CASCADE,
        related_name='leads',
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='leads_sent',
    )
    dealer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='leads_received',
    )
    message = models.TextField()
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    preferred_time = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default='listing_page')
    dealer_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'leads'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['dealer']),
            models.Index(fields=['listing']),
            models.Index(fields=['created_at']),
        ]

    def clean(self):
        """Enforce valid status transitions. Skipped for new (unsaved) leads."""
        if not self.pk:
            return

        try:
            old_status = Lead.objects.values_list('status', flat=True).get(pk=self.pk)
        except Lead.DoesNotExist:
            return

        new_status = self.status
        if old_status == new_status:
            return

        allowed = _LEAD_VALID_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            if not allowed:
                raise ValidationError(
                    f"Cannot change status from '{old_status}' — it is a terminal state."
                )
            raise ValidationError(
                f"Invalid status transition from '{old_status}' to '{new_status}'. "
                f"Allowed next states: {sorted(allowed)}."
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Lead from {self.buyer.email} for {self.listing.title}"

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class BlockedUser(models.Model):
    """A user blocks another user from messaging them."""

    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='blocked_users',
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='blocked_by',
    )
    reason     = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'blocked_users'
        unique_together = ('blocker', 'blocked')
        ordering        = ['-created_at']

    def clean(self):
        if self.blocker_id and self.blocked_id and self.blocker_id == self.blocked_id:
            raise ValidationError('You cannot block yourself.')

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.blocker} blocked {self.blocked}"


class Conversation(models.Model):
    """
    One thread per buyer per listing.
    unique_together('listing', 'buyer', 'seller') is enforced at DB level.
    """

    listing = models.ForeignKey(
        'cars.Listing',
        on_delete=models.CASCADE,
        related_name='conversations',
    )
    lead = models.ForeignKey(
        'leads.Lead',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='conversations',
    )
    reservation = models.ForeignKey(
        'orders.Reservation',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='conversations',
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversations_as_buyer',
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversations_as_seller',
    )
    is_active       = models.BooleanField(default=True)
    buyer_archived  = models.BooleanField(default=False)
    seller_archived = models.BooleanField(default=False)

    # Denormalized fields for fast listing
    last_message_at      = models.DateTimeField(null=True, blank=True)
    last_message_preview = models.CharField(max_length=100, blank=True, default='')
    unread_count_buyer   = models.IntegerField(default=0)
    unread_count_importer = models.IntegerField(default=0)

    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table        = 'conversations'
        unique_together = ('listing', 'buyer', 'seller')
        ordering        = ['-last_message_at', '-updated_at']
        indexes         = [
            models.Index(fields=['buyer']),
            models.Index(fields=['seller']),
            models.Index(fields=['listing']),
            models.Index(fields=['updated_at']),
            models.Index(fields=['last_message_at']),
        ]

    def __str__(self):
        return f"Conv #{self.pk}: {self.buyer} ↔ {self.seller} on {self.listing}"

    def update_last_message(self, message):
        """Update denormalized fields after a new message."""
        self.last_message_at = message.created_at
        self.last_message_preview = (message.content or '')[:100]
        # Increment unread for the other party
        if message.sender_id == self.buyer_id:
            self.unread_count_importer = models.F('unread_count_importer') + 1
        else:
            self.unread_count_buyer = models.F('unread_count_buyer') + 1
        self.save(update_fields=['last_message_at', 'last_message_preview',
                                 'unread_count_buyer', 'unread_count_importer', 'updated_at'])


MESSAGE_TYPE_CHOICES = [
    ('text', 'Text'),
    ('image', 'Image'),
    ('system', 'System'),
]


class Message(models.Model):
    """A single message in a Conversation."""

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_messages',
    )
    content      = models.TextField(max_length=2000, blank=True, default='')
    image        = models.ImageField(upload_to='message_images/', blank=True, null=True)
    message_type = models.CharField(max_length=10, choices=MESSAGE_TYPE_CHOICES, default='text')
    is_read      = models.BooleanField(default=False)
    read_at      = models.DateTimeField(null=True, blank=True)
    is_system    = models.BooleanField(default=False)
    contains_contact_attempt = models.BooleanField(default=False)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'messages'
        ordering = ['created_at']
        indexes  = [
            models.Index(fields=['conversation']),
            models.Index(fields=['sender']),
            models.Index(fields=['is_read']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"Msg #{self.pk} in Conv #{self.conversation_id} by {self.sender_id}"

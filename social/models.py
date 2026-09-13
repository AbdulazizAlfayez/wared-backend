from django.conf import settings
from django.db import models


class ListingLike(models.Model):
    """A single user's like on a listing. Toggled, never edited."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='listing_likes',
    )
    listing = models.ForeignKey(
        'cars.Listing',
        on_delete=models.CASCADE,
        related_name='likes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'social_listing_likes'
        unique_together = ('user', 'listing')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['listing']),
        ]

    def __str__(self):
        return f"{self.user} likes listing #{self.listing_id}"


class ListingComment(models.Model):
    """
    A public comment on a listing, or a seller's reply to one.

    Threads are exactly one level deep: a reply carries `parent`, and a reply
    may never itself be replied to. That is enforced in the serializer rather
    than the model so the API returns a 400 with a message instead of an
    integrity error.

    Comments are never hard-deleted. `is_deleted` is the author removing their
    own words; `is_hidden` is moderation, set by staff or automatically once a
    comment collects enough distinct reports. A deleted comment that still has
    visible replies is kept in the thread as a tombstone so the replies below
    it do not become orphans.
    """

    BODY_MAX_LENGTH = 500

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='listing_comments',
    )
    listing = models.ForeignKey(
        'cars.Listing',
        on_delete=models.CASCADE,
        related_name='comments',
    )
    body = models.TextField(max_length=BODY_MAX_LENGTH)
    parent = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='replies',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)

    class Meta:
        db_table = 'social_listing_comments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['listing', 'created_at']),
        ]

    def __str__(self):
        return f"Comment #{self.pk} by {self.user} on listing #{self.listing_id}"

    @property
    def is_owner_reply(self):
        """True when this is the listing owner answering on their own car."""
        return self.parent_id is not None and self.user_id == self.listing.owner_id


class CommentReport(models.Model):
    """One user flagging one comment. Unique per (comment, reporter)."""

    REASON_CHOICES = [
        ('spam', 'Spam'),
        ('abuse', 'Abuse'),
        ('scam', 'Scam'),
        ('other', 'Other'),
    ]

    #: Distinct reports that auto-hide a comment.
    AUTO_HIDE_THRESHOLD = 3

    comment = models.ForeignKey(
        ListingComment,
        on_delete=models.CASCADE,
        related_name='reports',
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='comment_reports',
    )
    reason = models.CharField(max_length=10, choices=REASON_CHOICES)
    note = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'social_comment_reports'
        unique_together = ('comment', 'reporter')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['comment']),
        ]

    def __str__(self):
        return f"Report on comment #{self.comment_id} by {self.reporter} ({self.reason})"

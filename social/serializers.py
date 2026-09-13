import re

from rest_framework import serializers

# Email detection is shared with messaging: an address is unambiguous, so one
# policy can cover both surfaces. Phone detection is NOT shared. Messaging's
# _PHONE_RE treats any 8+ run of digits, spaces, dashes and dots as a number —
# whitespace sits inside its character class, so it spans separate figures and
# rejects ordinary car talk like "2019 88000 km". Comments use the narrower
# pattern defined below instead.
from messaging.utils import _EMAIL_RE

from .models import CommentReport, ListingComment

CONTACT_ERROR = 'Contact details are not allowed in comments — use in-app messaging.'

#: The eight digits that follow a Saudi mobile prefix, each allowed to be led
#: by spaces or dashes so "05 1234 5678" and "05-1234-5678" read as one number.
#: Requiring all eight is what keeps a price like "105 500 SAR" legible: it
#: contains "0" then "5", but not a whole mobile number behind it.
_EIGHT_MORE_DIGITS = r'(?:[\s\-]*\d){8}'

#: What counts as a phone number inside a comment: a Saudi mobile, either
#: international (+9665…, 009665…, 9665…) or local (05…), with spaces and
#: dashes allowed throughout; or an unbroken run of nine or more digits.
#: Years, mileages and prices pass — "2019 88000 km", "1.5 million",
#: "105 500 SAR", "1,050,000" — because none of them is either of those.
_COMMENT_PHONE_RE = re.compile(
    r'(?:\+|00)?[\s\-]*966[\s\-]*5' + _EIGHT_MORE_DIGITS
    + r'|0[\s\-]*5' + _EIGHT_MORE_DIGITS
    + r'|\d{9,}',
    re.UNICODE,
)

#: Shown in place of an author-deleted comment that still has visible replies.
DELETED_BODY = '[deleted]'


def validate_comment_body(value):
    """Shared by create and by any future edit path."""
    body = (value or '').strip()
    if not body:
        raise serializers.ValidationError('Comment cannot be blank.')
    if len(body) > ListingComment.BODY_MAX_LENGTH:
        raise serializers.ValidationError(
            f'Comment cannot exceed {ListingComment.BODY_MAX_LENGTH} characters.'
        )
    if _COMMENT_PHONE_RE.search(body) or _EMAIL_RE.search(body):
        raise serializers.ValidationError(CONTACT_ERROR)
    return body


class CommentAuthorSerializer(serializers.Serializer):
    """
    The author, as much of them as is public.

    `avatar_url` is always null: the User model carries no avatar field today.
    It is emitted anyway so clients can bind to a stable shape rather than
    branching on a key that appears later.
    """

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, obj):
        return getattr(obj, 'avatar_url', None)


class ListingCommentSerializer(serializers.ModelSerializer):
    """The read shape, used for both list and create responses."""

    user = CommentAuthorSerializer(read_only=True)
    body = serializers.SerializerMethodField()
    is_owner_reply = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    replies = serializers.SerializerMethodField()
    report_count = serializers.SerializerMethodField()

    class Meta:
        model = ListingComment
        fields = [
            'id', 'user', 'body', 'created_at',
            'is_owner_reply', 'replies', 'can_delete', 'report_count',
        ]
        read_only_fields = fields

    def get_body(self, obj):
        # A deleted parent is kept as a tombstone so its replies keep their
        # context; its words are not returned to anyone.
        return DELETED_BODY if obj.is_deleted else obj.body

    def get_is_owner_reply(self, obj):
        return obj.parent_id is not None and obj.user_id == self._listing_owner_id(obj)

    def get_can_delete(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated or obj.is_deleted:
            return False
        return obj.user_id == request.user.id

    def get_replies(self, obj):
        # Only top-level comments carry replies; one level, oldest first.
        if obj.parent_id is not None:
            return []
        visible = [
            reply for reply in obj.replies.all()
            if not reply.is_deleted and not reply.is_hidden
        ]
        visible.sort(key=lambda reply: reply.created_at)
        return ListingCommentSerializer(visible, many=True, context=self.context).data

    def get_report_count(self, obj):
        """Staff only — absent for everyone else rather than zeroed."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated or not request.user.is_staff:
            return None
        annotated = getattr(obj, 'report_total', None)
        return annotated if annotated is not None else obj.reports.count()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Drop the key entirely for non-staff, rather than leaking its existence.
        if data.get('report_count') is None:
            data.pop('report_count', None)
        return data

    def _listing_owner_id(self, obj):
        listing = self.context.get('listing')
        return listing.owner_id if listing is not None else obj.listing.owner_id


class ListingCommentCreateSerializer(serializers.ModelSerializer):
    """Create only. The listing and author come from the view, never the body."""

    class Meta:
        model = ListingComment
        fields = ['body', 'parent']

    def validate_body(self, value):
        return validate_comment_body(value)

    def validate_parent(self, value):
        if value is None:
            return value

        listing = self.context['listing']
        request = self.context['request']

        if value.listing_id != listing.id:
            raise serializers.ValidationError('Parent comment belongs to another listing.')
        # One level only: a reply may not itself be replied to.
        if value.parent_id is not None:
            raise serializers.ValidationError('Replies cannot be replied to.')
        if value.is_deleted or value.is_hidden:
            raise serializers.ValidationError('Cannot reply to a removed comment.')
        # NOTE: who may reply is an authorisation question, not a validation
        # one, so it is enforced in the view and surfaces as 403. Everything
        # checked here is structural and correctly a 400.
        return value


class CommentReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommentReport
        fields = ['id', 'reason', 'note', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate_note(self, value):
        return (value or '').strip()

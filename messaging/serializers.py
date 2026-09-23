from django.utils import timezone
from rest_framework import serializers

from .models import BlockedUser, Conversation, Message


# ---------------------------------------------------------------------------
# Shared user mini-serializer
# ---------------------------------------------------------------------------

class UserMiniSerializer(serializers.Serializer):
    id    = serializers.IntegerField()
    name  = serializers.CharField()
    email = serializers.EmailField()
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, obj):
        try:
            return obj.avatar.url if obj.avatar else None
        except Exception:
            return None


class ListingMiniSerializer(serializers.Serializer):
    id                = serializers.IntegerField()
    make              = serializers.CharField()
    model             = serializers.CharField()
    year              = serializers.IntegerField()
    price             = serializers.DecimalField(max_digits=12, decimal_places=2)
    primary_image_url = serializers.SerializerMethodField()

    def get_primary_image_url(self, obj):
        primary = obj.images.filter(is_primary=True).first() or obj.images.first()
        if primary and primary.image:
            try:
                return primary.image.url
            except Exception:
                return None
        return None


# ---------------------------------------------------------------------------
# Message serializers
# ---------------------------------------------------------------------------

class MessageSerializer(serializers.ModelSerializer):
    sender    = UserMiniSerializer(read_only=True)
    is_mine   = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    content   = serializers.SerializerMethodField()

    class Meta:
        model  = Message
        fields = ('id', 'sender', 'content', 'image_url', 'message_type',
                  'is_read', 'read_at', 'is_system', 'is_mine', 'created_at')
        read_only_fields = fields

    def get_content(self, obj):
        from .utils import contact_exchange_allowed, mask_contact_info

        request = self.context.get('request')
        # Admin/staff always see raw content
        if request and request.user.is_authenticated and request.user.is_staff:
            return obj.content
        # Balance confirmed on this conversation's order: raw content
        if contact_exchange_allowed(obj.conversation):
            return obj.content
        # Otherwise mask
        masked, _ = mask_contact_info(obj.content)
        return masked

    def get_is_mine(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return obj.sender_id == request.user.pk
        return False

    def get_image_url(self, obj):
        if obj.image:
            try:
                return obj.image.url
            except Exception:
                return None
        return None


class MessageCreateSerializer(serializers.Serializer):
    content      = serializers.CharField(max_length=2000, allow_blank=True, required=False, default='')
    message_type = serializers.ChoiceField(choices=['text', 'image'], default='text', required=False)

    def validate(self, data):
        if data.get('message_type') == 'text' and not data.get('content', '').strip():
            raise serializers.ValidationError({'content': 'Message content cannot be empty.'})
        return data


# ---------------------------------------------------------------------------
# Conversation serializers
# ---------------------------------------------------------------------------

class ConversationListSerializer(serializers.ModelSerializer):
    listing      = ListingMiniSerializer(read_only=True)
    other_party  = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    # Scoped to this conversation's buyer, importer and listing — see
    # messaging.utils.contact_exchange_allowed / conversation_deal.
    can_exchange_contacts = serializers.SerializerMethodField()
    deal         = serializers.SerializerMethodField()
    my_role      = serializers.SerializerMethodField()
    # The ball is in my court: the last thing said came from the other party.
    # Distinct from `unread_count`, which goes to zero the moment the thread
    # is opened — a message read and not answered is still owed a reply, and
    # that is the one an importer's inbox has to keep showing.
    unanswered_by_me = serializers.SerializerMethodField()

    class Meta:
        model  = Conversation
        fields = (
            'id', 'listing', 'other_party', 'last_message',
            'last_message_preview', 'last_message_at',
            'unread_count', 'unanswered_by_me',
            'is_active', 'created_at', 'updated_at',
            'can_exchange_contacts', 'deal', 'my_role',
        )
        read_only_fields = fields

    def _requester(self):
        return self.context.get('request').user if self.context.get('request') else None

    def get_other_party(self, obj):
        me = self._requester()
        if me is None:
            return None
        other = obj.seller if obj.buyer_id == me.pk else obj.buyer
        is_verified = False
        try:
            is_verified = other.importer_profile.is_verified
        except Exception:
            pass
        avatar_url = None
        try:
            if other.avatar:
                avatar_url = other.avatar.url
        except Exception:
            pass
        return {
            'id':          other.pk,
            'name':        other.name,
            'avatar_url':  avatar_url,
            'is_verified': is_verified,
        }

    def get_last_message(self, obj):
        msg = obj.messages.order_by('-created_at').first()
        if msg is None:
            return None
        return {
            'content':    msg.content,
            'sender_id':  msg.sender_id,
            'is_read':    msg.is_read,
            'is_system':  msg.is_system,
            'created_at': msg.created_at,
        }

    def get_can_exchange_contacts(self, obj):
        from .utils import contact_exchange_allowed
        return contact_exchange_allowed(obj)

    def get_deal(self, obj):
        from .utils import conversation_deal
        return conversation_deal(obj)

    def get_my_role(self, obj):
        me = self._requester()
        if me is None:
            return None
        return 'buyer' if obj.buyer_id == me.pk else 'seller'

    def get_unanswered_by_me(self, obj):
        me = self._requester()
        if me is None:
            return False
        last = obj.messages.order_by('-created_at').first()
        if last is None or last.is_system:
            # A system note ("this car was reserved") is not a question.
            return False
        return last.sender_id != me.pk

    def get_unread_count(self, obj):
        me = self._requester()
        if me is None:
            return 0
        if obj.buyer_id == me.pk:
            return obj.unread_count_buyer
        return obj.unread_count_importer


class ConversationDetailSerializer(ConversationListSerializer):
    recent_messages = serializers.SerializerMethodField()

    class Meta(ConversationListSerializer.Meta):
        fields = ConversationListSerializer.Meta.fields + (
            'lead_id', 'buyer_archived', 'seller_archived', 'recent_messages',
        )
        read_only_fields = fields

    def get_recent_messages(self, obj):
        msgs = obj.messages.select_related('sender').order_by('-created_at')[:50]
        return MessageSerializer(reversed(list(msgs)), many=True, context=self.context).data


# ---------------------------------------------------------------------------
# Start conversation (with reservation gate)
# ---------------------------------------------------------------------------

class StartConversationSerializer(serializers.Serializer):
    car_id = serializers.IntegerField()
    buyer_id = serializers.IntegerField(required=False)

    def validate(self, attrs):
        from cars.models import Listing
        from .models import BlockedUser

        request = self.context['request']

        try:
            # The moderation gate: you cannot open a conversation about a car
            # the admin queue has not approved. Staff and the importer who
            # owns it are exempt, and the lock is not applied — a buyer must
            # still be able to message about the car they have reserved.
            listing = Listing.objects.moderated(request.user).select_related('owner').get(
                pk=attrs['car_id']
            )
        except Listing.DoesNotExist:
            raise serializers.ValidationError({'car_id': 'السيارة غير موجودة.'})

        buyer_id = attrs.get('buyer_id')
        if buyer_id and listing.owner_id == request.user.pk:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            try:
                buyer = User.objects.get(pk=buyer_id, is_active=True)
            except User.DoesNotExist:
                raise serializers.ValidationError({'buyer_id': 'المستخدم غير موجود.'})
            if buyer.pk == request.user.pk:
                raise serializers.ValidationError({'buyer_id': 'لا يمكنك مراسلة نفسك.'})
            seller = request.user
        else:
            buyer = request.user
            if listing.owner_id == buyer.pk:
                raise serializers.ValidationError({'car_id': 'لا يمكنك مراسلة نفسك.'})
            seller = listing.owner

        # Block check
        if BlockedUser.objects.filter(blocker=buyer, blocked=seller).exists():
            raise serializers.ValidationError('لقد قمت بحظر هذا المستخدم.')
        if BlockedUser.objects.filter(blocker=seller, blocked=buyer).exists():
            raise serializers.ValidationError('هذا المستخدم قام بحظرك.')

        # Find most recent reservation to link (optional)
        reservation = None
        try:
            from orders.models import Reservation
            reservation = Reservation.objects.filter(
                car=listing, buyer=buyer,
            ).order_by('-created_at').first()
        except Exception:
            pass

        attrs['listing'] = listing
        attrs['seller'] = seller
        attrs['reservation'] = reservation
        attrs['buyer'] = buyer
        return attrs


# ---------------------------------------------------------------------------
# BlockedUser serializer
# ---------------------------------------------------------------------------

class BlockedUserSerializer(serializers.ModelSerializer):
    blocked = UserMiniSerializer(read_only=True)

    class Meta:
        model  = BlockedUser
        fields = ('id', 'blocked', 'reason', 'created_at')
        read_only_fields = ('id', 'blocked', 'created_at')

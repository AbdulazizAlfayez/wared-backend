import statistics

from rest_framework import serializers

from .models import ImporterProfile

#: How far back the response-time median looks.
RESPONSE_WINDOW_DAYS = 90


def median_first_reply_hours(user_id):
    """
    Median hours between a buyer's opening message and this seller's first
    reply, over `RESPONSE_WINDOW_DAYS`. None when nothing has been answered.

    Counts each conversation once: the first inbound message and the first
    reply that follows it. A later exchange in the same thread says nothing
    about how quickly a stranger gets an answer.
    """
    from django.utils import timezone
    from datetime import timedelta

    from messaging.models import Conversation, Message

    since = timezone.now() - timedelta(days=RESPONSE_WINDOW_DAYS)
    conversations = Conversation.objects.filter(
        seller_id=user_id, created_at__gte=since,
    ).values_list('pk', flat=True)
    if not conversations:
        return None

    messages = (
        Message.objects
        .filter(conversation_id__in=list(conversations), is_system=False)
        .order_by('conversation_id', 'created_at')
        .values_list('conversation_id', 'sender_id', 'created_at')
    )

    first_inbound = {}
    measured = set()
    deltas = []
    for conversation_id, sender_id, created_at in messages:
        if conversation_id in measured:
            continue
        if sender_id == user_id:
            asked_at = first_inbound.get(conversation_id)
            if asked_at is not None:
                deltas.append((created_at - asked_at).total_seconds() / 3600)
                measured.add(conversation_id)
            # A seller who writes first has not "replied" to anything; wait
            # for the buyer's message and time the answer to that.
        else:
            first_inbound.setdefault(conversation_id, created_at)

    if not deltas:
        return None
    return round(statistics.median(deltas), 1)


class CityBriefSerializer(serializers.Serializer):
    id      = serializers.IntegerField()
    name_en = serializers.CharField()
    name_ar = serializers.CharField()
    #: The region the city sits in. A buyer in Dammam reads "Eastern Province"
    #: as a distance; the city alone only helps if they know the map.
    region  = serializers.SerializerMethodField()

    def get_region(self, obj):
        region = getattr(obj, 'region', None)
        if region is None:
            return None
        return {'id': region.pk, 'name_en': region.name_en, 'name_ar': region.name_ar}


# ---------------------------------------------------------------------------
# Contact fields that must NEVER appear in public API responses.
# ---------------------------------------------------------------------------
_CONTACT_FIELDS = {
    'phone', 'whatsapp', 'email', 'website',
    'instagram', 'twitter', 'snapchat', 'tiktok',
}


class ImporterProfileListSerializer(serializers.ModelSerializer):
    # A property now (see ImporterProfile.is_verified), so it needs declaring:
    # ModelSerializer only introspects concrete fields.
    is_verified = serializers.BooleanField(read_only=True)
    city    = CityBriefSerializer(read_only=True)
    user_id = serializers.IntegerField(source='user.id', read_only=True)

    class Meta:
        model  = ImporterProfile
        fields = [
            'id', 'user_id', 'business_name', 'business_name_ar',
            'logo', 'specializations', 'source_countries',
            'city', 'is_verified',
            'average_rating', 'total_reviews',
            'total_cars_imported', 'years_in_business',
        ]


class _ImporterDetailBase(serializers.ModelSerializer):
    """Shared computed fields for both public and owner/admin detail."""
    is_verified            = serializers.BooleanField(read_only=True)
    city                   = CityBriefSerializer(read_only=True)
    user_id                = serializers.IntegerField(source='user.id', read_only=True)
    active_listings_count  = serializers.SerializerMethodField()
    completed_orders_count = serializers.SerializerMethodField()
    # The seller card a buyer reads before starting a conversation, and the
    # same numbers the importer previews as their public profile.
    response_time_hours    = serializers.SerializerMethodField()
    rating_avg             = serializers.SerializerMethodField()
    reviews_count          = serializers.SerializerMethodField()
    cars_live              = serializers.SerializerMethodField()
    member_since           = serializers.SerializerMethodField()

    def get_response_time_hours(self, obj):
        """
        How long this importer usually takes to reply first, in hours.

        The MEDIAN over the last 90 days, not the mean: one forgotten thread
        open for three weeks would drag an average past the point of being
        useful, while the median still says what a buyer should expect.
        Conversations they never answered are not counted — this measures
        speed of reply, and `unanswered_by_me` on the conversation is what
        surfaces silence. None until there are replies to measure.
        """
        return median_first_reply_hours(obj.user_id)

    def get_rating_avg(self, obj):
        return float(obj.average_rating) if obj.average_rating else None

    def get_reviews_count(self, obj):
        return obj.total_reviews or 0

    def get_cars_live(self, obj):
        """Cars a buyer can actually open right now."""
        from cars.models import Listing

        return Listing.objects.public().filter(owner=obj.user).count()

    def get_member_since(self, obj):
        joined = getattr(obj.user, 'date_joined', None)
        return joined.isoformat() if joined else None

    def get_active_listings_count(self, obj):
        from cars.models import Listing
        from cars.visibility import public_market_q
        return Listing.objects.filter(
            public_market_q(),
            owner=obj.user,
            is_active=True,
            import_status='available',
        ).count()

    def get_completed_orders_count(self, obj):
        from orders.models import ImportOrder
        return ImportOrder.objects.filter(importer=obj.user, status='completed').count()


class ImporterProfileDetailSerializer(_ImporterDetailBase):
    """PUBLIC detail — excludes all contact fields."""

    class Meta:
        model  = ImporterProfile
        exclude = list(_CONTACT_FIELDS)


class ImporterProfileOwnerSerializer(_ImporterDetailBase):
    """Owner / admin detail — includes every field."""

    class Meta:
        model  = ImporterProfile
        fields = '__all__'


class ImporterProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model   = ImporterProfile
        exclude = [
            'user', 'verified_at',
            'average_rating', 'total_reviews', 'total_cars_imported',
            'created_at', 'updated_at',
        ]

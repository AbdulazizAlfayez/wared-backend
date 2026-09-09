from rest_framework import serializers

from .models import Lead, _LEAD_VALID_TRANSITIONS


# ---------------------------------------------------------------------------
# Nested sub-serializers (read-only)
# ---------------------------------------------------------------------------

class _BuyerInfoSerializer(serializers.Serializer):
    id    = serializers.IntegerField()
    name  = serializers.CharField()
    email = serializers.EmailField()
    phone = serializers.CharField()


class _ListingInfoSerializer(serializers.Serializer):
    id            = serializers.IntegerField()
    title         = serializers.CharField()
    make          = serializers.CharField()
    model         = serializers.CharField()
    year          = serializers.IntegerField()
    price         = serializers.DecimalField(max_digits=12, decimal_places=2)
    primary_image = serializers.SerializerMethodField()

    def get_primary_image(self, obj):
        primary = obj.images.filter(is_primary=True).first() or obj.images.first()
        if primary and primary.image:
            return primary.image.url
        return None


# ---------------------------------------------------------------------------
# 1. LeadCreateSerializer — buyer submits a lead
# ---------------------------------------------------------------------------

class LeadCreateSerializer(serializers.ModelSerializer):
    """
    Used by buyers to submit a new lead.
    buyer and importer are resolved automatically, not from request body.
    """

    class Meta:
        model  = Lead
        fields = ('listing', 'message', 'phone', 'email', 'preferred_time', 'source')

    def validate_listing(self, listing):
        request = self.context['request']
        # Buyer cannot submit a lead on their own listing
        if listing.owner == request.user:
            raise serializers.ValidationError(
                "You cannot submit a lead on your own listing."
            )
        # Limit 3 leads per buyer per listing (anti-spam)
        existing = Lead.objects.filter(listing=listing, buyer=request.user).count()
        if existing >= 3:
            raise serializers.ValidationError(
                "You have already submitted 3 leads on this listing."
            )
        return listing


# ---------------------------------------------------------------------------
# 2. LeadSerializer — full read serializer
# ---------------------------------------------------------------------------

class LeadSerializer(serializers.ModelSerializer):
    """
    Full representation of a lead.
    dealer_notes is only exposed to the importer who owns the lead and admins.
    """

    buyer_info   = serializers.SerializerMethodField()
    listing_info = serializers.SerializerMethodField()

    class Meta:
        model  = Lead
        fields = (
            'id', 'listing', 'buyer', 'dealer',
            'message', 'phone', 'email', 'preferred_time',
            'status', 'source', 'dealer_notes',
            'buyer_info', 'listing_info',
            'created_at', 'updated_at',
        )
        read_only_fields = fields

    def get_buyer_info(self, obj):
        return _BuyerInfoSerializer(obj.buyer).data

    def get_listing_info(self, obj):
        return _ListingInfoSerializer(obj.listing, context=self.context).data

    def to_representation(self, instance):
        data    = super().to_representation(instance)
        request = self.context.get('request')
        # Hide dealer_notes from anyone who is not the dealer or an admin
        if request and request.user.is_authenticated:
            is_dealer = (instance.dealer_id == request.user.pk)
            is_admin  = (getattr(request.user, 'role', None) == 'admin')
            if not (is_dealer or is_admin):
                data.pop('dealer_notes', None)
        else:
            data.pop('dealer_notes', None)
        return data


# ---------------------------------------------------------------------------
# 3. LeadUpdateSerializer — dealer updates status / dealer_notes
# ---------------------------------------------------------------------------

class LeadUpdateSerializer(serializers.ModelSerializer):
    """
    Used by importers (and admins) to update status and/or dealer_notes.
    Validates status transitions.
    """

    class Meta:
        model  = Lead
        fields = ('status', 'dealer_notes')

    def validate_status(self, new_status):
        instance = self.instance
        if instance is None:
            return new_status
        old_status = instance.status
        if old_status == new_status:
            return new_status
        allowed = _LEAD_VALID_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            if not allowed:
                raise serializers.ValidationError(
                    f"Cannot change status from '{old_status}' — it is a terminal state."
                )
            raise serializers.ValidationError(
                f"Invalid status transition from '{old_status}' to '{new_status}'. "
                f"Allowed: {sorted(allowed)}."
            )
        return new_status

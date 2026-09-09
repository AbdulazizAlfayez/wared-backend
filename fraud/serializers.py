from rest_framework import serializers
from .models import FraudFlag, IPLog, ListingLimit


class FraudFlagSerializer(serializers.ModelSerializer):
    listing_title  = serializers.SerializerMethodField()
    user_email     = serializers.SerializerMethodField()
    resolved_by_email = serializers.SerializerMethodField()

    class Meta:
        model  = FraudFlag
        fields = [
            'id', 'flag_type', 'severity', 'details',
            'listing', 'listing_title',
            'user', 'user_email',
            'auto_detected', 'is_resolved',
            'resolved_by', 'resolved_by_email', 'resolved_at', 'resolution_notes',
            'created_at',
        ]

    def get_listing_title(self, obj):
        if obj.listing:
            return f"{obj.listing.year} {obj.listing.make} {obj.listing.model}"
        return None

    def get_user_email(self, obj):
        return obj.user.email if obj.user else None

    def get_resolved_by_email(self, obj):
        return obj.resolved_by.email if obj.resolved_by else None


class FraudFlagResolveSerializer(serializers.Serializer):
    resolution_notes = serializers.CharField(required=False, allow_blank=True)


class IPLogSerializer(serializers.ModelSerializer):
    user_email = serializers.SerializerMethodField()

    class Meta:
        model  = IPLog
        fields = ['id', 'user', 'user_email', 'ip_address', 'action', 'user_agent', 'created_at']

    def get_user_email(self, obj):
        return obj.user.email if obj.user else None


class ListingLimitSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ListingLimit
        fields = ['user', 'daily_limit', 'listings_today', 'last_reset']

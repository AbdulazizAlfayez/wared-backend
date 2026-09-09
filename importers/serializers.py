from rest_framework import serializers
from .models import ImporterProfile


class CityBriefSerializer(serializers.Serializer):
    id      = serializers.IntegerField()
    name_en = serializers.CharField()
    name_ar = serializers.CharField()


# ---------------------------------------------------------------------------
# Contact fields that must NEVER appear in public API responses.
# ---------------------------------------------------------------------------
_CONTACT_FIELDS = {
    'phone', 'whatsapp', 'email', 'website',
    'instagram', 'twitter', 'snapchat', 'tiktok',
}


class ImporterProfileListSerializer(serializers.ModelSerializer):
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
    city                   = CityBriefSerializer(read_only=True)
    user_id                = serializers.IntegerField(source='user.id', read_only=True)
    active_listings_count  = serializers.SerializerMethodField()
    completed_orders_count = serializers.SerializerMethodField()

    def get_active_listings_count(self, obj):
        from cars.models import Listing
        return Listing.objects.filter(
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
            'user', 'is_verified', 'verified_at',
            'average_rating', 'total_reviews', 'total_cars_imported',
            'created_at', 'updated_at',
        ]

from rest_framework import serializers

from .models import City, Region


# ---------------------------------------------------------------------------
# Language helper (Phase 2.12)
# ---------------------------------------------------------------------------

class BilingualMixin:
    """Shared language-detection helper for location serializers."""

    def _lang(self) -> str:
        request = self.context.get('request')
        if request:
            accept = request.headers.get('Accept-Language', 'en')
            code = accept[:2].lower()
            return code if code in ('en', 'ar') else 'en'
        return 'en'


# ---------------------------------------------------------------------------
# City
# ---------------------------------------------------------------------------

class CitySerializer(BilingualMixin, serializers.ModelSerializer):
    region_name_en = serializers.CharField(source='region.name_en', read_only=True)
    region_name_ar = serializers.CharField(source='region.name_ar', read_only=True)
    name_display   = serializers.SerializerMethodField(read_only=True)
    region_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = City
        fields = (
            'id', 'name_en', 'name_ar', 'name_display', 'slug',
            'region', 'region_name_en', 'region_name_ar', 'region_display',
            'latitude', 'longitude',
        )

    def get_name_display(self, obj):
        if self._lang() == 'ar' and obj.name_ar:
            return obj.name_ar
        return obj.name_en

    def get_region_display(self, obj):
        if self._lang() == 'ar' and obj.region.name_ar:
            return obj.region.name_ar
        return obj.region.name_en


# ---------------------------------------------------------------------------
# Region
# ---------------------------------------------------------------------------

class RegionSerializer(BilingualMixin, serializers.ModelSerializer):
    cities_count = serializers.IntegerField(read_only=True)
    name_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Region
        fields = ('id', 'name_en', 'name_ar', 'name_display', 'slug', 'cities_count')

    def get_name_display(self, obj):
        if self._lang() == 'ar' and obj.name_ar:
            return obj.name_ar
        return obj.name_en


class RegionDetailSerializer(BilingualMixin, serializers.ModelSerializer):
    cities       = CitySerializer(many=True, read_only=True)
    name_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Region
        fields = ('id', 'name_en', 'name_ar', 'name_display', 'slug', 'cities')

    def get_name_display(self, obj):
        if self._lang() == 'ar' and obj.name_ar:
            return obj.name_ar
        return obj.name_en

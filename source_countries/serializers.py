from rest_framework import serializers

from .models import SourceCountry


class SourceCountrySerializer(serializers.ModelSerializer):
    """Public read-only serializer for source countries."""

    class Meta:
        model = SourceCountry
        fields = [
            "code",
            "name_en",
            "name_ar",
            "iso_code",
            "flag_emoji",
            "latitude",
            "longitude",
            "avg_shipping_cost_sar",
            "avg_shipping_days",
            "description",
            "description_ar",
            "display_order",
        ]
        read_only_fields = fields


class SourceCountryAdminSerializer(serializers.ModelSerializer):
    """Admin serializer — full CRUD, timestamps read-only."""

    class Meta:
        model = SourceCountry
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at"]


class CountryAggregateSerializer(serializers.Serializer):
    """Aggregated car stats per country for the world-map endpoint."""

    code = serializers.CharField()
    name_en = serializers.CharField()
    name_ar = serializers.CharField()
    iso_code = serializers.CharField()
    flag_emoji = serializers.CharField()
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    total_cars = serializers.IntegerField()
    available_cars = serializers.IntegerField()
    arriving_soon_cars = serializers.IntegerField()
    reserved_cars = serializers.IntegerField()
    avg_price_sar = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    min_price_sar = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    max_price_sar = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    popular_makes = serializers.ListField(child=serializers.CharField())
    avg_shipping_days = serializers.IntegerField()
    avg_shipping_cost_sar = serializers.DecimalField(max_digits=10, decimal_places=2)

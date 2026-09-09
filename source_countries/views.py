from django.core.cache import cache
from django.db.models import Avg, Count, Max, Min
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SourceCountry
from .serializers import (
    CountryAggregateSerializer,
    SourceCountryAdminSerializer,
    SourceCountrySerializer,
)
from .signals import CACHE_KEY_BY_COUNTRY

CACHE_TTL = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Public read-only
# ---------------------------------------------------------------------------
class SourceCountryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = SourceCountrySerializer
    queryset = SourceCountry.objects.filter(is_active=True)
    lookup_field = "code"


# ---------------------------------------------------------------------------
# Admin CRUD
# ---------------------------------------------------------------------------
class SourceCountryAdminViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]
    serializer_class = SourceCountryAdminSerializer
    queryset = SourceCountry.objects.all()
    lookup_field = "code"

    def _bust(self):
        cache.delete(CACHE_KEY_BY_COUNTRY)

    def perform_create(self, serializer):
        serializer.save()
        self._bust()

    def perform_update(self, serializer):
        serializer.save()
        self._bust()

    def perform_destroy(self, instance):
        # Soft delete
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        self._bust()


# ---------------------------------------------------------------------------
# Aggregation endpoint — main payload for the World Map page
# ---------------------------------------------------------------------------
class ImportedCarsByCountryView(APIView):
    permission_classes = [AllowAny]

    # Status buckets (match Listing.IMPORT_STATUS_CHOICES)
    AVAILABLE_STATUSES = {"available"}
    RESERVED_STATUSES = {"reserved"}
    ARRIVING_STATUSES = {
        "purchased",
        "preparing",
        "shipping",
        "at_port",
        "in_customs",
        "customs_cleared",
        "inspection",
    }
    PUBLIC_STATUSES = AVAILABLE_STATUSES | RESERVED_STATUSES | ARRIVING_STATUSES

    def get(self, request):
        cached = cache.get(CACHE_KEY_BY_COUNTRY)
        if cached is not None:
            return Response(cached)

        # Lazy import to avoid circular dependency
        from cars.models import Listing

        countries = SourceCountry.objects.filter(is_active=True)
        results = []
        total_globally = 0

        for country in countries:
            cars = Listing.objects.filter(
                source_country=country.code,
                import_status__in=self.PUBLIC_STATUSES,
                is_active=True,
            )
            total = cars.count()
            if total == 0 and country.code == "other":
                continue  # skip "other" when empty

            available = cars.filter(import_status__in=self.AVAILABLE_STATUSES).count()
            arriving = cars.filter(import_status__in=self.ARRIVING_STATUSES).count()
            reserved = cars.filter(import_status__in=self.RESERVED_STATUSES).count()

            price_agg = cars.aggregate(
                avg=Avg("final_price_sar"),
                min=Min("final_price_sar"),
                max=Max("final_price_sar"),
            )
            popular = list(
                cars.values("make")
                .annotate(count=Count("id"))
                .order_by("-count")[:5]
                .values_list("make", flat=True)
            )

            results.append(
                {
                    "code": country.code,
                    "name_en": country.name_en,
                    "name_ar": country.name_ar,
                    "iso_code": country.iso_code,
                    "flag_emoji": country.flag_emoji,
                    "latitude": country.latitude,
                    "longitude": country.longitude,
                    "total_cars": total,
                    "available_cars": available,
                    "arriving_soon_cars": arriving,
                    "reserved_cars": reserved,
                    "avg_price_sar": price_agg["avg"],
                    "min_price_sar": price_agg["min"],
                    "max_price_sar": price_agg["max"],
                    "popular_makes": popular,
                    "avg_shipping_days": country.avg_shipping_days,
                    "avg_shipping_cost_sar": country.avg_shipping_cost_sar,
                }
            )
            total_globally += total

        # Sort by most cars first
        results.sort(key=lambda r: (-r["total_cars"], r["code"]))

        payload = {
            "countries": CountryAggregateSerializer(results, many=True).data,
            "total_countries": len(results),
            "total_cars_globally": total_globally,
            "last_updated": timezone.now().isoformat(),
        }

        cache.set(CACHE_KEY_BY_COUNTRY, payload, CACHE_TTL)
        return Response(payload)

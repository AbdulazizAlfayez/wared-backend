from django.db.models import Count, Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from .models import City, Region
from .serializers import CitySerializer, RegionDetailSerializer, RegionSerializer


@extend_schema(tags=['Locations'])
class RegionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/regions/       — list all 13 regions with city count
    GET /api/regions/{id}/  — region detail with nested cities
    """
    permission_classes = [AllowAny]
    lookup_field = 'pk'
    pagination_class = None  # 13 regions — always return all

    def get_queryset(self):
        if self.action == 'list':
            return Region.objects.annotate(cities_count=Count('cities')).order_by('name_en')
        return Region.objects.prefetch_related('cities').order_by('name_en')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return RegionDetailSerializer
        return RegionSerializer


@extend_schema(tags=['Locations'])
class CityViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/cities/              — all cities
    GET /api/cities/?region={id} — filter by region FK
    GET /api/cities/?search=riy  — search by English or Arabic name
    """
    permission_classes = [AllowAny]
    serializer_class   = CitySerializer
    # 46 Saudi cities — no pagination, so selectors always get the full list
    # (page_size query param was silently ignored, hiding cities after #20)
    pagination_class   = None

    def get_queryset(self):
        qs = City.objects.select_related('region').order_by('name_en')

        region_param = self.request.query_params.get('region')
        if region_param:
            qs = qs.filter(region_id=region_param)

        search = self.request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(name_en__icontains=search) | Q(name_ar__icontains=search)
            )

        return qs

    @action(detail=True, methods=['get'], url_path='center')
    def center(self, request, pk=None):
        """
        GET /api/cities/{id}/center/
        Returns the city's coordinates as a default map center point,
        plus an approximate bounding box viewport for that city.
        """
        city = self.get_object()
        if city.latitude is None or city.longitude is None:
            return Response({'error': 'No coordinates available for this city.'}, status=404)

        lat = float(city.latitude)
        lng = float(city.longitude)
        # Approximate city viewport: ±0.3° lat (~33 km) and ±0.4° lng
        delta_lat = 0.3
        delta_lng = 0.4
        return Response({
            'city_id':   city.pk,
            'name_en':   city.name_en,
            'name_ar':   city.name_ar,
            'latitude':  lat,
            'longitude': lng,
            'viewport': {
                'lat_min': round(lat - delta_lat, 6),
                'lat_max': round(lat + delta_lat, 6),
                'lng_min': round(lng - delta_lng, 6),
                'lng_max': round(lng + delta_lng, 6),
            },
        })

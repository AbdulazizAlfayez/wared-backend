import django_filters
from django.db import models
from .models import Car, Listing, Showroom, Workshop


class CarFilter(django_filters.FilterSet):
    """Filter for Car listings."""
    
    make = django_filters.CharFilter(field_name='make', lookup_expr='icontains')
    model = django_filters.CharFilter(field_name='model', lookup_expr='icontains')
    min_price = django_filters.NumberFilter(field_name='price', lookup_expr='gte')
    max_price = django_filters.NumberFilter(field_name='price', lookup_expr='lte')
    min_year = django_filters.NumberFilter(field_name='year', lookup_expr='gte')
    max_year = django_filters.NumberFilter(field_name='year', lookup_expr='lte')
    fuel_type = django_filters.ChoiceFilter(choices=Car.FUEL_TYPE_CHOICES)
    transmission = django_filters.ChoiceFilter(choices=Car.TRANSMISSION_CHOICES)
    condition = django_filters.ChoiceFilter(choices=Car.CONDITION_CHOICES)
    location = django_filters.CharFilter(field_name='location', lookup_expr='icontains')
    status = django_filters.ChoiceFilter(choices=Car.STATUS_CHOICES)
    search = django_filters.CharFilter(method='search_filter')
    
    class Meta:
        model = Car
        fields = ['make', 'model', 'fuel_type', 'transmission', 'condition', 'status']
    
    def search_filter(self, queryset, name, value):
        """Search across title, description, make, and model."""
        return queryset.filter(
            models.Q(title__icontains=value) |
            models.Q(description__icontains=value) |
            models.Q(make__icontains=value) |
            models.Q(model__icontains=value)
        )


class ListingFilter(django_filters.FilterSet):
    """Filtering for Listing API. Admin can use is_active=false to see soft-deleted."""

    # Existing range / icontains filters
    make  = django_filters.CharFilter(field_name='make',  lookup_expr='icontains')
    model = django_filters.CharFilter(field_name='model', lookup_expr='icontains')
    city  = django_filters.CharFilter(field_name='city',  lookup_expr='icontains')
    color = django_filters.CharFilter(field_name='color', lookup_expr='icontains')

    year_min  = django_filters.NumberFilter(field_name='year',    lookup_expr='gte')
    year_max  = django_filters.NumberFilter(field_name='year',    lookup_expr='lte')
    price_min = django_filters.NumberFilter(field_name='price',   lookup_expr='gte')
    price_max = django_filters.NumberFilter(field_name='price',   lookup_expr='lte')

    # Spec range filters
    engine_size_min = django_filters.NumberFilter(field_name='engine_size', lookup_expr='gte')
    engine_size_max = django_filters.NumberFilter(field_name='engine_size', lookup_expr='lte')
    horsepower_min  = django_filters.NumberFilter(field_name='horsepower',  lookup_expr='gte')
    horsepower_max  = django_filters.NumberFilter(field_name='horsepower',  lookup_expr='lte')
    cylinders_min   = django_filters.NumberFilter(field_name='cylinders',   lookup_expr='gte')
    cylinders_max   = django_filters.NumberFilter(field_name='cylinders',   lookup_expr='lte')
    seats_min       = django_filters.NumberFilter(field_name='seats',       lookup_expr='gte')
    seats_max       = django_filters.NumberFilter(field_name='seats',       lookup_expr='lte')
    doors_min       = django_filters.NumberFilter(field_name='doors',       lookup_expr='gte')
    doors_max       = django_filters.NumberFilter(field_name='doors',       lookup_expr='lte')

    # Choice filters
    status       = django_filters.ChoiceFilter(choices=Listing.STATUS_CHOICES)
    body_type    = django_filters.ChoiceFilter(choices=Listing.BODY_TYPE_CHOICES)
    drive_type   = django_filters.ChoiceFilter(choices=Listing.DRIVE_TYPE_CHOICES)
    fuel_type    = django_filters.ChoiceFilter(choices=Listing.FUEL_TYPE_CHOICES)
    transmission = django_filters.ChoiceFilter(choices=Listing.TRANSMISSION_CHOICES)
    condition    = django_filters.ChoiceFilter(choices=Listing.CONDITION_CHOICES)
    imported_from = django_filters.ChoiceFilter(choices=Listing.IMPORT_SOURCE_CHOICES)

    # Exact numeric filters (year, mileage, seats, doors)
    year      = django_filters.NumberFilter(field_name='year',    lookup_expr='exact')
    mileage   = django_filters.NumberFilter(field_name='mileage', lookup_expr='exact')
    mileage_min = django_filters.NumberFilter(field_name='mileage', lookup_expr='gte')
    mileage_max = django_filters.NumberFilter(field_name='mileage', lookup_expr='lte')
    seats     = django_filters.NumberFilter(field_name='seats',   lookup_expr='exact')
    doors     = django_filters.NumberFilter(field_name='doors',   lookup_expr='exact')

    # Additional text filters
    color_interior = django_filters.CharFilter(field_name='color_interior', lookup_expr='icontains')

    # Boolean filters
    is_active          = django_filters.BooleanFilter(field_name='is_active')
    customs_cleared    = django_filters.BooleanFilter(field_name='customs_cleared')
    accident_history   = django_filters.BooleanFilter(field_name='accident_history')
    warranty_remaining = django_filters.BooleanFilter(field_name='warranty_remaining')
    service_history    = django_filters.BooleanFilter(field_name='service_history')
    negotiable         = django_filters.BooleanFilter(field_name='negotiable')

    # Phase 4.6 — Promotion filters
    is_featured    = django_filters.BooleanFilter(field_name='is_featured')
    is_highlighted = django_filters.BooleanFilter(field_name='is_highlighted')
    is_top_search  = django_filters.BooleanFilter(field_name='is_top_search')
    is_homepage    = django_filters.BooleanFilter(field_name='is_homepage')

    # Location FK filters
    city_obj        = django_filters.NumberFilter(field_name='city_obj',        lookup_expr='exact')
    city_obj__region = django_filters.NumberFilter(field_name='city_obj__region', lookup_expr='exact')

    # Viewport (map bounds) filters — Phase 2.15
    # NULL lat/lng rows are automatically excluded because NULL comparisons are FALSE in SQL.
    lat_min = django_filters.NumberFilter(field_name='latitude',  lookup_expr='gte')
    lat_max = django_filters.NumberFilter(field_name='latitude',  lookup_expr='lte')
    lng_min = django_filters.NumberFilter(field_name='longitude', lookup_expr='gte')
    lng_max = django_filters.NumberFilter(field_name='longitude', lookup_expr='lte')

    # Import-specific filters
    source_country    = django_filters.CharFilter(field_name='source_country', lookup_expr='exact')
    import_status     = django_filters.CharFilter(field_name='import_status',  lookup_expr='exact')
    spec_origin       = django_filters.CharFilter(field_name='spec_origin',    lookup_expr='exact')
    port_of_entry     = django_filters.CharFilter(field_name='port_of_entry',  lookup_expr='exact')
    auction_source    = django_filters.CharFilter(field_name='auction_source', lookup_expr='exact')
    gcc_specs         = django_filters.BooleanFilter(field_name='gcc_specs')
    has_salvage_title = django_filters.BooleanFilter(field_name='has_salvage_title')
    has_flood_damage  = django_filters.BooleanFilter(field_name='has_flood_damage')
    has_frame_damage  = django_filters.BooleanFilter(field_name='has_frame_damage')
    final_price_sar__gte = django_filters.NumberFilter(field_name='final_price_sar', lookup_expr='gte')
    final_price_sar__lte = django_filters.NumberFilter(field_name='final_price_sar', lookup_expr='lte')
    arriving_before   = django_filters.DateFilter(field_name='estimated_arrival_date', lookup_expr='lte')
    arriving_after    = django_filters.DateFilter(field_name='estimated_arrival_date', lookup_expr='gte')

    class Meta:
        model  = Listing
        fields = [
            'make', 'model', 'city', 'color', 'color_interior',
            'year', 'year_min', 'year_max',
            'price_min', 'price_max',
            'mileage', 'mileage_min', 'mileage_max',
            'engine_size_min', 'engine_size_max',
            'horsepower_min', 'horsepower_max',
            'cylinders_min', 'cylinders_max',
            'seats', 'seats_min', 'seats_max',
            'doors', 'doors_min', 'doors_max',
            'status', 'body_type', 'drive_type', 'fuel_type',
            'transmission', 'condition', 'imported_from',
            'is_active', 'customs_cleared', 'accident_history',
            'warranty_remaining', 'service_history', 'negotiable',
            'city_obj', 'city_obj__region',
            'lat_min', 'lat_max', 'lng_min', 'lng_max',
            'is_featured', 'is_highlighted', 'is_top_search', 'is_homepage',
            # Import-specific
            'source_country', 'import_status', 'spec_origin', 'port_of_entry',
            'auction_source', 'gcc_specs', 'has_salvage_title', 'has_flood_damage',
            'has_frame_damage', 'final_price_sar__gte', 'final_price_sar__lte',
            'arriving_before', 'arriving_after',
        ]


# ---------------------------------------------------------------------------
# Phase 2.15 — Showroom & Workshop viewport filters
# ---------------------------------------------------------------------------

class ShowroomFilter(django_filters.FilterSet):
    city        = django_filters.CharFilter(field_name='city',        lookup_expr='icontains')
    name        = django_filters.CharFilter(field_name='name',        lookup_expr='icontains')
    verified    = django_filters.BooleanFilter(field_name='verified')
    is_verified = django_filters.BooleanFilter(field_name='is_verified')
    is_active   = django_filters.BooleanFilter(field_name='is_active')

    # Location FK filters (Phase 4.1)
    city_obj        = django_filters.NumberFilter(field_name='city_obj',         lookup_expr='exact')
    city_obj__region = django_filters.NumberFilter(field_name='city_obj__region', lookup_expr='exact')

    # JSON specializations filter — PostgreSQL __contains
    specializations = django_filters.CharFilter(method='filter_specializations')

    lat_min = django_filters.NumberFilter(field_name='latitude',  lookup_expr='gte')
    lat_max = django_filters.NumberFilter(field_name='latitude',  lookup_expr='lte')
    lng_min = django_filters.NumberFilter(field_name='longitude', lookup_expr='gte')
    lng_max = django_filters.NumberFilter(field_name='longitude', lookup_expr='lte')

    class Meta:
        model  = Showroom
        fields = [
            'city', 'name', 'verified', 'is_verified', 'is_active',
            'city_obj', 'city_obj__region', 'specializations',
            'lat_min', 'lat_max', 'lng_min', 'lng_max',
        ]

    def filter_specializations(self, queryset, name, value):
        """Filter showrooms whose specializations JSON array contains *value*."""
        return queryset.filter(specializations__contains=[value])


class WorkshopFilter(django_filters.FilterSet):
    city        = django_filters.CharFilter(field_name='city',        lookup_expr='icontains')
    name        = django_filters.CharFilter(field_name='name',        lookup_expr='icontains')
    is_verified = django_filters.BooleanFilter(field_name='is_verified')
    is_active   = django_filters.BooleanFilter(field_name='is_active')

    # Location FK filters
    city_obj         = django_filters.NumberFilter(field_name='city_obj',          lookup_expr='exact')
    city_obj__region = django_filters.NumberFilter(field_name='city_obj__region',  lookup_expr='exact')

    lat_min = django_filters.NumberFilter(field_name='latitude',  lookup_expr='gte')
    lat_max = django_filters.NumberFilter(field_name='latitude',  lookup_expr='lte')
    lng_min = django_filters.NumberFilter(field_name='longitude', lookup_expr='gte')
    lng_max = django_filters.NumberFilter(field_name='longitude', lookup_expr='lte')

    class Meta:
        model  = Workshop
        fields = [
            'city', 'name', 'is_verified', 'is_active',
            'city_obj', 'city_obj__region',
            'lat_min', 'lat_max', 'lng_min', 'lng_max',
        ]


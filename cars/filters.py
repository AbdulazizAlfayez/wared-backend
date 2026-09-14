import django_filters
from django.db import models
from django.db.models import Q

from .models import Car, Listing, Showroom, Workshop


def split_csv(value):
    """`"a, b ,c"` -> `['a', 'b', 'c']`, dropping blanks."""
    if value in (None, ''):
        return []
    return [part.strip() for part in str(value).split(',') if part.strip()]


class MultiValueIContainsFilter(django_filters.CharFilter):
    """
    Comma-separated values, ORed, each matched as a case-insensitive substring.

    A single value produces exactly the query the previous `icontains` filter
    did, so `make=Toyota` is unchanged — including partial matches like
    `make=Toy`, which an `iexact` rewrite would silently have broken.
    `make=Toyota,Nissan` is Toyota OR Nissan.
    """

    def filter(self, qs, value):
        values = split_csv(value)
        if not values:
            return qs
        query = Q()
        for item in values:
            query |= Q(**{f'{self.field_name}__icontains': item})
        return qs.filter(query)


class MultiValueChoiceFilter(django_filters.CharFilter):
    """
    Comma-separated enum values, ORed through a single `__in`.

    Values are lower-cased first. Every `Listing` choice constant is lower-case,
    so this is case-insensitive in practice while still using the column's
    index — which `iexact` would defeat, and there are indexes on `body_type`,
    `fuel_type`, `transmission`, `drive_type` and `imported_from`.

    Deliberately a `CharFilter` rather than a `ChoiceFilter`: a choice field
    validates the whole parameter against its choices, so a comma-joined string
    is rejected outright. An unrecognised value now simply matches nothing,
    which is the right answer for an OR list.
    """

    def filter(self, qs, value):
        values = [item.lower() for item in split_csv(value)]
        if not values:
            return qs
        return qs.filter(**{f'{self.field_name}__in': values})


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

    # Categorical text filters — comma-separated, ORed, case-insensitive.
    make  = MultiValueIContainsFilter(field_name='make')
    model = MultiValueIContainsFilter(field_name='model')
    city  = MultiValueIContainsFilter(field_name='city')
    color = MultiValueIContainsFilter(field_name='color')

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

    # Choice filters — comma-separated, ORed.
    status       = MultiValueChoiceFilter(field_name='status')
    body_type    = MultiValueChoiceFilter(field_name='body_type')
    drive_type   = MultiValueChoiceFilter(field_name='drive_type')
    fuel_type    = MultiValueChoiceFilter(field_name='fuel_type')
    transmission = MultiValueChoiceFilter(field_name='transmission')
    condition    = MultiValueChoiceFilter(field_name='condition')
    imported_from = MultiValueChoiceFilter(field_name='imported_from')

    # Exact numeric filters (year, mileage, seats, doors)
    year      = django_filters.NumberFilter(field_name='year',    lookup_expr='exact')
    mileage   = django_filters.NumberFilter(field_name='mileage', lookup_expr='exact')
    mileage_min = django_filters.NumberFilter(field_name='mileage', lookup_expr='gte')
    mileage_max = django_filters.NumberFilter(field_name='mileage', lookup_expr='lte')
    seats     = django_filters.NumberFilter(field_name='seats',   lookup_expr='exact')
    doors     = django_filters.NumberFilter(field_name='doors',   lookup_expr='exact')

    # Additional text filters
    color_interior = MultiValueIContainsFilter(field_name='color_interior')

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
    source_country    = MultiValueChoiceFilter(field_name='source_country')
    import_status     = MultiValueChoiceFilter(field_name='import_status')
    spec_origin       = MultiValueChoiceFilter(field_name='spec_origin')
    port_of_entry     = MultiValueChoiceFilter(field_name='port_of_entry')
    auction_source    = MultiValueChoiceFilter(field_name='auction_source')
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


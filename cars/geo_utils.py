"""
Geo utilities for Phase 2.15 — Map Coordinates & Bounds Filtering.

Provides:
  haversine(lat1, lng1, lat2, lng2) -> float (km)
  bounding_box(lat, lng, radius_km) -> dict
  haversine_annotation(lat, lng)    -> Django ORM ExpressionWrapper
"""

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0


def haversine(lat1, lng1, lat2, lng2) -> float:
    """Return great-circle distance in km between two WGS-84 points."""
    lat1, lng1, lat2, lng2 = (
        radians(float(lat1)),
        radians(float(lng1)),
        radians(float(lat2)),
        radians(float(lng2)),
    )
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2.0 * EARTH_RADIUS_KM * asin(sqrt(a))


def bounding_box(lat, lng, radius_km) -> dict:
    """
    Return a rough lat/lng bounding box for a circle of *radius_km* around
    (*lat*, *lng*).  Used as a cheap pre-filter before exact Haversine.
    """
    lat_delta = radius_km / 111.0
    # longitude degrees shrink towards the poles
    lng_delta = radius_km / (111.0 * cos(radians(float(lat))))
    return {
        'lat_min': float(lat) - lat_delta,
        'lat_max': float(lat) + lat_delta,
        'lng_min': float(lng) - lng_delta,
        'lng_max': float(lng) + lng_delta,
    }


def haversine_annotation(lat, lng):
    """
    Return a Django ORM ExpressionWrapper that computes Haversine distance
    (in km) from (*lat*, *lng*) to the row's latitude/longitude columns.

    The queryset must have non-null latitude and longitude for this to work —
    always pre-filter with bounding_box() first.

    Formula:
        d = R * acos(cos(φ1)·cos(φ2)·cos(λ2-λ1) + sin(φ1)·sin(φ2))
    """
    from django.db.models import ExpressionWrapper, F, FloatField, Value
    from django.db.models.functions import ACos, Cos, Radians, Sin

    lat_val = Value(float(lat))
    lng_val = Value(float(lng))

    return ExpressionWrapper(
        Value(EARTH_RADIUS_KM) * ACos(
            Cos(Radians(lat_val)) * Cos(Radians(F('latitude')))
            * Cos(Radians(F('longitude')) - Radians(lng_val))
            + Sin(Radians(lat_val)) * Sin(Radians(F('latitude')))
        ),
        output_field=FloatField(),
    )

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ImportedCarsByCountryView,
    SourceCountryAdminViewSet,
    SourceCountryViewSet,
)

public_router = DefaultRouter()
public_router.register("source-countries", SourceCountryViewSet, basename="source-country")

admin_router = DefaultRouter()
admin_router.register("source-countries", SourceCountryAdminViewSet, basename="admin-source-country")

urlpatterns = [
    path("api/", include(public_router.urls)),
    path("api/admin/", include(admin_router.urls)),
    path(
        "api/imported-cars/by-country/",
        ImportedCarsByCountryView.as_view(),
        name="imported-cars-by-country",
    ),
]

"""Routed at /api/admin/inspect/ from car_marketplace/urls.py."""
from django.urls import path

from .views import InspectView

urlpatterns = [
    path('<str:entity>/<int:pk>/', InspectView.as_view(), name='admin-inspect'),
]

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import AppointmentViewSet, ServiceBookingViewSet

router = DefaultRouter()
router.register(r'appointments',     AppointmentViewSet,    basename='appointment')
router.register(r'service-bookings', ServiceBookingViewSet, basename='service-booking')

urlpatterns = [
    path('', include(router.urls)),
]

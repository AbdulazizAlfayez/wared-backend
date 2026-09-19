from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import DeviceView, NotificationViewSet

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')

urlpatterns = [
    path('', include(router.urls)),
    # Push registration. Keyed on the token, so POST is an upsert.
    path('devices/', DeviceView.as_view(), name='devices'),
]

"""
URL configuration for car_marketplace project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from datetime import datetime
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('accounts.urls')),
    path('api/', include('cars.urls')),
    path('api/dashboard/', include('dashboard.urls')),
    path('api/', include('auditlog.urls')),
    path('api/favorites/', include('favorites.urls')),
    path('api/', include('leads.urls')),
    path('api/', include('locations.urls')),
    path('api/', include('bookings.urls')),
    path('api/', include('notifications.urls')),
    path('api/', include('messaging.urls')),
    path('api/', include('dealer_applications.urls')),
    # DEPRECATED: Subscription system disabled — WARED uses commission-only model
    # path('api/', include('subscriptions.urls')),
    path('api/', include('moderation.urls')),
    path('api/', include('reviews.urls')),
    path('api/', include('fraud.urls')),
    path('api/', include('orders.urls')),
    path('api/', include('importers.urls')),
    path('api/', include('calculator.urls')),
    path('api/', include('legal_documents.urls')),
    path('api/', include('payments.urls')),
    path('api/assistant/', include('assistant.urls')),
    path('api/', include('appconfig.urls')),
    path('', include('source_countries.urls')),
    path('health/', lambda request: JsonResponse({
        'status': 'OK',
        'timestamp': datetime.now().isoformat()
    })),
    # API documentation
    path('api/schema/',  SpectacularAPIView.as_view(),                          name='schema'),
    path('api/docs/',    SpectacularSwaggerView.as_view(url_name='schema'),     name='swagger-ui'),
    path('api/redoc/',   SpectacularRedocView.as_view(url_name='schema'),       name='redoc'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    # Sentry test endpoint — triggers a ZeroDivisionError to verify capture
    def sentry_debug(request):
        _ = 1 / 0
    urlpatterns += [path('api/_sentry-debug/', sentry_debug, name='sentry_debug')]


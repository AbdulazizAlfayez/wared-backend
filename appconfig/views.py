from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from .models import AppConfig


@extend_schema(tags=['Config'])
class ConfigView(APIView):
    """GET /api/config/ — public app configuration."""
    permission_classes = [AllowAny]

    def get(self, request):
        config = AppConfig.load()
        return Response({
            'min_supported_version': config.min_supported_version,
            'latest_version': config.latest_version,
            'maintenance_mode': config.maintenance_mode,
            'maintenance_message': config.maintenance_message,
            'features': config.features,
        })

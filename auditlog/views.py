import django_filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import permissions, viewsets
from rest_framework.filters import OrderingFilter
from drf_spectacular.utils import extend_schema

from .models import AuditLog
from .serializers import AuditLogSerializer


class IsAdminRole(permissions.BasePermission):
    """Allow access only to users with role='admin'."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, 'role', None) == 'admin'
        )


class AuditLogFilter(django_filters.FilterSet):
    timestamp__gte = django_filters.IsoDateTimeFilter(field_name='timestamp', lookup_expr='gte')
    timestamp__lte = django_filters.IsoDateTimeFilter(field_name='timestamp', lookup_expr='lte')

    class Meta:
        model = AuditLog
        fields = ['action', 'model_name', 'user']


@extend_schema(tags=['Audit Log'])
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only audit log endpoint. Admin access only.

    Filterable by:
        ?action=create|update|delete|approve|reject|status_change
        ?model_name=Listing|Car|...
        ?user=<user_id>
        ?timestamp__gte=2024-01-01T00:00:00Z
        ?timestamp__lte=2024-12-31T23:59:59Z
    """

    queryset = AuditLog.objects.select_related('user').all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAdminRole]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = AuditLogFilter
    ordering_fields = ['timestamp']
    ordering = ['-timestamp']

import logging
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from auditlog.utils import get_client_ip, log_action
from .models import FraudFlag, IPLog, ListingLimit
from .serializers import (
    FraudFlagResolveSerializer,
    FraudFlagSerializer,
    IPLogSerializer,
)

logger = logging.getLogger(__name__)


class IsAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and
            (request.user.is_staff or getattr(request.user, 'role', '') == 'admin')
        )


class AdminFraudListView(ListAPIView):
    """GET /api/admin/fraud/ — list all fraud flags."""
    permission_classes = [IsAdminRole]
    serializer_class   = FraudFlagSerializer

    def get_queryset(self):
        qs = FraudFlag.objects.select_related('listing', 'user', 'resolved_by')
        p  = self.request.query_params
        if ft := p.get('flag_type'):
            qs = qs.filter(flag_type=ft)
        if sv := p.get('severity'):
            qs = qs.filter(severity=sv)
        if ir := p.get('is_resolved'):
            qs = qs.filter(is_resolved=(ir.lower() == 'true'))
        if df := p.get('date_from'):
            qs = qs.filter(created_at__date__gte=df)
        if dt := p.get('date_to'):
            qs = qs.filter(created_at__date__lte=dt)
        return qs.order_by('-created_at')


class AdminFraudDetailView(APIView):
    """GET/PATCH /api/admin/fraud/{id}/"""
    permission_classes = [IsAdminRole]

    def _get(self, pk):
        try:
            return FraudFlag.objects.select_related('listing', 'user', 'resolved_by').get(pk=pk)
        except FraudFlag.DoesNotExist:
            return None

    def get(self, request, pk):
        flag = self._get(pk)
        if not flag:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(FraudFlagSerializer(flag).data)

    def patch(self, request, pk):
        flag = self._get(pk)
        if not flag:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = FraudFlagResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        flag.is_resolved      = True
        flag.resolved_by      = request.user
        flag.resolved_at      = timezone.now()
        flag.resolution_notes = serializer.validated_data.get('resolution_notes', '')
        flag.save(update_fields=['is_resolved', 'resolved_by', 'resolved_at', 'resolution_notes'])

        log_action(
            user=request.user, action='update', model_name='FraudFlag',
            object_id=flag.id, new_value={'is_resolved': True},
            ip_address=get_client_ip(request),
        )
        return Response(FraudFlagSerializer(flag).data)


class AdminFraudStatsView(APIView):
    """GET /api/admin/fraud/stats/"""
    permission_classes = [IsAdminRole]

    def get(self, request):
        now   = timezone.now()
        week  = now - timedelta(days=7)
        month = now - timedelta(days=30)

        by_type = dict(
            FraudFlag.objects.values('flag_type').annotate(c=Count('id')).values_list('flag_type', 'c')
        )
        by_severity = dict(
            FraudFlag.objects.values('severity').annotate(c=Count('id')).values_list('severity', 'c')
        )

        return Response({
            'total':        FraudFlag.objects.count(),
            'unresolved':   FraudFlag.objects.filter(is_resolved=False).count(),
            'critical':     FraudFlag.objects.filter(severity='critical', is_resolved=False).count(),
            'this_week':    FraudFlag.objects.filter(created_at__gte=week).count(),
            'this_month':   FraudFlag.objects.filter(created_at__gte=month).count(),
            'by_type':      by_type,
            'by_severity':  by_severity,
        })


class AdminIPReportView(APIView):
    """GET /api/admin/fraud/ip-report/ — suspicious IPs."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        # IPs with 3+ distinct users
        suspicious = (
            IPLog.objects
            .values('ip_address')
            .annotate(
                user_count=Count('user', distinct=True),
                action_count=Count('id'),
            )
            .filter(user_count__gte=3)
            .order_by('-user_count')[:50]
        )
        return Response(list(suspicious))


class AdminFraudScanView(APIView):
    """POST /api/admin/fraud/scan/ — manually trigger fraud scan."""
    permission_classes = [IsAdminRole]

    def post(self, request):
        from .tasks import (
            check_banned_keywords, check_duplicate_vin,
            check_rapid_posting, check_suspicious_pricing,
        )
        listing_id = request.data.get('listing_id')
        user_id    = request.data.get('user_id')

        if listing_id:
            check_duplicate_vin.delay(listing_id)
            check_suspicious_pricing.delay(listing_id)
            check_banned_keywords.delay(listing_id)

        if user_id:
            check_rapid_posting.delay(user_id)

        if not listing_id and not user_id:
            return Response({'detail': 'Provide listing_id or user_id.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Fraud scan triggered.', 'listing_id': listing_id, 'user_id': user_id})


class UserListingLimitView(APIView):
    """GET /api/listings/limit/ — return current user's listing limit status."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .utils import check_and_enforce_limit
        allowed, limit, used = check_and_enforce_limit(request.user)
        return Response({
            'daily_limit':    limit,
            'listings_today': used,
            'remaining':      max(0, limit - used),
            'allowed':        allowed,
        })

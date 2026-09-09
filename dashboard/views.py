"""Dashboard API: importer and admin stats."""
from datetime import timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F
from django.utils import timezone
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema

from cars.models import Listing

User = get_user_model()


@extend_schema(tags=['Dashboard'])
class DealerDashboardView(APIView):
    """GET /api/dashboard/dealer/ — only role=importer; counts own listings by status."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if getattr(request.user, 'role', None) != 'importer':
            return Response(
                {'error': 'Only importers can access this dashboard.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = Listing.objects.filter(owner=request.user, is_active=True)
        return Response({
            'total_listings': qs.count(),
            'pending_listings': qs.filter(status='pending').count(),
            'approved_listings': qs.filter(status='approved').count(),
            'rejected_listings': qs.filter(status='rejected').count(),
            'sold_listings': qs.filter(status='sold').count(),
        })


@extend_schema(tags=['Dashboard'])
class DealerLeadStatsView(APIView):
    """GET /api/dashboard/dealer/leads/ — importer or admin; lead stats for the importer."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        role = getattr(user, 'role', None)
        if role not in ('importer', 'admin'):
            return Response(
                {'error': 'Only importers or admins can access lead stats.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Import here to avoid circular imports at module load time
        from leads.models import Lead

        qs = Lead.objects.filter(dealer=user)

        now         = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start  = today_start - timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)

        # Average response time: for leads currently at 'contacted', use
        # updated_at - created_at as a proxy for time-to-first-contact.
        avg_duration = qs.filter(status='contacted').annotate(
            duration=ExpressionWrapper(
                F('updated_at') - F('created_at'),
                output_field=DurationField(),
            )
        ).aggregate(avg=Avg('duration'))['avg']

        avg_hours = round(avg_duration.total_seconds() / 3600, 2) if avg_duration else None

        top_listings = list(
            qs.values('listing_id', 'listing__title')
            .annotate(lead_count=Count('id'))
            .order_by('-lead_count')[:5]
        )

        return Response({
            'total_leads':        qs.count(),
            'new_leads':          qs.filter(status='new').count(),
            'contacted_leads':    qs.filter(status='contacted').count(),
            'closed_leads':       qs.filter(status='closed').count(),
            'spam_leads':         qs.filter(status='spam').count(),
            'leads_today':        qs.filter(created_at__gte=today_start).count(),
            'leads_this_week':    qs.filter(created_at__gte=week_start).count(),
            'leads_this_month':   qs.filter(created_at__gte=month_start).count(),
            'avg_response_time_hours': avg_hours,
            'top_listings': [
                {
                    'listing_id': row['listing_id'],
                    'title':      row['listing__title'],
                    'lead_count': row['lead_count'],
                }
                for row in top_listings
            ],
        })


@extend_schema(tags=['Dashboard'])
class AdminDashboardView(APIView):
    """GET /api/dashboard/admin/ — only role=admin; system-wide counts."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if getattr(request.user, 'role', None) != 'admin':
            return Response(
                {'error': 'Only admins can access this dashboard.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        from cars.models import Showroom, Workshop
        from dealer_applications.models import ImporterApplication

        listings = Listing.objects.all()

        # Live order/payment counts so the admin page never shows blanks
        active_orders = 0
        pending_payments = 0
        try:
            from orders.models import ImportOrder
            active_orders = ImportOrder.objects.exclude(
                status__in=('completed', 'cancelled', 'refunded'),
            ).count()
        except Exception:
            pass
        try:
            from payments.models import PaymentTransaction
            pending_payments = PaymentTransaction.objects.filter(
                payment_type='balance', status='pending',
            ).count()
        except Exception:
            pass

        return Response({
            'total_users': User.objects.count(),
            'total_importers': User.objects.filter(role='importer').count(),
            'total_listings': listings.count(),
            'pending_listings': listings.filter(status='pending').count(),
            'approved_listings': listings.filter(status='approved').count(),
            'rejected_listings': listings.filter(status='rejected').count(),
            'sold_listings': listings.filter(status='sold').count(),
            'total_workshops': Workshop.objects.count(),
            'total_showrooms': Showroom.objects.filter(is_active=True).count(),
            'pending_applications': ImporterApplication.objects.filter(status='pending').count(),
            'active_orders': active_orders,
            'pending_payments': pending_payments,
        })


# ---------------------------------------------------------------------------
# Phase 4.4 — Dealer Analytics views
# ---------------------------------------------------------------------------

@extend_schema(tags=['Dashboard'])
class DealerAnalyticsOverviewView(APIView):
    """GET /api/dashboard/dealer/analytics/ — dealer or admin; cached 5 min."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if getattr(user, 'role', None) not in ('importer', 'admin'):
            return Response(
                {'error': 'Only dealers or admins can access analytics.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        from .analytics import get_dealer_analytics_overview
        return Response(get_dealer_analytics_overview(user))


@extend_schema(tags=['Dashboard'])
class DealerListingAnalyticsView(APIView):
    """
    GET /api/dashboard/dealer/analytics/listings/
    Paginated per-listing analytics, filterable + sortable.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if getattr(user, 'role', None) not in ('importer', 'admin'):
            return Response(
                {'error': 'Only dealers or admins can access analytics.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from .analytics import get_dealer_listing_analytics, serialize_listing_analytics
        from rest_framework.pagination import PageNumberPagination

        ordering       = request.query_params.get('ordering', '-view_count')
        status_filter  = request.query_params.get('status')
        created_after  = request.query_params.get('created_after')
        created_before = request.query_params.get('created_before')

        qs = get_dealer_listing_analytics(
            user,
            ordering=ordering,
            status_filter=status_filter,
            created_after=created_after,
            created_before=created_before,
        )

        paginator = PageNumberPagination()
        paginator.page_size = request.query_params.get('page_size', 20)
        page = paginator.paginate_queryset(qs, request)
        data = [serialize_listing_analytics(listing) for listing in page]
        return paginator.get_paginated_response(data)


@extend_schema(tags=['Dashboard'])
class SingleListingAnalyticsView(APIView):
    """GET /api/dashboard/dealer/analytics/listings/{id}/ — deep per-listing analytics."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        user = request.user
        if getattr(user, 'role', None) not in ('importer', 'admin'):
            return Response(
                {'error': 'Only dealers or admins can access analytics.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from cars.models import Listing
        from .analytics import get_single_listing_analytics

        try:
            listing = Listing.objects.get(pk=pk, is_active=True)
        except Listing.DoesNotExist:
            return Response({'error': 'Listing not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Dealers may only view their own listings.
        if getattr(user, 'role', None) == 'importer' and listing.owner_id != user.pk:
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        return Response(get_single_listing_analytics(listing))


@extend_schema(tags=['Dashboard'])
class DealerComparativeAnalyticsView(APIView):
    """GET /api/dashboard/dealer/analytics/compare/ — dealer vs platform."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if getattr(user, 'role', None) not in ('importer', 'admin'):
            return Response(
                {'error': 'Only dealers or admins can access analytics.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        from .analytics import get_comparative_analytics
        return Response(get_comparative_analytics(user))


@extend_schema(tags=['Dashboard'])
class DealerAnalyticsExportView(APIView):
    """
    GET /api/dashboard/dealer/analytics/export/?format=csv|json
    Streams per-listing analytics as CSV (default) or JSON.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        import csv
        import json as _json
        from django.http import StreamingHttpResponse, HttpResponse

        user = request.user
        if getattr(user, 'role', None) not in ('importer', 'admin'):
            return Response(
                {'error': 'Only dealers or admins can access analytics.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Use 'export_format' to avoid DRF content-negotiation on ?format=
        fmt = request.query_params.get('export_format', 'csv').lower()
        from .analytics import get_dealer_listing_analytics, serialize_listing_analytics

        qs   = get_dealer_listing_analytics(user)
        rows = [serialize_listing_analytics(l) for l in qs]

        if fmt == 'json':
            content = _json.dumps(rows, default=str, ensure_ascii=False)
            return HttpResponse(
                content,
                content_type='application/json',
                headers={'Content-Disposition': 'attachment; filename="analytics.json"'},
            )

        # --- CSV streaming ---
        class _Echo:
            def write(self, value):
                return value

        def _generate():
            writer = csv.writer(_Echo())
            yield writer.writerow([
                'id', 'make', 'model', 'year', 'price', 'status',
                'created_at', 'days_listed',
                'view_count', 'unique_view_count',
                'lead_count', 'favorite_count', 'appointment_count', 'message_count',
                'conversion_rate', 'views_per_day',
                'views_last_7_days', 'views_last_30_days',
                'leads_last_7_days', 'leads_last_30_days',
            ])
            for row in rows:
                s = row['stats']
                t = row['trends']
                yield writer.writerow([
                    row['id'], row['make'], row['model'], row['year'],
                    row['price'], row['status'], row['created_at'],
                    s['days_listed'], s['view_count'], s['unique_view_count'],
                    s['lead_count'], s['favorite_count'], s['appointment_count'],
                    s['message_count'], s['conversion_rate'], s['views_per_day'],
                    t['views_last_7_days'], t['views_last_30_days'],
                    t['leads_last_7_days'], t['leads_last_30_days'],
                ])

        response = StreamingHttpResponse(_generate(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="analytics.csv"'
        return response


@extend_schema(tags=['Dashboard'])
class DealerTopListingsView(APIView):
    """GET /api/dashboard/dealer/analytics/top/ — top performing listings."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if getattr(user, 'role', None) not in ('importer', 'admin'):
            return Response(
                {'error': 'Only dealers or admins can access analytics.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        from .analytics import get_top_performing_listings
        return Response(get_top_performing_listings(user))


@extend_schema(tags=['Dashboard'])
class AdminPlatformAnalyticsView(APIView):
    """GET /api/dashboard/admin/analytics/ — platform-wide analytics; admin only."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if getattr(request.user, 'role', None) != 'admin':
            return Response(
                {'error': 'Only admins can access platform analytics.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        from .analytics import get_admin_platform_analytics
        return Response(get_admin_platform_analytics())

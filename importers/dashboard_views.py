from django.db.models import Avg, Count, Sum
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView


class ImporterDashboardView(APIView):
    """GET /api/dashboard/importer/ — importer overview stats."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if getattr(request.user, 'role', '') not in ('importer', 'admin'):
            from rest_framework import status as drf_status
            return Response({'detail': 'Importer access only.'}, status=drf_status.HTTP_403_FORBIDDEN)

        from cars.models import Listing
        from orders.models import ImportOrder

        user      = request.user
        now       = timezone.now()
        month_ago = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        listings     = Listing.objects.filter(owner=user, is_active=True)
        total_listed = listings.count()
        live_listings_count    = listings.filter(status='approved').count()
        pending_approval_count = listings.exclude(status__in=('approved', 'sold')).count()
        sold_count             = listings.filter(status='sold').count()

        by_status = dict(
            listings.values('import_status')
            .annotate(c=Count('id'))
            .values_list('import_status', 'c')
        )

        TERMINAL = {'completed', 'cancelled', 'refunded'}
        orders        = ImportOrder.objects.filter(importer=user)
        active_orders = orders.exclude(status__in=TERMINAL).count()

        completed_this_month = orders.filter(
            status='completed', updated_at__gte=month_ago
        ).count()

        total_revenue = orders.filter(status='completed').aggregate(
            total=Sum('total_price')
        )['total'] or 0

        revenue_this_month = orders.filter(
            status='completed', updated_at__gte=month_ago
        ).aggregate(total=Sum('total_price'))['total'] or 0

        # Average delivery days from profile field (safe fallback)
        avg_delivery = 0
        try:
            avg_delivery = int(user.importer_profile.average_delivery_days)
        except Exception:
            avg_delivery = 0

        # Rating from profile
        customer_rating = 0
        try:
            customer_rating = float(user.importer_profile.average_rating)
        except Exception:
            pass

        # Unread messages
        unread_messages = 0
        try:
            from messaging.models import Message, Conversation
            unread_messages = Message.objects.filter(
                conversation__in=Conversation.objects.filter(
                    seller=user, is_active=True
                ),
                is_read=False,
            ).exclude(sender=user).count()
        except Exception:
            pass

        # Business name
        business_name = ''
        try:
            business_name = user.importer_profile.business_name or user.importer_profile.business_name_ar or ''
        except Exception:
            pass

        # Revenue sparkline (last 12 months — mock data for now until analytics are built)
        revenue_sparkline = [0] * 12
        revenue_sparkline[-1] = float(revenue_this_month)

        # Revenue change percentage
        prev_month_start = (month_ago.replace(day=1) - timezone.timedelta(days=1)).replace(day=1)
        revenue_last_month = orders.filter(
            status='completed',
            updated_at__gte=prev_month_start,
            updated_at__lt=month_ago,
        ).aggregate(total=Sum('total_price'))['total'] or 0
        revenue_change_pct = 0
        if revenue_last_month > 0:
            revenue_change_pct = round(
                ((float(revenue_this_month) - float(revenue_last_month)) / float(revenue_last_month)) * 100
            )

        # Attention items
        attention_items = []

        # Pending reservations (highest priority)
        from orders.models import Reservation
        pending_reservations = Reservation.objects.filter(
            importer=user, status='pending_review',
        ).select_related('car', 'buyer').order_by('created_at')
        for res in pending_reservations:
            from django.utils.timesince import timesince
            attention_items.append({
                'type': 'new_reservation',
                'title': f'حجز جديد من {res.buyer.name}',
                'subtitle': f'{res.car.title if res.car else ""} · 99 ريال · قبل {timesince(res.created_at)}',
                'timestamp': res.created_at.isoformat(),
                'related_object_id': res.id,
                'related_object_type': 'reservation',
            })

        # Orders needing action
        pending_orders = orders.filter(status='pending').order_by('-created_at')[:3]
        for po in pending_orders:
            attention_items.append({
                'type': 'new_order',
                'title': f'طلب حجز جديد',
                'subtitle': f'{po.car.title if po.car else ""} · #{po.order_number}',
                'timestamp': po.created_at.isoformat(),
                'related_object_id': po.id,
            })

        # Fleet status (5-stage summary)
        fleet_status = {
            'sourcing': by_status.get('sourcing', 0),
            'preparing': by_status.get('preparing_shipment', 0) + by_status.get('purchased', 0),
            'shipping': by_status.get('shipping', 0) + by_status.get('at_port', 0),
            'customs': by_status.get('in_customs', 0) + by_status.get('customs_cleared', 0),
            'ready': by_status.get('ready_for_delivery', 0) + by_status.get('available', 0),
        }

        return Response({
            'business_name':               business_name,
            'total_cars_listed':           total_listed,
            'live_listings_count':         live_listings_count,
            'pending_approval_count':      pending_approval_count,
            'sold_count':                  sold_count,
            'cars_by_import_status':       by_status,
            'active_orders_count':         active_orders,
            'completed_orders_this_month': completed_this_month,
            'total_revenue':               float(total_revenue),
            'revenue_this_month':          float(revenue_this_month),
            'revenue_change_pct':          revenue_change_pct,
            'revenue_sparkline':           revenue_sparkline,
            'average_delivery_days':       avg_delivery,
            'average_rating':              customer_rating,
            'pending_messages':            unread_messages,
            'attention_items':             attention_items,
            'fleet_status':                fleet_status,
        })


class ImporterPipelineView(APIView):
    """GET /api/dashboard/importer/pipeline/ — Kanban board data."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if getattr(request.user, 'role', '') not in ('importer', 'admin'):
            from rest_framework import status as drf_status
            return Response({'detail': 'Importer access only.'}, status=drf_status.HTTP_403_FORBIDDEN)

        from cars.models import Listing
        from cars.serializers import ListingSerializer

        PIPELINE_STATUSES = [
            'sourcing', 'available', 'reserved', 'purchased',
            'preparing_shipment', 'shipping', 'at_port', 'in_customs',
            'customs_cleared', 'inspection', 'ready_for_delivery',
        ]

        result = {}

        # Cars still awaiting admin approval (pending / changes_requested /
        # rejected / draft) get their own first column — they must never
        # appear under "Available" or any other live pipeline stage.
        pending_qs = Listing.objects.filter(
            owner=request.user, is_active=True,
        ).exclude(status__in=('approved', 'sold')).select_related('city_obj').prefetch_related('images')
        result['pending_approval'] = ListingSerializer(
            pending_qs, many=True, context={'request': request}
        ).data

        for s in PIPELINE_STATUSES:
            qs = Listing.objects.filter(
                owner=request.user, import_status=s, is_active=True,
                status__in=('approved', 'sold'),
            ).select_related('city_obj').prefetch_related('images')
            result[s] = ListingSerializer(qs, many=True, context={'request': request}).data

        return Response(result)


class ImporterOrdersView(APIView):
    """GET /api/dashboard/importer/orders/ — orders for this importer."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if getattr(request.user, 'role', '') not in ('importer', 'admin'):
            from rest_framework import status as drf_status
            return Response({'detail': 'Importer access only.'}, status=drf_status.HTTP_403_FORBIDDEN)

        from orders.models import ImportOrder
        from orders.serializers import ImportOrderListSerializer

        qs = ImportOrder.objects.filter(importer=request.user).select_related('car', 'buyer')
        if s := request.query_params.get('status'):
            qs = qs.filter(status=s)
        return Response(ImportOrderListSerializer(qs, many=True, context={'request': request}).data)


class BuyerDashboardView(APIView):
    """GET /api/dashboard/buyer/ — buyer dashboard."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from orders.models import ImportOrder
        from orders.serializers import ImportOrderListSerializer

        TERMINAL = {'completed', 'cancelled', 'refunded'}
        all_orders    = ImportOrder.objects.filter(buyer=request.user).select_related('car')
        active_orders = all_orders.exclude(status__in=TERMINAL)
        history       = all_orders.filter(status__in=TERMINAL)

        favorites_count = 0
        try:
            from favorites.models import Favorite
            favorites_count = Favorite.objects.filter(user=request.user).count()
        except Exception:
            pass

        unread_messages = 0
        try:
            from messaging.models import Message, Conversation
            unread_messages = Message.objects.filter(
                conversation__in=Conversation.objects.filter(buyer=request.user, is_active=True),
                is_read=False,
            ).exclude(sender=request.user).count()
        except Exception:
            pass

        return Response({
            'active_orders':   ImportOrderListSerializer(active_orders, many=True, context={'request': request}).data,
            'order_history':   ImportOrderListSerializer(history,       many=True, context={'request': request}).data,
            'favorites_count': favorites_count,
            'unread_messages': unread_messages,
        })

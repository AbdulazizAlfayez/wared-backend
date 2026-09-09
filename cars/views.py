import cloudinary.uploader
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models as db_models
from django.db.models import Case, Count, F, IntegerField, Max, Min, Q, Value, When
from django.db.models.functions import ExtractHour, TruncDate
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, generics, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from auditlog.utils import get_client_ip, log_action
from drf_spectacular.utils import extend_schema
from .models import (
    Car, CarImage, Listing, ListingImage, SavedSearch, RecentlyViewed, ViewLog,
    Showroom, ShowroomWorkingHours, ShowroomBranch, ShowroomReview,
    Workshop, WorkshopWorkingHours, WorkshopService, WorkshopReview,
    BulkUpload, PromotionPackage, ListingPromotion,
)
from .serializers import (
    CarSerializer,
    CarListSerializer,
    CarImageSerializer,
    ListingSerializer,
    ListingListSerializer,
    ListingImageSerializer,
    SavedSearchSerializer,
    RecentlyViewedSerializer,
    ListingMapPinSerializer,
    NearbyListingSerializer,
    ShowroomSerializer,
    ShowroomListSerializer,
    ShowroomDetailSerializer,
    ShowroomCreateUpdateSerializer,
    ShowroomWorkingHoursSerializer,
    ShowroomBranchSerializer,
    ShowroomReviewSerializer,
    WorkshopSerializer,
    WorkshopListSerializer,
    WorkshopDetailSerializer,
    WorkshopCreateUpdateSerializer,
    WorkshopWorkingHoursSerializer,
    WorkshopServiceSerializer,
    WorkshopReviewSerializer,
    ShowroomMapPinSerializer,
    NearbyShowroomSerializer,
    WorkshopMapPinSerializer,
    NearbyWorkshopSerializer,
    BulkUploadSerializer,
    BulkUploadCreateSerializer,
    BulkStatusChangeSerializer,
    BulkDeleteSerializer,
    PromotionPackageSerializer,
    ListingPromotionSerializer,
    PromoteListingSerializer,
)
from .filters import CarFilter, ListingFilter, ShowroomFilter, WorkshopFilter
from .visibility import public_market_q, party_q, detail_queryset
from .permissions import IsOwnerOrAdmin
from .geo_utils import bounding_box, haversine_annotation


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def _car_snapshot(car):
    return {
        'title': car.title,
        'make': car.make,
        'model': car.model,
        'year': car.year,
        'price': str(car.price),
        'mileage': car.mileage,
        'status': car.status,
    }


def _listing_snapshot(listing):
    return {
        'title': listing.title,
        'make': listing.make,
        'model': listing.model,
        'year': listing.year,
        'price': str(listing.price),
        'mileage': listing.mileage,
        'status': listing.status,
        'city': listing.city,
        'vin': listing.vin,
    }


# ---------------------------------------------------------------------------
# Listing approval/rejection email helper (Celery-aware with sync fallback)
# ---------------------------------------------------------------------------

def _send_listing_email(listing_id: int, action: str, reason: str = '') -> None:
    """
    Dispatch listing email via Celery task.
    With CELERY_TASK_ALWAYS_EAGER=True (dev), .delay() executes synchronously.
    With a real Celery worker (prod), .delay() queues to Redis.
    Failure never breaks the caller.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        from notifications.tasks import (
            send_listing_approved_email,
            send_listing_rejected_email,
            send_listing_changes_requested_email,
        )
        if action == 'approved':
            send_listing_approved_email.delay(listing_id)
        elif action == 'rejected':
            send_listing_rejected_email.delay(listing_id, reason)
        elif action == 'changes_requested':
            send_listing_changes_requested_email.delay(listing_id, reason)
    except Exception as exc:
        logger.error("_send_listing_email(%s, %s) failed: %s", listing_id, action, exc, exc_info=True)


def _notify_listing_status_change(instance, old_status: str, request_user) -> None:
    """Send notification + email when a listing transitions to approved/rejected."""
    if old_status == instance.status:
        return  # No transition — don't send duplicates

    try:
        from notifications.utils import notify
    except ImportError:
        return

    if instance.status == 'approved' and old_status != 'approved':
        notify(
            recipient=instance.owner,
            notification_type='listing_approved',
            title='تم اعتماد إعلانك / Your listing has been approved',
            message=f'إعلانك "{instance.title}" معتمد الحين وظاهر للمشترين.',
            listing=instance,
        )
        _send_listing_email(instance.pk, 'approved')

    elif instance.status == 'rejected' and old_status != 'rejected':
        reason = getattr(instance, 'rejection_reason', '') or ''
        notify(
            recipient=instance.owner,
            notification_type='listing_rejected',
            title='تم رفض إعلانك / Your listing was rejected',
            message=f'إعلانك "{instance.title}" مرفوض. السبب: {reason}' if reason else f'إعلانك "{instance.title}" مرفوض.',
            listing=instance,
            metadata={'rejection_reason': reason},
        )
        _send_listing_email(instance.pk, 'rejected', reason)

    elif instance.status == 'changes_requested' and old_status != 'changes_requested':
        note = getattr(instance, 'admin_notes', '') or ''
        notify(
            recipient=instance.owner,
            notification_type='listing_changes_requested',
            title='مطلوب تعديل على إعلانك / Changes requested on your listing',
            message=f'يرجى تعديل إعلانك "{instance.title}". الملاحظة: {note}' if note else f'يرجى تعديل إعلانك "{instance.title}".',
            listing=instance,
            metadata={'admin_notes': note},
        )
        _send_listing_email(instance.pk, 'changes_requested', note)


def _notify_admins_new_listing(listing):
    """Notify admins when a listing is submitted or resubmitted for review."""
    try:
        from django.contrib.auth import get_user_model
        from notifications.utils import notify
        User = get_user_model()
        admins = User.objects.filter(role='admin', is_active=True)
        for admin in admins:
            notify(
                recipient=admin,
                notification_type='listing_submitted',
                title='إعلان جديد يحتاج مراجعة',
                message=f'{listing.owner.name} قدّم إعلان: {listing.title}',
                listing=listing,
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase 2.13 — view tracking helper
# ---------------------------------------------------------------------------

_VALID_SOURCES = {c[0] for c in ViewLog.SOURCE_CHOICES}


def _track_view(request, listing) -> None:
    """
    Record a ViewLog entry and update denormalized counters on Listing.

    Rules:
    - Owner views of their own listing are ignored.
    - At most one view per (user OR IP) per listing per calendar day.
    - unique_view_count is incremented only on the viewer's very first visit.
    - Uses F() expressions for atomic, race-condition-free counter updates.
    """
    user = request.user if request.user.is_authenticated else None

    # Skip self-views
    if user and listing.owner_id == user.pk:
        return

    ip         = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]
    source     = request.query_params.get('source', 'direct')
    if source not in _VALID_SOURCES:
        source = 'direct'

    today = timezone.now().date()

    if user:
        already_today = ViewLog.objects.filter(
            listing=listing, user=user, viewed_at__date=today,
        ).exists()
        is_first_ever = not ViewLog.objects.filter(
            listing=listing, user=user,
        ).exists()
    else:
        already_today = ViewLog.objects.filter(
            listing=listing, user__isnull=True, ip_address=ip, viewed_at__date=today,
        ).exists()
        is_first_ever = not ViewLog.objects.filter(
            listing=listing, user__isnull=True, ip_address=ip,
        ).exists()

    if already_today:
        return  # Already recorded a view for today — skip

    ViewLog.objects.create(
        listing=listing,
        user=user,
        ip_address=ip,
        user_agent=user_agent,
        source=source,
    )

    update_fields = {'view_count': F('view_count') + 1}
    if is_first_ever:
        update_fields['unique_view_count'] = F('unique_view_count') + 1

    Listing.objects.filter(pk=listing.pk).update(**update_fields)


# ---------------------------------------------------------------------------
# Car
# ---------------------------------------------------------------------------

@extend_schema(tags=['Listings'])
class CarViewSet(viewsets.ModelViewSet):
    """ViewSet for Car model."""

    queryset = Car.objects.select_related('seller').prefetch_related('images').all()
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = CarFilter
    search_fields = ['title', 'description', 'make', 'model']
    ordering_fields = ['price', 'year', 'created_at', 'mileage']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return CarListSerializer
        return CarSerializer

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == 'list':
            status_param = self.request.query_params.get('status', 'AVAILABLE')
            if status_param:
                queryset = queryset.filter(status=status_param)
        return queryset

    def perform_create(self, serializer):
        instance = serializer.save(seller=self.request.user)
        log_action(
            user=self.request.user,
            action='create',
            model_name='Car',
            object_id=instance.pk,
            old_value=None,
            new_value=_car_snapshot(instance),
            ip_address=get_client_ip(self.request),
        )

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.seller != request.user and request.user.role != 'admin':
            return Response(
                {'error': 'You do not have permission to update this car'},
                status=status.HTTP_403_FORBIDDEN,
            )
        old_values = _car_snapshot(instance)
        response = super().update(request, *args, **kwargs)
        instance.refresh_from_db()
        log_action(
            user=request.user,
            action='update',
            model_name='Car',
            object_id=instance.pk,
            old_value=old_values,
            new_value=_car_snapshot(instance),
            ip_address=get_client_ip(request),
        )
        return response

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.seller != request.user and request.user.role != 'admin':
            return Response(
                {'error': 'You do not have permission to delete this car'},
                status=status.HTTP_403_FORBIDDEN,
            )
        old_values = _car_snapshot(instance)
        object_id = instance.pk
        response = super().destroy(request, *args, **kwargs)
        log_action(
            user=request.user,
            action='delete',
            model_name='Car',
            object_id=object_id,
            old_value=old_values,
            new_value=None,
            ip_address=get_client_ip(request),
        )
        return response

    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def my_listings(self, request):
        queryset = self.filter_queryset(
            self.get_queryset().filter(seller=request.user)
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Car Images  (existing — now uploads to Cloudinary via uploader.upload)
# ---------------------------------------------------------------------------

@extend_schema(tags=['Listing Images'])
class CarImageViewSet(viewsets.ModelViewSet):
    """ViewSet for CarImage model."""

    queryset = CarImage.objects.select_related('car').all()
    serializer_class = CarImageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        car_id = self.request.query_params.get('car_id')
        if car_id:
            queryset = queryset.filter(car_id=car_id)
        return queryset

    def create(self, request, *args, **kwargs):
        car_id = request.data.get('car_id')
        if not car_id:
            return Response({'error': 'car_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            car = Car.objects.get(id=car_id)
        except Car.DoesNotExist:
            return Response({'error': 'Car not found'}, status=status.HTTP_404_NOT_FOUND)

        if car.seller != request.user and request.user.role != 'admin':
            return Response(
                {'error': 'You do not have permission to upload images for this car'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if 'image' not in request.FILES:
            return Response({'error': 'Image file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        result = cloudinary.uploader.upload(request.FILES['image'], folder='cars/')
        car_image = CarImage.objects.create(car=car, image=result['public_id'])
        car_image.refresh_from_db()
        serializer = self.get_serializer(car_image)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.car.seller != request.user and request.user.role != 'admin':
            return Response(
                {'error': 'You do not have permission to delete this image'},
                status=status.HTTP_403_FORBIDDEN,
            )
        public_id = instance.image.public_id if instance.image else None
        response = super().destroy(request, *args, **kwargs)
        if public_id:
            try:
                cloudinary.uploader.destroy(public_id)
            except Exception:
                pass
        return response


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

@extend_schema(tags=['Listings'])
class ListingViewSet(viewsets.ModelViewSet):
    """
    REST API for cars.models.Listing.

    Visibility: public → approved only; admin → all; authenticated → approved + own.
    Filtering, search, and ordering are supported.

    Example queries:
        /api/listings?make=Toyota
        /api/listings?make__icontains=toyota
        /api/listings?price__gte=50000&price__lte=100000
        /api/listings?year__gte=2018
        /api/listings?city=Riyadh
        /api/listings?search=Camry
        /api/listings?ordering=price
        /api/listings?ordering=-created_at
    """

    serializer_class = ListingSerializer
    queryset = Listing.objects.select_related(
        'owner', 'showroom', 'workshop', 'city_obj',
    ).prefetch_related('images').all()
    permission_classes = [AllowAny]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ListingFilter
    search_fields = ['title', 'make', 'model', 'city', 'description']
    ordering_fields = ['price', 'year', 'created_at']
    ordering = ['-created_at']

    def get_pagination_class(self):
        from .pagination import OptInCursorPagination
        return OptInCursorPagination

    @property
    def pagination_class(self):
        return self.get_pagination_class()

    def get_serializer_class(self):
        if self.action == 'list':
            return ListingListSerializer
        return ListingSerializer

    def get_permissions(self):
        if self.action in (
            'list', 'retrieve', 'compare', 'autocomplete', 'popular',
            'nearby', 'map_pins', 'featured', 'promotion_packages',
        ):
            return [AllowAny()]
        return [IsAuthenticated(), IsOwnerOrAdmin()]

    def get_queryset(self):
        user = self.request.user
        now  = timezone.now()
        base = Listing.objects.select_related(
            'owner', 'showroom', 'workshop', 'approved_by', 'city_obj',
        ).prefetch_related(
            'images',
            # Phase 4.6 — prefetch only active promotions to avoid N+1 in ListingSerializer
            Prefetch(
                'promotions',
                queryset=ListingPromotion.objects.filter(
                    status='active', expires_at__gt=now,
                ).select_related('package'),
                to_attr='active_promotions_prefetched',
            ),
        )

        # Off-market rules for public browse — see cars/visibility.py
        # (single source of truth shared with /api/imported-cars/ views).
        _public = public_market_q()

        if not user or not user.is_authenticated:
            qs = base.filter(_public)
        elif getattr(user, 'role', None) == 'admin':
            qs = base.all()
        else:
            # Parties keep access: the importer sees their own listings and
            # a buyer keeps access to a car they have an order on.
            qs = base.filter(_public | party_q(user)).distinct()

        # ?mine=1 — the importer's own listings ONLY (My Listings page).
        # Cars in a PAID deal (full payment verified by WARED) are excluded
        # here: they belong on the Orders page. If that order is later
        # cancelled/refunded, the car automatically returns to My Listings.
        if self.request.query_params.get('mine') and user and user.is_authenticated:
            from django.db.models import Exists, OuterRef
            from orders.models import ImportOrder
            paid_deal = ImportOrder.objects.filter(
                car=OuterRef('pk'),
                payments__payment_type='balance',
                payments__status='succeeded',
            ).exclude(status__in=('cancelled', 'refunded'))
            qs = base.filter(owner=user).filter(~Exists(paid_deal))

        # ids= filter: works on list endpoint (no min/max restriction)
        ids_param = self.request.query_params.get('ids')
        if ids_param:
            try:
                id_list = [int(i.strip()) for i in ids_param.split(',') if i.strip()]
                qs = qs.filter(id__in=id_list)
            except ValueError:
                pass

        return qs.order_by('-created_at')

    def list(self, request, *args, **kwargs):
        """
        Override list() to inject promotion boost ordering:
        is_top_search listings appear first, then is_featured, then normal ordering.
        """
        queryset = self.filter_queryset(self.get_queryset())

        # Annotate and re-order: promoted listings bubble to the top
        current_order = list(queryset.query.order_by) or ['-created_at']
        queryset = queryset.annotate(
            _promo_boost=Case(
                When(is_top_search=True, then=Value(2)),
                When(is_featured=True,   then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        ).order_by('-_promo_boost', *current_order)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated], url_path='my')
    def my_listings(self, request):
        listings = (
            Listing.objects
            .filter(owner=request.user, is_active=True)
            .select_related('owner', 'showroom', 'workshop')
            .prefetch_related('images')
            .order_by('-created_at')
        )
        serializer = self.get_serializer(listings, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        """Fetch a single listing; track RecentlyViewed + ViewLog for non-owners."""
        instance = self.get_object()
        serializer = self.get_serializer(instance)

        if request.user.is_authenticated and instance.owner_id != request.user.pk:
            rv, created = RecentlyViewed.objects.get_or_create(
                user=request.user,
                listing=instance,
            )
            if not created:
                # auto_now=True updates viewed_at only when save() is called
                rv.save(update_fields=['viewed_at'])
            elif created:
                # Auto-cleanup: keep only the 50 most recent per user
                excess_ids = list(
                    RecentlyViewed.objects
                    .filter(user=request.user)
                    .order_by('-viewed_at')
                    .values_list('id', flat=True)[50:]
                )
                if excess_ids:
                    RecentlyViewed.objects.filter(id__in=excess_ids).delete()

        # Phase 2.13 — track view (lightweight, atomic F() updates)
        _track_view(request, instance)

        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='autocomplete', permission_classes=[AllowAny])
    def autocomplete(self, request):
        """
        GET /api/listings/autocomplete/?q=toy
        Returns up to 10 distinct make/model suggestions from approved listings.
        Minimum query length: 2 characters.
        """
        q = request.query_params.get('q', '').strip()
        if len(q) < 2:
            return Response(
                {'error': 'Query must be at least 2 characters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        base_qs = Listing.objects.filter(status='approved', is_active=True)

        # Fetch up to 10 distinct makes first, fill remaining slots with models
        makes = list(
            base_qs
            .filter(make__icontains=q)
            .values_list('make', flat=True)
            .distinct()
            .order_by('make')[:10]
        )
        remaining = max(0, 10 - len(makes))
        models_list = (
            list(
                base_qs
                .filter(model__icontains=q)
                .values_list('model', flat=True)
                .distinct()
                .order_by('model')[:remaining]
            )
            if remaining > 0 else []
        )

        suggestions = [{'type': 'make', 'value': m} for m in makes]
        suggestions += [{'type': 'model', 'value': m} for m in models_list]
        return Response({'suggestions': suggestions})

    @action(detail=False, methods=['get'], url_path='popular', permission_classes=[AllowAny])
    def popular(self, request):
        """
        GET /api/listings/popular/
        GET /api/listings/popular/?period=week
        GET /api/listings/popular/?period=month

        Returns up to 10 approved+active listings ordered by views.
        Default (no period): ordered by total view_count.
        period=week|month: annotated from ViewLog in the last 7|30 days.
        """
        period   = request.query_params.get('period', '')
        base_qs  = (
            Listing.objects
            .filter(status='approved', is_active=True)
            .select_related('owner', 'showroom', 'workshop')
            .prefetch_related('images')
        )

        if period in ('week', 'month'):
            days   = 7 if period == 'week' else 30
            cutoff = timezone.now() - timedelta(days=days)
            qs = (
                base_qs
                .filter(view_logs__viewed_at__gte=cutoff)
                .annotate(period_views=Count('view_logs'))
                .order_by('-period_views')[:10]
            )
        else:
            qs = base_qs.order_by('-view_count')[:10]

        serializer = ListingSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='analytics', permission_classes=[IsAuthenticated])
    def analytics(self, request, pk=None):
        """
        GET /api/listings/{id}/analytics/
        Owner or admin only. Returns detailed view analytics from ViewLog.
        """
        instance = self.get_object()

        if instance.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response(
                {'error': 'Permission denied.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        thirty_days_ago = timezone.now() - timedelta(days=30)
        qs = ViewLog.objects.filter(listing=instance)

        # Views by day — last 30 days
        views_by_day = list(
            qs.filter(viewed_at__gte=thirty_days_ago)
            .annotate(date=TruncDate('viewed_at'))
            .values('date')
            .annotate(views=Count('id'))
            .order_by('date')
            .values('date', 'views')
        )
        views_by_day = [
            {'date': str(entry['date']), 'views': entry['views']}
            for entry in views_by_day
        ]

        # Views by source
        views_by_source = list(
            qs.values('source')
            .annotate(count=Count('id'))
            .order_by('-count')
            .values('source', 'count')
        )

        # Peak hour of day (0–23)
        peak_row = (
            qs.annotate(hour=ExtractHour('viewed_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('-count')
            .first()
        )
        peak_hour = peak_row['hour'] if peak_row else None

        return Response({
            'total_views':     instance.view_count,
            'unique_views':    instance.unique_view_count,
            'views_by_day':    views_by_day,
            'views_by_source': views_by_source,
            'peak_hour':       peak_hour,
        })

    def perform_create(self, serializer):
        from rest_framework.exceptions import Throttled, PermissionDenied
        from fraud.utils import check_and_enforce_limit, increment_listing_count, log_ip_action

        user = self.request.user

        # WARED model: ONLY registered importers (and admins) may list cars.
        # Buyers browse, reserve and buy — they never sell on the platform.
        if getattr(user, 'role', '') not in ('importer', 'admin'):
            raise PermissionDenied(
                'Only registered importers can list cars. '
                'Apply to become an importer first. / '
                'فقط المستوردون المسجّلون يمكنهم عرض السيارات.'
            )

        # Phase 5.4 — Daily fraud limit check (all users)
        allowed, limit, used = check_and_enforce_limit(user)
        if not allowed:
            raise Throttled(detail=f"Daily listing limit reached ({used}/{limit}). Try again tomorrow.")

        # DEPRECATED: Subscription listing-limit check disabled — commission-only model
        # Importers can now list unlimited cars.
        # if user.role == 'importer':
        #     try:
        #         from subscriptions.models import DealerSubscription
        #         from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
        #         sub  = DealerSubscription.objects.select_related('plan').get(dealer=user)
        #         plan = sub.plan
        #         if plan.max_listings > 0:
        #             active_count = Listing.objects.filter(
        #                 owner=user, is_active=True,
        #             ).exclude(status='sold').count()
        #             if active_count >= plan.max_listings:
        #                 raise DRFPermissionDenied({
        #                     'error': (
        #                         f"You've reached your listing limit ({plan.max_listings}). "
        #                         "Upgrade your plan to list more cars."
        #                     ),
        #                     'current_count': active_count,
        #                     'limit':         plan.max_listings,
        #                 })
        #     except DealerSubscription.DoesNotExist:
        #         pass

        instance = serializer.save(owner=user, status='pending')
        _notify_admins_new_listing(instance)

        # Phase 5.4 — Increment daily limit counter
        increment_listing_count(user)

        # Phase 5.4 — Log IP action
        ip = get_client_ip(self.request)
        user_agent = self.request.META.get('HTTP_USER_AGENT', '')
        try:
            log_ip_action(user, ip, 'create_listing', user_agent)
        except Exception:
            pass  # Never block the main flow

        log_action(
            user=user,
            action='create',
            model_name='Listing',
            object_id=instance.pk,
            old_value=None,
            new_value=_listing_snapshot(instance),
            ip_address=ip,
        )

        # Phase 5.4 — Trigger async fraud checks
        try:
            from fraud.tasks import (
                check_duplicate_vin, check_suspicious_pricing,
                check_rapid_posting, check_banned_keywords,
            )
            check_duplicate_vin.delay(instance.id)
            check_suspicious_pricing.delay(instance.id)
            check_rapid_posting.delay(user.id)
            check_banned_keywords.delay(instance.id)
        except Exception:
            pass  # Celery may not be running in dev — never block the main flow

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.owner != request.user and request.user.role != 'admin':
            return Response(
                {'error': 'You do not have permission to update this listing'},
                status=status.HTTP_403_FORBIDDEN,
            )
        old_values = _listing_snapshot(instance)

        # SECURITY: importers can only change import_status on approved listings
        if (request.user.role != 'admin'
                and 'import_status' in request.data
                and instance.status != 'approved'):
            return Response(
                {'error': 'Import status can only be updated on approved listings.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            response = super().update(request, *args, **kwargs)
        except DjangoValidationError as exc:
            msgs = exc.message_dict if hasattr(exc, 'message_dict') else {'error': exc.messages}
            return Response(msgs, status=status.HTTP_400_BAD_REQUEST)

        instance.refresh_from_db()
        log_action(
            user=request.user,
            action='update',
            model_name='Listing',
            object_id=instance.pk,
            old_value=old_values,
            new_value=_listing_snapshot(instance),
            ip_address=get_client_ip(request),
        )
        # Check for status transition via generic update (admin edit modal)
        old_status = old_values.get('status', '')
        _notify_listing_status_change(instance, old_status, request.user)

        # Resubmit: owner editing a changes_requested/rejected listing → back to pending
        if (old_values.get('status') in ('changes_requested', 'rejected')
                and instance.owner == request.user
                and request.user.role != 'admin'
                and instance.status in ('changes_requested', 'rejected')):
            instance.status = 'pending'
            instance.save(update_fields=['status'])
            _notify_admins_new_listing(instance)

        return response

    def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Soft delete: set is_active=False."""
        instance = self.get_object()
        if instance.owner != request.user and request.user.role != 'admin':
            return Response(
                {'error': 'You do not have permission to delete this listing'},
                status=status.HTTP_403_FORBIDDEN,
            )
        # An importer may remove an AVAILABLE car, but never one that is part
        # of an active deal — the order must be cancelled first (admin can).
        if request.user.role != 'admin':
            from .visibility import ACTIVE_ORDER_STATUSES
            if instance.import_orders.filter(status__in=ACTIVE_ORDER_STATUSES).exists():
                return Response(
                    {'error': 'This car is part of an active deal and cannot be removed. '
                              'Cancel the order first.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        old_values = _listing_snapshot(instance)
        instance.is_active = False
        instance.save(update_fields=['is_active'])
        log_action(
            user=request.user,
            action='delete',
            model_name='Listing',
            object_id=instance.pk,
            old_value=old_values,
            new_value=None,
            ip_address=get_client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'], url_path='compare', permission_classes=[AllowAny])
    def compare(self, request):
        """
        GET /api/listings/compare/?ids=1,2,3,4
        Returns 2–4 approved listings in the exact order the IDs were supplied.
        """
        ids_param = request.query_params.get('ids', '')
        try:
            ids = [int(i.strip()) for i in ids_param.split(',') if i.strip()]
        except ValueError:
            return Response(
                {'error': 'ids must be comma-separated integers.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(ids) < 2:
            return Response(
                {'error': 'Provide at least 2 IDs to compare.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(ids) > 4:
            return Response(
                {'error': 'You can compare at most 4 listings at a time.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Preserve caller-supplied order via Case/When
        order_preserved = Case(
            *[When(id=pk, then=Value(i)) for i, pk in enumerate(ids)],
            output_field=IntegerField(),
        )
        listings = (
            Listing.objects
            .select_related('owner', 'showroom', 'workshop')
            .prefetch_related('images')
            .filter(id__in=ids, status='approved', is_active=True)
            .annotate(_order=order_preserved)
            .order_by('_order')
        )

        serializer = ListingSerializer(listings, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['patch'], url_path='approve', permission_classes=[IsAuthenticated])
    def approve(self, request, pk=None):
        if request.user.role != 'admin':
            return Response(
                {'error': 'Only admin can approve listings'},
                status=status.HTTP_403_FORBIDDEN,
            )
        instance = self.get_object()
        old_status = instance.status
        now = timezone.now()
        instance.status            = 'approved'
        instance.approved_by       = request.user
        instance.approved_at       = now
        instance.rejection_reason  = ''
        instance.status_changed_at = now
        instance.status_changed_by = request.user
        instance.save(update_fields=[
            'status', 'approved_by', 'approved_at',
            'rejection_reason', 'status_changed_at', 'status_changed_by',
        ])
        log_action(
            user=request.user,
            action='approve',
            model_name='Listing',
            object_id=instance.pk,
            old_value={'status': old_status},
            new_value={'status': instance.status},
            ip_address=get_client_ip(request),
        )
        _notify_listing_status_change(instance, old_status, request.user)
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=['patch'], url_path='reject', permission_classes=[IsAuthenticated])
    def reject(self, request, pk=None):
        if request.user.role != 'admin':
            return Response(
                {'error': 'Only admin can reject listings'},
                status=status.HTTP_403_FORBIDDEN,
            )
        rejection_reason = request.data.get('rejection_reason', '').strip()
        if not rejection_reason:
            return Response(
                {'error': 'rejection_reason is required when rejecting a listing.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance = self.get_object()
        old_status = instance.status
        now = timezone.now()
        instance.status            = 'rejected'
        instance.approved_by       = None
        instance.approved_at       = None
        instance.rejection_reason  = rejection_reason
        instance.status_changed_at = now
        instance.status_changed_by = request.user
        instance.save(update_fields=[
            'status', 'approved_by', 'approved_at',
            'rejection_reason', 'status_changed_at', 'status_changed_by',
        ])
        log_action(
            user=request.user,
            action='reject',
            model_name='Listing',
            object_id=instance.pk,
            old_value={'status': old_status},
            new_value={'status': instance.status},
            ip_address=get_client_ip(request),
        )
        _notify_listing_status_change(instance, old_status, request.user)
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=['patch'], url_path='request-changes', permission_classes=[IsAuthenticated])
    def request_changes(self, request, pk=None):
        if request.user.role != 'admin':
            return Response({'error': 'Only admin can request changes'}, status=status.HTTP_403_FORBIDDEN)
        admin_note = request.data.get('admin_notes', '').strip()
        if not admin_note:
            return Response({'error': 'admin_notes is required when requesting changes.'}, status=status.HTTP_400_BAD_REQUEST)
        instance = self.get_object()
        old_status = instance.status
        now = timezone.now()
        instance.status = 'changes_requested'
        instance.admin_notes = admin_note
        instance.status_changed_at = now
        instance.status_changed_by = request.user
        instance.save(update_fields=['status', 'admin_notes', 'status_changed_at', 'status_changed_by'])
        log_action(
            user=request.user, action='request_changes', model_name='Listing',
            object_id=instance.pk, old_value={'status': old_status},
            new_value={'status': instance.status}, ip_address=get_client_ip(request),
        )
        _notify_listing_status_change(instance, old_status, request.user)
        return Response(self.get_serializer(instance).data)

    @action(detail=False, methods=['get'], url_path='nearby', permission_classes=[AllowAny])
    def nearby(self, request):
        """
        GET /api/listings/nearby/?lat=24.7136&lng=46.6753&radius=50
        Returns approved+active listings within *radius* km ordered by distance.
        Response includes distance_km (rounded to 1 decimal).
        """
        try:
            lat    = float(request.query_params.get('lat', ''))
            lng    = float(request.query_params.get('lng', ''))
            radius = float(request.query_params.get('radius', 50))
        except (ValueError, TypeError):
            return Response(
                {'error': 'lat and lng are required numeric parameters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        radius = min(radius, 500.0)  # cap at 500 km

        # 1. Rough bounding box pre-filter (uses index on latitude/longitude)
        box = bounding_box(lat, lng, radius)
        qs = (
            Listing.objects
            .filter(
                status='approved', is_active=True,
                latitude__isnull=False, longitude__isnull=False,
                latitude__gte=box['lat_min'], latitude__lte=box['lat_max'],
                longitude__gte=box['lng_min'], longitude__lte=box['lng_max'],
            )
            .prefetch_related('images')
        )

        # 2. Annotate with exact Haversine distance, filter, sort
        qs = (
            qs
            .annotate(distance_km=haversine_annotation(lat, lng))
            .filter(distance_km__lte=radius)
            .order_by('distance_km')
        )

        # Round distance_km to 1 decimal in Python after evaluation
        results = []
        for listing in qs[:100]:
            listing.distance_km = round(listing.distance_km, 1)
            results.append(listing)

        serializer = NearbyListingSerializer(results, many=True, context={'request': request})
        return Response({'count': len(results), 'results': serializer.data})

    @action(detail=False, methods=['get'], url_path='map-pins', permission_classes=[AllowAny])
    def map_pins(self, request):
        """
        GET /api/listings/map-pins/?lat_min=24&lat_max=25&lng_min=46&lng_max=47
        Returns up to 200 lightweight pin objects for approved+active listings
        that have coordinates. Supports viewport bounds filters.
        """
        qs = (
            Listing.objects
            .filter(status='approved', is_active=True,
                    latitude__isnull=False, longitude__isnull=False)
            .prefetch_related('images')
        )
        # Apply viewport bounds via ListingFilter (only lat/lng bounds params)
        qs = ListingFilter(request.query_params, queryset=qs).qs
        qs = qs[:200]
        serializer = ListingMapPinSerializer(qs, many=True, context={'request': request})
        return Response({'count': len(serializer.data), 'results': serializer.data})

    # -------------------------------------------------------------------------
    # Phase 4.6 — Promotion actions
    # -------------------------------------------------------------------------

    # Mapping from PromotionPackage.promotion_type → Listing flag field name
    _PROMO_TYPE_TO_FLAG = {
        'featured':    'is_featured',
        'highlighted': 'is_highlighted',
        'top_search':  'is_top_search',
        'homepage':    'is_homepage',
    }

    @action(detail=False, methods=['get'], url_path='featured', permission_classes=[AllowAny])
    def featured(self, request):
        """
        GET /api/listings/featured/
        Returns approved+active listings that are featured or on the homepage,
        ordered by promotion_priority (highest first). Public endpoint.
        """
        qs = (
            Listing.objects
            .filter(
                Q(is_featured=True) | Q(is_homepage=True),
                status='approved', is_active=True,
            )
            .select_related('owner', 'showroom', 'workshop')
            .prefetch_related(
                'images',
                Prefetch(
                    'promotions',
                    queryset=ListingPromotion.objects.filter(
                        status='active', expires_at__gt=timezone.now(),
                    ).select_related('package'),
                    to_attr='active_promotions_prefetched',
                ),
            )
            .order_by('-promotion_priority', '-created_at')
        )
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='promotion-packages', permission_classes=[AllowAny])
    def promotion_packages(self, request):
        """
        GET /api/listings/promotion-packages/
        Returns all active promotion packages a dealer can purchase.
        """
        packages = PromotionPackage.objects.filter(is_active=True).order_by('display_order')
        serializer = PromotionPackageSerializer(packages, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='promote', permission_classes=[IsAuthenticated])
    def promote(self, request, pk=None):
        """
        POST /api/listings/{id}/promote/
        Body: {"package_id": <int>, "reference": "<bank transfer reference>"}

        Payment-first flow: the importer transfers the package price to WARED's
        bank account, submits the transfer reference here, and the promotion is
        created as 'pending_payment'. It only activates (and affects browse /
        homepage) after a WARED admin verifies the transfer in the Payments tab.
        """
        instance = self.get_object()

        # Permission: owner or admin
        if instance.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        # Listing must be approved and active
        if instance.status != 'approved' or not instance.is_active:
            return Response(
                {'error': 'Only approved, active listings can be promoted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Only AVAILABLE cars can be boosted — a reserved/sold car or a car in
        # an active deal is off-market and cannot be promoted.
        if instance.import_status != 'available':
            return Response(
                {'error': 'Only available cars can be promoted. This car is '
                          f"currently '{instance.import_status}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .visibility import ACTIVE_ORDER_STATUSES
        if instance.import_orders.filter(status__in=ACTIVE_ORDER_STATUSES).exists():
            return Response(
                {'error': 'This car is in an active deal and cannot be promoted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reference = (request.data.get('reference') or '').strip()
        if not reference:
            return Response(
                {'reference': ['Bank transfer reference is required — transfer the '
                               'package price to the WARED account first.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PromoteListingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        package = serializer.validated_data['package']

        now = timezone.now()

        # Check max_featured_listings from dealer subscription
        try:
            from subscriptions.models import DealerSubscription
            sub = DealerSubscription.objects.select_related('plan').get(dealer=instance.owner)
            max_featured = sub.plan.max_featured_listings
            if max_featured > 0:
                active_count = ListingPromotion.objects.filter(
                    dealer=instance.owner, status='active', expires_at__gt=now,
                ).count()
                if active_count >= max_featured:
                    return Response(
                        {
                            'error': (
                                f"You have reached your maximum featured listings ({max_featured}). "
                                "Upgrade your subscription to promote more listings."
                            ),
                            'active_promotions': active_count,
                            'limit': max_featured,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        except DealerSubscription.DoesNotExist:
            pass  # No subscription — no limit enforced

        # Check for an existing active or awaiting-payment promotion of the
        # same type on this listing
        duplicate = ListingPromotion.objects.filter(
            Q(status='active', expires_at__gt=now) | Q(status='pending_payment'),
            listing=instance,
            package__promotion_type=package.promotion_type,
        ).exists()
        if duplicate:
            return Response(
                {'error': f"This listing already has an active or pending "
                          f"'{package.promotion_type}' promotion."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create the promotion in PENDING PAYMENT — no visibility boost yet.
        # expires_at is provisional; it is reset from the moment the admin
        # confirms the payment so the importer gets the full duration.
        expires_at = now + timedelta(days=package.duration_days)
        promotion = ListingPromotion.objects.create(
            listing=instance,
            package=package,
            dealer=instance.owner,
            status='pending_payment',
            expires_at=expires_at,
            amount_paid=package.price,
        )

        from payments.models import PaymentTransaction
        PaymentTransaction.objects.create(
            promotion=promotion,
            user=request.user,
            amount=package.price,
            payment_type='promotion',
            method='bank_transfer',
            provider='bank_transfer',
            provider_transaction_id=reference,
            status='pending',
        )

        log_action(
            user=request.user,
            action='update',
            model_name='Listing',
            object_id=instance.pk,
            old_value={'promotion': None},
            new_value={
                'promotion_package': package.name,
                'promotion_type': package.promotion_type,
                'status': 'pending_payment',
                'reference': reference,
            },
            ip_address=get_client_ip(request),
        )
        from notifications.utils import notify
        notify(
            recipient=instance.owner,
            notification_type='listing_approved',  # reuse closest type
            title='Promotion payment under review',
            message=(
                f'Your "{package.name}" promotion for "{instance.title}" will '
                'activate as soon as WARED verifies your bank transfer '
                '(within 1 business day).'
            ),
            listing=instance,
        )

        data = ListingPromotionSerializer(promotion, context={'request': request}).data
        data['detail'] = (
            'Promotion submitted — it activates once WARED verifies your '
            'bank transfer.'
        )
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='promote/cancel', permission_classes=[IsAuthenticated])
    def cancel_promotion(self, request, pk=None):
        """
        POST /api/listings/{id}/promote/cancel/
        Body: {"promotion_id": <int>}  (optional — cancels all active if omitted)
        Cancels active promotion(s) and clears listing flags.
        """
        instance = self.get_object()

        if instance.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        now = timezone.now()
        qs  = ListingPromotion.objects.filter(listing=instance, status='active')

        promotion_id = request.data.get('promotion_id')
        if promotion_id:
            qs = qs.filter(pk=promotion_id)

        if not qs.exists():
            return Response(
                {'error': 'No active promotion found for this listing.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        cancelled_count = qs.update(status='cancelled')

        # Recalculate listing flags from any remaining active promotions
        remaining = ListingPromotion.objects.filter(
            listing=instance, status='active', expires_at__gt=now,
        ).select_related('package')

        instance.is_featured    = remaining.filter(package__promotion_type='featured').exists()
        instance.is_highlighted = remaining.filter(package__promotion_type='highlighted').exists()
        instance.is_top_search  = remaining.filter(package__promotion_type='top_search').exists()
        instance.is_homepage    = remaining.filter(package__promotion_type='homepage').exists()
        max_priority = remaining.aggregate(mp=Max('package__priority'))['mp'] or 0
        instance.promotion_priority = max_priority
        instance.promotion_expires_at = (
            remaining.order_by('expires_at').values_list('expires_at', flat=True).first()
        )
        instance.save(update_fields=[
            'is_featured', 'is_highlighted', 'is_top_search', 'is_homepage',
            'promotion_priority', 'promotion_expires_at',
        ])

        log_action(
            user=request.user,
            action='update',
            model_name='Listing',
            object_id=instance.pk,
            old_value={'promotion_cancelled': cancelled_count},
            new_value={'promotion': None},
            ip_address=get_client_ip(request),
        )

        return Response({'cancelled': cancelled_count})

    @action(detail=True, methods=['get'], url_path='promotions', permission_classes=[IsAuthenticated])
    def promotions(self, request, pk=None):
        """
        GET /api/listings/{id}/promotions/
        Returns promotion history for a specific listing (owner or admin only).
        """
        instance = self.get_object()

        if instance.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        qs = (
            ListingPromotion.objects
            .filter(listing=instance)
            .select_related('package', 'listing')
            .order_by('-created_at')
        )
        serializer = ListingPromotionSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Phase 4.6 — My Promotions (standalone view)
# ---------------------------------------------------------------------------

@extend_schema(tags=['Promotions'])
class MyPromotionsView(generics.ListAPIView):
    """
    GET /api/my-promotions/
    Returns all promotions created by the authenticated dealer.
    Query params:
      ?status=active|expired|cancelled|pending_payment
      ?listing=<id>
    """
    serializer_class   = ListingPromotionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = (
            ListingPromotion.objects
            .filter(dealer=self.request.user)
            .select_related('package', 'listing')
            .order_by('-created_at')
        )
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)

        listing_param = self.request.query_params.get('listing')
        if listing_param:
            try:
                qs = qs.filter(listing_id=int(listing_param))
            except (ValueError, TypeError):
                pass

        return qs


# ---------------------------------------------------------------------------
# Phase 2.15 — Showroom ViewSet
# ---------------------------------------------------------------------------

@extend_schema(tags=['Showrooms'])
class ShowroomViewSet(viewsets.ModelViewSet):
    """
    Phase 4.1 — Full showroom management.

    List/detail: AllowAny
    Create: authenticated dealer or admin
    Update/delete: owner or admin
    Sub-resources: /listings/, /reviews/, /reviews/<id>/, /branches/, /branches/<id>/,
                   /working-hours/, /verify/
    """

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ShowroomFilter
    search_fields   = ['name', 'name_ar', 'city', 'description']
    ordering_fields = ['name', 'created_at', 'average_rating', 'total_listings']
    ordering        = ['name']

    def get_queryset(self):
        qs = Showroom.objects.select_related('owner', 'city_obj').prefetch_related(
            'working_hours', 'branches__city_obj',
        )
        if self.action in ('list', 'nearby', 'map_pins'):
            qs = qs.filter(is_active=True)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return ShowroomListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return ShowroomCreateUpdateSerializer
        return ShowroomDetailSerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy',
                           'branch_detail', 'review_detail', 'verify'):
            return [IsAuthenticated()]
        return [AllowAny()]

    def perform_create(self, serializer):
        user = self.request.user
        if getattr(user, 'role', None) not in ('importer', 'admin'):
            raise PermissionDenied('Only importers or admins can create showrooms.')
        showroom = serializer.save(owner=user, is_verified=False)
        log_action(
            user=user, action='create',
            model_name='Showroom', object_id=showroom.pk,
            ip_address=get_client_ip(self.request),
        )

    def perform_update(self, serializer):
        # serializer.instance is already the object being updated
        instance = serializer.instance
        if instance.owner != self.request.user and getattr(self.request.user, 'role', None) != 'admin':
            raise PermissionDenied('Only the owner or an admin can update this showroom.')
        updated = serializer.save()
        log_action(
            user=self.request.user, action='update',
            model_name='Showroom', object_id=updated.pk,
            ip_address=get_client_ip(self.request),
        )

    def destroy(self, request, *args, **kwargs):
        showroom = self.get_object()
        if showroom.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        showroom.is_active = False
        showroom.save(update_fields=['is_active'])
        log_action(
            user=request.user, action='delete',
            model_name='Showroom', object_id=showroom.pk,
            ip_address=get_client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------ listings
    @action(detail=True, methods=['get'], url_path='listings',
            permission_classes=[AllowAny])
    def listings(self, request, pk=None):
        """GET /api/showrooms/{id}/listings/ — approved listings for this showroom."""
        showroom = self.get_object()
        qs = Listing.objects.filter(
            showroom=showroom, status='approved', is_active=True
        ).order_by('-created_at')
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = ListingSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)
        serializer = ListingSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    # ------------------------------------------------------------------ reviews
    @action(detail=True, methods=['get', 'post'], url_path='reviews',
            permission_classes=[AllowAny])
    def reviews(self, request, pk=None):
        """GET/POST /api/showrooms/{id}/reviews/"""
        showroom = self.get_object()
        if request.method == 'GET':
            qs = showroom.reviews.filter(is_approved=True).select_related('user')
            page = self.paginate_queryset(qs)
            if page is not None:
                return self.get_paginated_response(
                    ShowroomReviewSerializer(page, many=True, context={'request': request}).data
                )
            return Response(
                ShowroomReviewSerializer(qs, many=True, context={'request': request}).data
            )
        # POST
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
        if showroom.owner_id == request.user.pk:
            return Response({'detail': 'Cannot review your own showroom.'}, status=status.HTTP_400_BAD_REQUEST)
        if showroom.reviews.filter(user=request.user).exists():
            return Response({'detail': 'You have already reviewed this showroom.'}, status=status.HTTP_400_BAD_REQUEST)
        serializer = ShowroomReviewSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        review = serializer.save(showroom=showroom, user=request.user)
        log_action(
            user=request.user, action='create',
            model_name='ShowroomReview', object_id=review.pk,
            ip_address=get_client_ip(request),
        )
        return Response(
            ShowroomReviewSerializer(review, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['patch', 'delete'],
            url_path=r'reviews/(?P<review_id>[0-9]+)',
            permission_classes=[IsAuthenticated])
    def review_detail(self, request, pk=None, review_id=None):
        """PATCH/DELETE /api/showrooms/{id}/reviews/{review_id}/"""
        showroom = self.get_object()
        review   = get_object_or_404(ShowroomReview, pk=review_id, showroom=showroom)
        is_admin = getattr(request.user, 'role', None) == 'admin'
        if review.user != request.user and not is_admin:
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        if request.method == 'DELETE':
            review.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = ShowroomReviewSerializer(
            review, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # ------------------------------------------------------------------ branches
    @action(detail=True, methods=['get', 'post'], url_path='branches',
            permission_classes=[AllowAny])
    def branches(self, request, pk=None):
        """GET/POST /api/showrooms/{id}/branches/"""
        showroom = self.get_object()
        if request.method == 'GET':
            qs = showroom.branches.filter(is_active=True).select_related('city_obj')
            return Response(
                ShowroomBranchSerializer(qs, many=True, context={'request': request}).data
            )
        # POST
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
        if showroom.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        if showroom.branches.count() >= 10:
            return Response(
                {'detail': 'Maximum 10 branches allowed per showroom.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ShowroomBranchSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        branch = serializer.save(showroom=showroom)
        return Response(
            ShowroomBranchSerializer(branch, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['patch', 'delete'],
            url_path=r'branches/(?P<branch_id>[0-9]+)',
            permission_classes=[IsAuthenticated])
    def branch_detail(self, request, pk=None, branch_id=None):
        """PATCH/DELETE /api/showrooms/{id}/branches/{branch_id}/"""
        showroom = self.get_object()
        branch   = get_object_or_404(ShowroomBranch, pk=branch_id, showroom=showroom)
        if showroom.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        if request.method == 'DELETE':
            branch.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = ShowroomBranchSerializer(
            branch, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # ------------------------------------------------------------------ working hours
    @action(detail=True, methods=['get', 'put'], url_path='working-hours',
            permission_classes=[AllowAny])
    def working_hours(self, request, pk=None):
        """GET /api/showrooms/{id}/working-hours/   — public read
        PUT /api/showrooms/{id}/working-hours/   — replace all (owner/admin)"""
        showroom = self.get_object()
        if request.method == 'GET':
            qs = showroom.working_hours.all()
            return Response(
                ShowroomWorkingHoursSerializer(qs, many=True, context={'request': request}).data
            )
        # PUT
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
        if showroom.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        hours_data = (
            request.data
            if isinstance(request.data, list)
            else request.data.get('hours', [])
        )
        serializer = ShowroomWorkingHoursSerializer(
            data=hours_data, many=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        showroom.working_hours.all().delete()
        created = []
        for item in serializer.validated_data:
            wh = ShowroomWorkingHours(showroom=showroom, **item)
            try:
                wh.full_clean()
            except DjangoValidationError as exc:
                return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
            wh.save()
            created.append(wh)
        return Response(
            ShowroomWorkingHoursSerializer(created, many=True, context={'request': request}).data
        )

    # ------------------------------------------------------------------ mine
    @action(detail=False, methods=['get'], url_path='mine',
            permission_classes=[IsAuthenticated])
    def mine(self, request):
        """GET /api/showrooms/mine/ — returns showrooms owned by the current user."""
        qs = Showroom.objects.filter(owner=request.user).select_related(
            'owner', 'city_obj'
        ).prefetch_related('working_hours', 'branches__city_obj')
        serializer = ShowroomDetailSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    # ------------------------------------------------------------------ verify
    @action(detail=True, methods=['post'], url_path='verify',
            permission_classes=[IsAuthenticated])
    def verify(self, request, pk=None):
        """POST /api/showrooms/{id}/verify/ — admin only."""
        if getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        showroom = self.get_object()
        showroom.is_verified = True
        showroom.verified    = True   # keep legacy flag in sync
        showroom.verified_at = timezone.now()
        showroom.save(update_fields=['is_verified', 'verified', 'verified_at'])
        log_action(
            user=request.user, action='approve',
            model_name='Showroom', object_id=showroom.pk,
            ip_address=get_client_ip(request),
        )
        return Response({'detail': 'Showroom verified.', 'verified_at': showroom.verified_at})

    # ------------------------------------------------------------------ nearby / map-pins
    @action(detail=False, methods=['get'], url_path='nearby',
            permission_classes=[AllowAny])
    def nearby(self, request):
        """GET /api/showrooms/nearby/?lat=&lng=&radius="""
        try:
            lat    = float(request.query_params.get('lat', ''))
            lng    = float(request.query_params.get('lng', ''))
            radius = float(request.query_params.get('radius', 30))
        except (ValueError, TypeError):
            return Response(
                {'error': 'lat and lng are required numeric parameters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        radius = min(radius, 500.0)
        box = bounding_box(lat, lng, radius)
        qs = (
            Showroom.objects
            .filter(
                is_active=True,
                latitude__isnull=False, longitude__isnull=False,
                latitude__gte=box['lat_min'], latitude__lte=box['lat_max'],
                longitude__gte=box['lng_min'], longitude__lte=box['lng_max'],
            )
            .annotate(distance_km=haversine_annotation(lat, lng))
            .filter(distance_km__lte=radius)
            .order_by('distance_km')
        )
        results = []
        for obj in qs[:100]:
            obj.distance_km = round(obj.distance_km, 1)
            results.append(obj)
        serializer = NearbyShowroomSerializer(results, many=True, context={'request': request})
        return Response({'count': len(results), 'results': serializer.data})

    @action(detail=False, methods=['get'], url_path='map-pins',
            permission_classes=[AllowAny])
    def map_pins(self, request):
        """GET /api/showrooms/map-pins/ — viewport-filtered lightweight pins."""
        qs = Showroom.objects.filter(is_active=True, latitude__isnull=False, longitude__isnull=False)
        qs = ShowroomFilter(request.query_params, queryset=qs).qs[:200]
        serializer = ShowroomMapPinSerializer(qs, many=True, context={'request': request})
        return Response({'count': len(serializer.data), 'results': serializer.data})


# ---------------------------------------------------------------------------
# Phase 4.2 — Workshop ViewSet (enhanced from Phase 2.15 ReadOnly)
# ---------------------------------------------------------------------------

@extend_schema(tags=['Workshops'])
class WorkshopViewSet(viewsets.ModelViewSet):
    """
    GET    /api/workshops/                  — list (filterable, AllowAny)
    POST   /api/workshops/                  — create (dealer/admin)
    GET    /api/workshops/{id}/             — detail (AllowAny)
    PATCH  /api/workshops/{id}/             — update (owner/admin)
    DELETE /api/workshops/{id}/             — soft-delete (owner/admin)
    GET    /api/workshops/mine/             — current user's workshops
    GET    /api/workshops/{id}/services/    — list services
    POST   /api/workshops/{id}/services/    — add service (owner/admin)
    PATCH  /api/workshops/{id}/services/{service_id}/ — update service
    DELETE /api/workshops/{id}/services/{service_id}/ — delete service
    PUT    /api/workshops/{id}/working-hours/ — replace weekly schedule
    GET    /api/workshops/{id}/reviews/     — list approved reviews
    POST   /api/workshops/{id}/reviews/     — submit review (authenticated)
    PATCH  /api/workshops/{id}/reviews/{review_id}/ — edit own review
    DELETE /api/workshops/{id}/reviews/{review_id}/ — delete own review
    POST   /api/workshops/{id}/verify/      — admin verify
    GET    /api/workshops/nearby/           — ?lat=&lng=&radius=
    GET    /api/workshops/map-pins/         — lightweight viewport pins
    """

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = WorkshopFilter
    search_fields   = ['name', 'name_ar', 'city', 'description', 'services']
    ordering_fields = ['name', 'rating', 'average_rating', 'created_at']
    ordering        = ['name']

    def get_queryset(self):
        qs = Workshop.objects.select_related('owner', 'city_obj').prefetch_related(
            'working_hours', 'service_items',
        )
        if self.action in ('list', 'nearby', 'map_pins'):
            qs = qs.filter(is_active=True)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return WorkshopListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return WorkshopCreateUpdateSerializer
        return WorkshopDetailSerializer

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy',
                           'service_detail', 'review_detail', 'verify'):
            return [IsAuthenticated()]
        return [AllowAny()]

    def perform_create(self, serializer):
        user = self.request.user
        if getattr(user, 'role', None) not in ('importer', 'admin'):
            raise PermissionDenied('Only importers or admins can create workshops.')
        workshop = serializer.save(owner=user, is_verified=False)
        log_action(
            user=user, action='create',
            model_name='Workshop', object_id=workshop.pk,
            ip_address=get_client_ip(self.request),
        )

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.owner != self.request.user and getattr(self.request.user, 'role', None) != 'admin':
            raise PermissionDenied('Only the owner or an admin can update this workshop.')
        updated = serializer.save()
        log_action(
            user=self.request.user, action='update',
            model_name='Workshop', object_id=updated.pk,
            ip_address=get_client_ip(self.request),
        )

    def destroy(self, request, *args, **kwargs):
        workshop = self.get_object()
        if workshop.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        workshop.is_active = False
        workshop.save(update_fields=['is_active'])
        log_action(
            user=request.user, action='delete',
            model_name='Workshop', object_id=workshop.pk,
            ip_address=get_client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------ mine
    @action(detail=False, methods=['get'], url_path='mine',
            permission_classes=[IsAuthenticated])
    def mine(self, request):
        """GET /api/workshops/mine/ — returns workshops owned by the current user."""
        qs = Workshop.objects.filter(owner=request.user).select_related(
            'owner', 'city_obj'
        ).prefetch_related('working_hours', 'service_items')
        serializer = WorkshopDetailSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    # ------------------------------------------------------------------ services
    @action(detail=True, methods=['get', 'post'], url_path='services',
            permission_classes=[AllowAny])
    def services(self, request, pk=None):
        """GET/POST /api/workshops/{id}/services/"""
        workshop = self.get_object()
        if request.method == 'GET':
            qs = workshop.service_items.filter(is_active=True)
            return Response(
                WorkshopServiceSerializer(qs, many=True, context={'request': request}).data
            )
        # POST
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
        if workshop.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = WorkshopServiceSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        service = serializer.save(workshop=workshop)
        log_action(
            user=request.user, action='create',
            model_name='WorkshopService', object_id=service.pk,
            ip_address=get_client_ip(request),
        )
        return Response(
            WorkshopServiceSerializer(service, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['patch', 'delete'],
            url_path=r'services/(?P<service_id>[0-9]+)',
            permission_classes=[IsAuthenticated])
    def service_detail(self, request, pk=None, service_id=None):
        """PATCH/DELETE /api/workshops/{id}/services/{service_id}/"""
        workshop = self.get_object()
        service  = get_object_or_404(WorkshopService, pk=service_id, workshop=workshop)
        if workshop.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        if request.method == 'DELETE':
            service.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = WorkshopServiceSerializer(
            service, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # ------------------------------------------------------------------ working hours
    @action(detail=True, methods=['get', 'put'], url_path='working-hours',
            permission_classes=[AllowAny])
    def working_hours(self, request, pk=None):
        """GET/PUT /api/workshops/{id}/working-hours/"""
        workshop = self.get_object()
        if request.method == 'GET':
            qs = workshop.working_hours.all()
            return Response(
                WorkshopWorkingHoursSerializer(qs, many=True, context={'request': request}).data
            )
        # PUT
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
        if workshop.owner != request.user and getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        hours_data = (
            request.data
            if isinstance(request.data, list)
            else request.data.get('hours', [])
        )
        serializer = WorkshopWorkingHoursSerializer(
            data=hours_data, many=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        workshop.working_hours.all().delete()
        created = []
        for item in serializer.validated_data:
            wh = WorkshopWorkingHours(workshop=workshop, **item)
            try:
                wh.full_clean()
            except DjangoValidationError as exc:
                return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
            wh.save()
            created.append(wh)
        return Response(
            WorkshopWorkingHoursSerializer(created, many=True, context={'request': request}).data
        )

    # ------------------------------------------------------------------ reviews
    @action(detail=True, methods=['get', 'post'], url_path='reviews',
            permission_classes=[AllowAny])
    def reviews(self, request, pk=None):
        """GET/POST /api/workshops/{id}/reviews/"""
        workshop = self.get_object()
        if request.method == 'GET':
            qs = workshop.reviews.filter(is_approved=True).select_related('user', 'service')
            page = self.paginate_queryset(qs)
            if page is not None:
                return self.get_paginated_response(
                    WorkshopReviewSerializer(page, many=True, context={'request': request}).data
                )
            return Response(
                WorkshopReviewSerializer(qs, many=True, context={'request': request}).data
            )
        # POST
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
        if workshop.owner_id == request.user.pk:
            return Response({'detail': 'Cannot review your own workshop.'}, status=status.HTTP_400_BAD_REQUEST)
        if workshop.reviews.filter(user=request.user).exists():
            return Response({'detail': 'You have already reviewed this workshop.'}, status=status.HTTP_400_BAD_REQUEST)
        serializer = WorkshopReviewSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        review = serializer.save(workshop=workshop, user=request.user)
        log_action(
            user=request.user, action='create',
            model_name='WorkshopReview', object_id=review.pk,
            ip_address=get_client_ip(request),
        )
        from notifications.utils import notify
        if workshop.owner:
            notify(
                recipient=workshop.owner,
                notification_type='system',
                title='New Workshop Review',
                message=f'{request.user.name} left a {review.rating}★ review on {workshop.name}.',
                metadata={'workshop_id': workshop.pk, 'review_id': review.pk},
            )
        return Response(
            WorkshopReviewSerializer(review, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['patch', 'delete'],
            url_path=r'reviews/(?P<review_id>[0-9]+)',
            permission_classes=[IsAuthenticated])
    def review_detail(self, request, pk=None, review_id=None):
        """PATCH/DELETE /api/workshops/{id}/reviews/{review_id}/"""
        workshop = self.get_object()
        review   = get_object_or_404(WorkshopReview, pk=review_id, workshop=workshop)
        is_admin = getattr(request.user, 'role', None) == 'admin'
        if review.user != request.user and not is_admin:
            return Response({'detail': 'Not allowed.'}, status=status.HTTP_403_FORBIDDEN)
        if request.method == 'DELETE':
            review.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = WorkshopReviewSerializer(
            review, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # ------------------------------------------------------------------ verify
    @action(detail=True, methods=['post'], url_path='verify',
            permission_classes=[IsAuthenticated])
    def verify(self, request, pk=None):
        """POST /api/workshops/{id}/verify/ — admin only."""
        if getattr(request.user, 'role', None) != 'admin':
            return Response({'detail': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        workshop = self.get_object()
        workshop.is_verified = True
        workshop.verified_at = timezone.now()
        workshop.save(update_fields=['is_verified', 'verified_at'])
        log_action(
            user=request.user, action='approve',
            model_name='Workshop', object_id=workshop.pk,
            ip_address=get_client_ip(request),
        )
        return Response({'detail': 'Workshop verified.', 'verified_at': workshop.verified_at})

    # ------------------------------------------------------------------ nearby / map-pins
    @action(detail=False, methods=['get'], url_path='nearby')
    def nearby(self, request):
        """GET /api/workshops/nearby/?lat=&lng=&radius="""
        try:
            lat    = float(request.query_params.get('lat', ''))
            lng    = float(request.query_params.get('lng', ''))
            radius = float(request.query_params.get('radius', 30))
        except (ValueError, TypeError):
            return Response(
                {'error': 'lat and lng are required numeric parameters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        radius = min(radius, 500.0)
        box = bounding_box(lat, lng, radius)
        qs = (
            Workshop.objects
            .filter(
                is_active=True,
                latitude__isnull=False, longitude__isnull=False,
                latitude__gte=box['lat_min'], latitude__lte=box['lat_max'],
                longitude__gte=box['lng_min'], longitude__lte=box['lng_max'],
            )
            .annotate(distance_km=haversine_annotation(lat, lng))
            .filter(distance_km__lte=radius)
            .order_by('distance_km')
        )
        results = []
        for obj in qs[:100]:
            obj.distance_km = round(obj.distance_km, 1)
            results.append(obj)
        serializer = NearbyWorkshopSerializer(results, many=True, context={'request': request})
        return Response({'count': len(results), 'results': serializer.data})

    @action(detail=False, methods=['get'], url_path='map-pins')
    def map_pins(self, request):
        """GET /api/workshops/map-pins/ — viewport-filtered lightweight pins."""
        qs = Workshop.objects.filter(is_active=True, latitude__isnull=False, longitude__isnull=False)
        qs = WorkshopFilter(request.query_params, queryset=qs).qs[:200]
        serializer = WorkshopMapPinSerializer(qs, many=True, context={'request': request})
        return Response({'count': len(serializer.data), 'results': serializer.data})


# ---------------------------------------------------------------------------
# Listing Images  (nested under /api/listings/{listing_id}/images/)
# ---------------------------------------------------------------------------

@extend_schema(tags=['Listing Images'])
class ListingImageViewSet(APIView):
    """
    Nested image endpoints for a Listing.

    GET    /api/listings/{listing_id}/images/              — public
    POST   /api/listings/{listing_id}/images/              — owner or admin, multipart
    GET    /api/listings/{listing_id}/images/{pk}/         — public
    PATCH  /api/listings/{listing_id}/images/{pk}/         — owner or admin
    DELETE /api/listings/{listing_id}/images/{pk}/         — owner or admin
    """

    def _get_listing(self, listing_id):
        return get_object_or_404(Listing, pk=listing_id)

    def _is_owner_or_admin(self, listing, user):
        return listing.owner == user or getattr(user, 'role', None) == 'admin'

    # -- list ---------------------------------------------------------------

    def get(self, request, listing_id, pk=None):
        listing = self._get_listing(listing_id)
        if pk is not None:
            image = get_object_or_404(ListingImage, pk=pk, listing=listing)
            return Response(ListingImageSerializer(image).data)
        images = ListingImage.objects.filter(listing=listing).order_by('order', '-is_primary')
        return Response(ListingImageSerializer(images, many=True).data)

    # -- create -------------------------------------------------------------

    def post(self, request, listing_id, pk=None):
        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

        listing = self._get_listing(listing_id)
        if not self._is_owner_or_admin(listing, request.user):
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        # Respect subscription max_images_per_listing
        max_images = 20  # default / fallback
        try:
            from subscriptions.models import DealerSubscription
            sub = DealerSubscription.objects.select_related('plan').get(dealer=listing.owner)
            max_images = sub.plan.max_images_per_listing
        except Exception:
            pass

        current_count = ListingImage.objects.filter(listing=listing).count()
        if current_count >= max_images:
            return Response(
                {'error': f'Maximum {max_images} images per listing on your current plan.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if 'image' not in request.FILES:
            return Response({'error': 'Image file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Upload to Cloudinary
        result = cloudinary.uploader.upload(request.FILES['image'], folder='listings/')

        # Auto-assign order: max existing order + 1 (0 if first image)
        max_order = ListingImage.objects.filter(listing=listing).aggregate(m=Max('order'))['m']
        auto_order = (max_order + 1) if max_order is not None else 0
        order = int(request.data.get('order', auto_order))

        # Validate explicit order doesn't conflict
        if request.data.get('order') is not None and \
                ListingImage.objects.filter(listing=listing, order=order).exists():
            return Response(
                {'error': f"Order {order} is already taken for this listing."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # First image is automatically primary
        raw_is_primary = request.data.get('is_primary', current_count == 0)
        is_primary = raw_is_primary in (True, 'true', '1', 'True') if isinstance(raw_is_primary, str) \
            else bool(raw_is_primary)

        image = ListingImage.objects.create(
            listing=listing,
            image=result['public_id'],
            is_primary=is_primary,
            order=order,
        )
        image.refresh_from_db()

        # Trigger async compression (silently skipped if broker unavailable)
        try:
            from cars.tasks import compress_image
            compress_image.delay(image.id)
        except Exception:
            pass

        log_action(
            user=request.user,
            action='create',
            model_name='ListingImage',
            object_id=image.pk,
            old_value=None,
            new_value={'listing_id': listing.pk, 'is_primary': image.is_primary, 'order': image.order},
            ip_address=get_client_ip(request),
        )
        return Response(ListingImageSerializer(image).data, status=status.HTTP_201_CREATED)

    # -- partial_update ------------------------------------------------------

    def patch(self, request, listing_id, pk=None):
        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

        listing = self._get_listing(listing_id)
        image = get_object_or_404(ListingImage, pk=pk, listing=listing)

        if not self._is_owner_or_admin(listing, request.user):
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        if 'is_primary' in request.data:
            raw = request.data['is_primary']
            image.is_primary = raw in (True, 'true', '1', 'True') if isinstance(raw, str) else bool(raw)

        if 'order' in request.data:
            new_order = int(request.data['order'])
            if ListingImage.objects.filter(listing=listing, order=new_order).exclude(pk=image.pk).exists():
                return Response(
                    {'error': f"Order {new_order} is already taken for this listing."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            image.order = new_order

        image.save()
        return Response(ListingImageSerializer(image).data)

    # -- destroy ------------------------------------------------------------

    def delete(self, request, listing_id, pk=None):
        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)

        listing = self._get_listing(listing_id)
        image = get_object_or_404(ListingImage, pk=pk, listing=listing)

        if not self._is_owner_or_admin(listing, request.user):
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        was_primary = image.is_primary
        image_id = image.pk
        public_id = image.image.public_id if image.image else None

        image.delete()

        # Promote next image to primary if the deleted one was primary
        if was_primary:
            next_image = ListingImage.objects.filter(listing=listing).order_by('order').first()
            if next_image:
                next_image.is_primary = True
                next_image.save(update_fields=['is_primary'])

        # Remove from Cloudinary (best-effort)
        if public_id:
            try:
                cloudinary.uploader.destroy(public_id)
            except Exception:
                pass

        log_action(
            user=request.user,
            action='delete',
            model_name='ListingImage',
            object_id=image_id,
            old_value={'listing_id': listing.pk, 'was_primary': was_primary},
            new_value=None,
            ip_address=get_client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Phase 2.9 — Saved Searches
# ---------------------------------------------------------------------------

@extend_schema(tags=['Saved Searches'])
class SavedSearchViewSet(viewsets.ModelViewSet):
    """
    CRUD for SavedSearch.

    GET    /api/saved-searches/          — list own saved searches
    POST   /api/saved-searches/          — create (max 20 per user)
    GET    /api/saved-searches/{id}/     — retrieve
    PATCH  /api/saved-searches/{id}/     — update name/filters/notify
    DELETE /api/saved-searches/{id}/     — destroy
    GET    /api/saved-searches/{id}/results/ — run filters, return listings
    """
    serializer_class = SavedSearchSerializer
    permission_classes = [IsAuthenticated]
    # Explicitly set queryset so DRF router can introspect it;
    # the actual data is always filtered to request.user in get_queryset().
    queryset = SavedSearch.objects.none()

    def get_queryset(self):
        return SavedSearch.objects.filter(user=self.request.user).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        if SavedSearch.objects.filter(user=request.user).count() >= 20:
            return Response(
                {'error': 'Maximum 20 saved searches allowed per user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        instance = serializer.save(user=self.request.user)
        log_action(
            user=self.request.user,
            action='create',
            model_name='SavedSearch',
            object_id=instance.pk,
            old_value=None,
            new_value={'name': instance.name, 'filters': instance.filters},
            ip_address=get_client_ip(self.request),
        )

    def perform_destroy(self, instance):
        log_action(
            user=self.request.user,
            action='delete',
            model_name='SavedSearch',
            object_id=instance.pk,
            old_value={'name': instance.name, 'filters': instance.filters},
            new_value=None,
            ip_address=get_client_ip(self.request),
        )
        instance.delete()

    @action(detail=True, methods=['get'], url_path='results')
    def results(self, request, pk=None):
        """
        GET /api/saved-searches/{id}/results/
        Re-runs the saved filter dict against current approved listings.
        Returns paginated ListingSerializer output.
        """
        saved_search = self.get_object()
        base_qs = (
            Listing.objects
            .filter(status='approved', is_active=True)
            .select_related('owner', 'showroom', 'workshop')
            .prefetch_related('images')
        )
        filtered_qs = ListingFilter(saved_search.filters, queryset=base_qs).qs

        page = self.paginate_queryset(filtered_qs)
        if page is not None:
            serializer = ListingSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)
        serializer = ListingSerializer(filtered_qs, many=True, context={'request': request})
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Phase 2.9 — Recently Viewed
# ---------------------------------------------------------------------------

@extend_schema(tags=['Saved Searches'])
class RecentlyViewedView(generics.GenericAPIView):
    """
    GET    /api/recently-viewed/  — paginated list ordered by -viewed_at
    DELETE /api/recently-viewed/  — clear all entries for this user
    Auth: IsAuthenticated
    """
    permission_classes = [IsAuthenticated]
    serializer_class = RecentlyViewedSerializer

    def get_queryset(self):
        return (
            RecentlyViewed.objects
            .filter(user=self.request.user)
            .select_related(
                'listing', 'listing__owner',
                'listing__showroom', 'listing__workshop',
            )
            .prefetch_related('listing__images')
            .order_by('-viewed_at')
        )

    def get(self, request):
        qs = self.get_queryset()
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    def delete(self, request):
        self.get_queryset().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Saved Searches'])
class RecentlyViewedDetailView(APIView):
    """
    DELETE /api/recently-viewed/{id}/ — remove a single entry
    Auth: IsAuthenticated
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        rv = get_object_or_404(RecentlyViewed, pk=pk, user=request.user)
        rv.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Phase 4.5 — Bulk Operations
# ---------------------------------------------------------------------------

# CSV column headers for the template download
BULK_TEMPLATE_HEADERS = [
    'title', 'make', 'model', 'year', 'price', 'mileage', 'city',
    'fuel_type', 'transmission', 'condition', 'body_type',
    'description', 'color', 'vin',
]

BULK_TEMPLATE_EXAMPLE = [
    '2022 Toyota Camry', 'Toyota', 'Camry', '2022', '95000', '20000', 'Riyadh',
    'petrol', 'automatic', 'used', 'sedan',
    'Very clean, single owner', 'White', '',
]


@extend_schema(tags=['Bulk Operations'])
class BulkTemplateView(APIView):
    """
    GET /api/listings/bulk/template/
    Download a blank CSV template that dealers can fill in.
    Auth: dealer or admin only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        import csv
        import io
        from django.http import HttpResponse

        if request.user.role not in ('importer', 'admin'):
            return Response(
                {'detail': 'Only importers and admins can access this endpoint.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(BULK_TEMPLATE_HEADERS)
        writer.writerow(BULK_TEMPLATE_EXAMPLE)

        response = HttpResponse(output.getvalue(), content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="bulk_listing_template.csv"'
        return response


@extend_schema(tags=['Bulk Operations'])
class BulkUploadCreateView(APIView):
    """
    POST /api/listings/bulk/upload/
    Upload a CSV file.  Requires Pro plan (can_bulk_upload=True).
    Auth: dealer or admin only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role not in ('importer', 'admin'):
            return Response(
                {'detail': 'Only importers and admins can upload bulk listings.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # --- Subscription gate ---
        try:
            from subscriptions.models import DealerSubscription
            sub = DealerSubscription.objects.select_related('plan').get(
                dealer=request.user, is_active=True,
            )
            if not sub.plan.can_bulk_upload:
                return Response(
                    {'detail': 'Bulk upload requires a Pro plan. Please upgrade your subscription.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        except Exception:
            # If subscriptions app isn't set up or dealer has no sub, block by default
            return Response(
                {'detail': 'Bulk upload requires a Pro plan. Please upgrade your subscription.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BulkUploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        uploaded_file = serializer.validated_data['file']

        bulk = BulkUpload.objects.create(
            dealer=request.user,
            file_name=uploaded_file.name,
            file=uploaded_file,
            status='pending',
        )

        # Dispatch Celery task; fall back to synchronous if Celery unavailable
        try:
            from cars.tasks import process_bulk_upload
            process_bulk_upload.delay(bulk.pk)
        except Exception:
            from cars.tasks import process_bulk_upload
            process_bulk_upload(bulk.pk)

        log_action(
            user=request.user,
            action='create',
            model_name='BulkUpload',
            object_id=bulk.pk,
            ip_address=get_client_ip(request),
        )

        return Response(BulkUploadSerializer(bulk).data, status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=['Bulk Operations'])
class BulkUploadListView(APIView):
    """
    GET /api/listings/bulk/uploads/
    List the authenticated dealer's previous bulk uploads (newest first).
    Auth: dealer or admin only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ('importer', 'admin'):
            return Response(
                {'detail': 'Only importers and admins can view bulk uploads.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        qs = BulkUpload.objects.filter(dealer=request.user).order_by('-created_at')
        return Response(BulkUploadSerializer(qs, many=True).data)


@extend_schema(tags=['Bulk Operations'])
class BulkUploadDetailView(APIView):
    """
    GET /api/listings/bulk/uploads/{id}/
    Auth: owner or admin only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if request.user.role == 'admin':
            bulk = get_object_or_404(BulkUpload, pk=pk)
        else:
            bulk = get_object_or_404(BulkUpload, pk=pk, dealer=request.user)
        return Response(BulkUploadSerializer(bulk).data)


@extend_schema(tags=['Bulk Operations'])
class BulkStatusChangeView(APIView):
    """
    POST /api/listings/bulk/status/
    Change status for up to 50 of the dealer's own listings.
    Admin can change any listing.
    Auth: dealer or admin only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role not in ('importer', 'admin'):
            return Response(
                {'detail': 'Only importers and admins can bulk-change status.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BulkStatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        listing_ids = serializer.validated_data['listing_ids']
        new_status  = serializer.validated_data['status']

        # SECURITY: approval-status changes are admin-only.
        # 'sold' is included: cars are marked Sold AUTOMATICALLY when WARED
        # confirms the buyer's full payment — importers never set it by hand.
        ADMIN_ONLY_STATUSES = {'approved', 'rejected', 'changes_requested', 'pending', 'sold'}
        is_admin = request.user.role == 'admin'

        if not is_admin and new_status in ADMIN_ONLY_STATUSES:
            return Response(
                {'detail': 'Only admins can change this status. Cars are marked Sold '
                           'automatically when WARED confirms the full payment.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if is_admin:
            qs = Listing.objects.filter(pk__in=listing_ids, is_active=True)
        else:
            qs = Listing.objects.filter(
                pk__in=listing_ids, owner=request.user, is_active=True,
            )

        updated_ids   = []
        skipped_ids   = []
        not_found_ids = set(listing_ids) - set(qs.values_list('pk', flat=True))

        for listing in qs.select_for_update():
            old_status = listing.status
            listing.status = new_status
            try:
                listing.full_clean()
                listing.save(update_fields=['status'])
                updated_ids.append(listing.pk)
                log_action(
                    user=request.user,
                    action='status_change',
                    model_name='Listing',
                    object_id=listing.pk,
                    old_value={'status': old_status},
                    new_value={'status': new_status},
                    ip_address=get_client_ip(request),
                )
            except Exception:
                listing.status = old_status  # rollback in-memory
                skipped_ids.append(listing.pk)

        return Response({
            'updated': updated_ids,
            'skipped': skipped_ids,
            'not_found': list(not_found_ids),
        })


@extend_schema(tags=['Bulk Operations'])
class BulkDeleteView(APIView):
    """
    POST /api/listings/bulk/delete/
    Soft-delete up to 50 of the dealer's own listings.
    Admin can delete any listing.
    Auth: dealer or admin only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role not in ('importer', 'admin'):
            return Response(
                {'detail': 'Only importers and admins can bulk-delete listings.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BulkDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        listing_ids = serializer.validated_data['listing_ids']

        if request.user.role == 'admin':
            qs = Listing.objects.filter(pk__in=listing_ids, is_active=True)
            skipped_in_deal = []
        else:
            qs = Listing.objects.filter(
                pk__in=listing_ids, owner=request.user, is_active=True,
            )
            # Importers cannot delete cars that are part of an active deal
            from .visibility import ACTIVE_ORDER_STATUSES
            in_deal_qs = qs.filter(import_orders__status__in=ACTIVE_ORDER_STATUSES)
            skipped_in_deal = list(in_deal_qs.values_list('pk', flat=True).distinct())
            qs = qs.exclude(pk__in=skipped_in_deal)

        deleted_ids   = list(qs.values_list('pk', flat=True))
        not_found_ids = set(listing_ids) - set(deleted_ids) - set(skipped_in_deal)

        qs.update(is_active=False)

        for pk in deleted_ids:
            log_action(
                user=request.user,
                action='delete',
                model_name='Listing',
                object_id=pk,
                ip_address=get_client_ip(request),
            )

        return Response({
            'deleted':   deleted_ids,
            'skipped_active_deal': skipped_in_deal,
            'not_found': list(not_found_ids),
        })


@extend_schema(tags=['Bulk Operations'])
class BulkExportView(APIView):
    """
    GET /api/listings/bulk/export/?format=csv|xlsx
    Export the dealer's active listings as CSV or XLSX.
    Auth: dealer or admin only.
    """
    permission_classes = [IsAuthenticated]

    EXPORT_FIELDS = [
        'id', 'title', 'make', 'model', 'year', 'price', 'mileage',
        'city', 'fuel_type', 'transmission', 'condition', 'body_type',
        'description', 'color', 'vin', 'status', 'created_at',
    ]

    def get(self, request):
        import csv
        import io
        from django.http import HttpResponse, StreamingHttpResponse

        if request.user.role not in ('importer', 'admin'):
            return Response(
                {'detail': 'Only importers and admins can export listings.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        fmt = request.query_params.get('format', 'csv').lower()
        if fmt not in ('csv', 'xlsx'):
            return Response(
                {'detail': "format must be 'csv' or 'xlsx'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.user.role == 'admin':
            qs = Listing.objects.filter(is_active=True).values(*self.EXPORT_FIELDS)
        else:
            qs = Listing.objects.filter(
                owner=request.user, is_active=True,
            ).values(*self.EXPORT_FIELDS)

        # ---- CSV ----
        if fmt == 'csv':
            def _rows():
                yield ','.join(self.EXPORT_FIELDS) + '\n'
                for row in qs.iterator(chunk_size=500):
                    yield ','.join(
                        f'"{str(row[f]).replace(chr(34), chr(34)+chr(34))}"'
                        for f in self.EXPORT_FIELDS
                    ) + '\n'

            response = StreamingHttpResponse(_rows(), content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="listings_export.csv"'
            return response

        # ---- XLSX ----
        try:
            import openpyxl
        except ImportError:
            return Response(
                {'detail': 'XLSX export is not available. Install openpyxl.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Listings'
        ws.append(self.EXPORT_FIELDS)

        for row in qs.iterator(chunk_size=500):
            ws.append([str(row[f]) if row[f] is not None else '' for f in self.EXPORT_FIELDS])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        response = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename="listings_export.xlsx"'
        return response


# ---------------------------------------------------------------------------
# Import-specific public views
# ---------------------------------------------------------------------------

class ImportedCarListView(generics.ListAPIView):
    """
    GET /api/imported-cars/ — publicly available imported cars.
    Supports: ?search=, ?make=, ?year_min=, ?year_max=, ?price_min=, ?price_max=,
              ?source_country=, ?body_type=, ?spec_origin=, ?sort=
    """
    permission_classes = [AllowAny]
    serializer_class   = ListingSerializer
    filter_backends    = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class    = ListingFilter
    search_fields      = ['make', 'model', 'title', 'source_country', 'spec_origin']
    ordering_fields    = ['price', 'year', 'mileage', 'created_at', 'final_price_sar', 'estimated_arrival_date']
    # Paid promotions come first: Top-of-Search pins, then promotion priority,
    # then newest. Explicit ?ordering=/?sort= from the client still overrides.
    ordering           = ['-is_top_search', '-promotion_priority', '-created_at']

    SORT_MAP = {
        'newest': '-created_at',
        'price_low': 'final_price_sar',
        'price_high': '-final_price_sar',
        'arriving_soon': 'estimated_arrival_date',
    }

    def get_queryset(self):
        # Public browse: strictly on-market cars only.
        # Reserved/sold cars, and cars in an active deal, are private to the
        # buyer / importer / admin — they never appear here.
        qs = Listing.objects.filter(
            public_market_q()
        ).select_related('owner', 'city_obj').prefetch_related('images').distinct()

        params = self.request.query_params

        # Make filter (comma-separated)
        make = params.get('make')
        if make:
            qs = qs.filter(make__in=[m.strip() for m in make.split(',')])

        # Year range
        year_min = params.get('year_min')
        year_max = params.get('year_max')
        if year_min:
            qs = qs.filter(year__gte=int(year_min))
        if year_max:
            qs = qs.filter(year__lte=int(year_max))

        # Price range (SAR)
        price_min = params.get('price_min')
        price_max = params.get('price_max')
        if price_min:
            qs = qs.filter(final_price_sar__gte=price_min)
        if price_max:
            qs = qs.filter(final_price_sar__lte=price_max)

        # Source country (comma-separated)
        source_country = params.get('source_country')
        if source_country:
            qs = qs.filter(source_country__in=[s.strip() for s in source_country.split(',')])

        # Body type (comma-separated)
        body_type = params.get('body_type')
        if body_type:
            qs = qs.filter(body_type__in=[b.strip() for b in body_type.split(',')])

        # Spec origin (comma-separated)
        spec_origin = params.get('spec_origin')
        if spec_origin:
            qs = qs.filter(spec_origin__in=[s.strip() for s in spec_origin.split(',')])

        # Sort
        sort = params.get('sort')
        if sort and sort in self.SORT_MAP:
            qs = qs.order_by(self.SORT_MAP[sort])

        return qs


class ImportedCarFilterOptionsView(APIView):
    """GET /api/imported-cars/filter-options/ — available filter values."""
    permission_classes = [AllowAny]

    def get(self, request):
        qs = Listing.objects.filter(public_market_q()).distinct()
        makes = sorted(set(qs.values_list('make', flat=True).distinct()))
        body_types = sorted(set(qs.exclude(body_type='').values_list('body_type', flat=True).distinct()))
        years = qs.aggregate(min_year=Min('year'), max_year=Max('year'))
        prices = qs.exclude(final_price_sar__isnull=True).aggregate(
            min_price=Min('final_price_sar'),
            max_price=Max('final_price_sar'),
        )
        return Response({
            'makes': [m for m in makes if m],
            'body_types': [b for b in body_types if b],
            'year_range': {
                'min': years.get('min_year') or 2018,
                'max': years.get('max_year') or 2025,
            },
            'price_range_sar': {
                'min': float(prices.get('min_price') or 0),
                'max': float(prices.get('max_price') or 1000000),
            },
        })


class ImportedCarDetailView(generics.RetrieveAPIView):
    """
    GET /api/imported-cars/{id}/ — single imported car detail, publicly accessible.
    Only shows approved, active listings (same rule as the list endpoint).
    """
    permission_classes = [AllowAny]
    serializer_class   = ListingSerializer

    def get_queryset(self):
        # Public cars for everyone; reserved/sold/deal cars stay reachable
        # ONLY for the parties involved (owner, buyer with an order, admin).
        base = Listing.objects.filter(
            is_active=True,
            status='approved',
        ).select_related('owner', 'city_obj').prefetch_related('images')
        return detail_queryset(base, self.request.user)


class ImportedCarArrivingView(generics.ListAPIView):
    """
    GET /api/imported-cars/arriving/ — cars currently being shipped (coming soon).
    """
    permission_classes = [AllowAny]
    serializer_class   = ListingSerializer
    ordering           = ['estimated_arrival_date']

    def get_queryset(self):
        # "Coming soon" stock only — cars in a private deal are excluded
        # (their shipping progress belongs to the buyer's order page).
        return Listing.objects.filter(
            public_market_q(),
            import_status__in=['shipping', 'at_port', 'in_customs', 'customs_cleared'],
        ).select_related('owner', 'city_obj').prefetch_related('images').distinct()


# ---------------------------------------------------------------------------
# Promotion payment verification (WARED admin)
# ---------------------------------------------------------------------------

_PROMO_TYPE_TO_FLAG = {
    'featured':    'is_featured',
    'highlighted': 'is_highlighted',
    'top_search':  'is_top_search',
    'homepage':    'is_homepage',
}


def _activate_promotion(promotion):
    """Mark a promotion active and apply its visibility flags to the listing.
    The full package duration starts NOW (payment confirmation time)."""
    now = timezone.now()
    expires_at = now + timedelta(days=promotion.package.duration_days)
    promotion.status = 'active'
    promotion.expires_at = expires_at
    promotion.save(update_fields=['status', 'expires_at'])

    listing = promotion.listing
    flag_field = _PROMO_TYPE_TO_FLAG.get(promotion.package.promotion_type)
    update_fields = ['promotion_priority', 'promotion_expires_at']
    if flag_field:
        setattr(listing, flag_field, True)
        update_fields.append(flag_field)
    listing.promotion_priority = max(listing.promotion_priority, promotion.package.priority)
    if listing.promotion_expires_at is None or expires_at > listing.promotion_expires_at:
        listing.promotion_expires_at = expires_at
    listing.save(update_fields=update_fields)
    return expires_at


class AdminConfirmPromotionPaymentView(APIView):
    """POST /api/admin/promotion-payments/{txn_id}/confirm/ — admin verified
    the importer's bank transfer; the promotion goes live immediately."""
    permission_classes = [IsAuthenticated]

    def post(self, request, txn_id):
        if not (request.user.is_staff or getattr(request.user, 'role', '') == 'admin'):
            return Response({'detail': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)

        from payments.models import PaymentTransaction
        try:
            txn = PaymentTransaction.objects.select_related(
                'promotion', 'promotion__package', 'promotion__listing', 'user',
            ).get(pk=txn_id, payment_type='promotion')
        except PaymentTransaction.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if txn.status != 'pending':
            return Response({'detail': f'This payment is already {txn.status}.'},
                            status=status.HTTP_400_BAD_REQUEST)
        promotion = txn.promotion
        if promotion is None:
            return Response({'detail': 'No promotion attached to this payment.'},
                            status=status.HTTP_400_BAD_REQUEST)

        txn.status = 'succeeded'
        txn.save(update_fields=['status', 'updated_at'])
        expires_at = _activate_promotion(promotion)

        try:
            from notifications.utils import notify
            notify(
                recipient=promotion.dealer,
                notification_type='listing_approved',
                title='Promotion activated',
                message=(
                    f'Payment confirmed — "{promotion.package.name}" is now live on '
                    f'"{promotion.listing.title}" until {expires_at.strftime("%Y-%m-%d")}.'
                ),
                listing=promotion.listing,
            )
        except Exception:
            pass

        return Response({'detail': 'Payment confirmed — promotion is now active.',
                         'expires_at': expires_at.isoformat()})


class AdminRejectPromotionPaymentView(APIView):
    """POST /api/admin/promotion-payments/{txn_id}/reject/ — transfer could not
    be matched; the promotion is cancelled and the importer is told to retry."""
    permission_classes = [IsAuthenticated]

    def post(self, request, txn_id):
        if not (request.user.is_staff or getattr(request.user, 'role', '') == 'admin'):
            return Response({'detail': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)

        from payments.models import PaymentTransaction
        try:
            txn = PaymentTransaction.objects.select_related(
                'promotion', 'promotion__package', 'promotion__listing',
            ).get(pk=txn_id, payment_type='promotion')
        except PaymentTransaction.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if txn.status != 'pending':
            return Response({'detail': f'This payment is already {txn.status}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        reason = (request.data.get('reason') or 'Transfer could not be verified.').strip()
        txn.status = 'failed'
        txn.error_message = reason
        txn.save(update_fields=['status', 'error_message', 'updated_at'])
        if txn.promotion and txn.promotion.status == 'pending_payment':
            txn.promotion.status = 'cancelled'
            txn.promotion.save(update_fields=['status'])

        try:
            from notifications.utils import notify
            notify(
                recipient=txn.user,
                notification_type='system',
                title='Promotion payment issue',
                message=f'{reason} Please re-check your bank transfer and boost the listing again.',
            )
        except Exception:
            pass

        return Response({'detail': 'Payment rejected — the importer has been notified.'})

import logging
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from auditlog.utils import get_client_ip, log_action
from notifications.utils import notify
from .models import Review, ReviewReply
from .serializers import (
    AdminReviewModerateSerializer,
    AdminReviewSerializer,
    ImporterOrderReviewSerializer,
    ReviewCreateSerializer,
    ReviewReplySerializer,
    ReviewSerializer,
    ReviewUpdateSerializer,
)

logger = logging.getLogger(__name__)


class IsAdminRole(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and
            (request.user.is_staff or getattr(request.user, 'role', '') == 'admin')
        )


# ---------------------------------------------------------------------------
# User-facing
# ---------------------------------------------------------------------------

class ReviewListCreateView(APIView):
    """
    GET  /api/reviews/ — list approved reviews (filters: reviewed_user, listing, review_type, min_rating)
    POST /api/reviews/ — submit a new review
    """
    def get_permissions(self):
        if self.request.method == 'POST':
            return [permissions.IsAuthenticated()]
        return [permissions.AllowAny()]

    def get(self, request):
        qs = Review.objects.filter(status='approved').select_related(
            'reviewer', 'reviewed_user', 'listing', 'reply__author'
        ).order_by('-created_at')

        if uid := request.query_params.get('reviewed_user'):
            qs = qs.filter(reviewed_user_id=uid)
        if lid := request.query_params.get('listing'):
            qs = qs.filter(listing_id=lid)
        if rt := request.query_params.get('review_type'):
            qs = qs.filter(review_type=rt)
        if mr := request.query_params.get('min_rating'):
            try:
                qs = qs.filter(rating__gte=int(mr))
            except ValueError:
                pass
        if sid := request.query_params.get('showroom'):
            qs = qs.filter(showroom_id=sid)
        if wid := request.query_params.get('workshop'):
            qs = qs.filter(workshop_id=wid)

        return Response(ReviewSerializer(qs, many=True, context={'request': request}).data)

    def post(self, request):
        serializer = ReviewCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        review = serializer.save()

        # Notify reviewed user
        notify(
            recipient=review.reviewed_user,
            notification_type='system',
            title='You received a new review',
            message=f"{request.user.name} left you a {review.rating}-star review.",
        )

        log_action(
            user=request.user,
            action='create',
            model_name='Review',
            object_id=review.id,
            new_value={'rating': review.rating, 'review_type': review.review_type},
            ip_address=get_client_ip(request),
        )

        return Response(ReviewSerializer(review, context={'request': request}).data, status=status.HTTP_201_CREATED)


class ReviewDetailView(APIView):
    """
    GET    /api/reviews/{id}/ — review detail
    PUT    /api/reviews/{id}/ — edit own review (within 48h)
    DELETE /api/reviews/{id}/ — delete own review
    """
    def _get_review(self, pk):
        try:
            return Review.objects.select_related(
                'reviewer', 'reviewed_user', 'listing', 'reply__author'
            ).get(pk=pk)
        except Review.DoesNotExist:
            return None

    def get(self, request, pk):
        review = self._get_review(pk)
        if not review:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ReviewSerializer(review, context={'request': request}).data)

    def put(self, request, pk):
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
        review = self._get_review(pk)
        if not review:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if review.reviewer_id != request.user.id:
            return Response({'detail': 'You can only edit your own reviews.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ReviewUpdateSerializer(review, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ReviewSerializer(review, context={'request': request}).data)

    def delete(self, request, pk):
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
        review = self._get_review(pk)
        if not review:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if review.reviewer_id != request.user.id:
            return Response({'detail': 'You can only delete your own reviews.'}, status=status.HTTP_403_FORBIDDEN)
        review.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReviewReplyView(APIView):
    """POST /api/reviews/{id}/reply/ — reviewed_user replies."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            review = Review.objects.select_related('reviewed_user').get(pk=pk)
        except Review.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if review.reviewed_user_id != request.user.id:
            return Response({'detail': 'Only the reviewed user can reply.'}, status=status.HTTP_403_FORBIDDEN)

        if hasattr(review, 'reply'):
            return Response({'detail': 'A reply already exists for this review.'}, status=status.HTTP_400_BAD_REQUEST)

        comment = request.data.get('comment', '')
        if not comment:
            return Response({'detail': 'Comment is required.'}, status=status.HTTP_400_BAD_REQUEST)

        reply = ReviewReply.objects.create(
            review=review,
            author=request.user,
            comment=comment,
        )

        # Notify the reviewer
        notify(
            recipient=review.reviewer,
            notification_type='system',
            title='Your review received a reply',
            message=f"{request.user.name} replied to your review.",
        )

        return Response(ReviewReplySerializer(reply).data, status=status.HTTP_201_CREATED)


class MyReviewsView(APIView):
    """GET /api/reviews/mine/ — reviews I've written."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = Review.objects.filter(reviewer=request.user).select_related(
            'reviewed_user', 'listing', 'reply__author'
        ).order_by('-created_at')
        return Response(ReviewSerializer(qs, many=True, context={'request': request}).data)


class ReviewsAboutMeView(APIView):
    """GET /api/reviews/about-me/ — reviews about me."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = Review.objects.filter(
            reviewed_user=request.user, status='approved'
        ).select_related('reviewer', 'listing', 'reply__author').order_by('-created_at')
        return Response(ReviewSerializer(qs, many=True, context={'request': request}).data)


class UserReviewsView(APIView):
    """GET /api/users/{id}/reviews/ — public reviews for a user."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, user_id):
        qs = Review.objects.filter(
            reviewed_user_id=user_id, status='approved'
        ).select_related('reviewer', 'listing', 'reply__author').order_by('-created_at')
        return Response(ReviewSerializer(qs, many=True, context={'request': request}).data)


class ShowroomReviewsView(APIView):
    """GET /api/showrooms/{id}/reviews/ — showroom reviews."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, showroom_id):
        qs = Review.objects.filter(
            showroom_id=showroom_id, status='approved'
        ).select_related('reviewer', 'reply__author').order_by('-created_at')
        return Response(ReviewSerializer(qs, many=True, context={'request': request}).data)


class WorkshopReviewsView(APIView):
    """GET /api/workshops/{id}/reviews/ — workshop reviews."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, workshop_id):
        qs = Review.objects.filter(
            workshop_id=workshop_id, status='approved'
        ).select_related('reviewer', 'reply__author').order_by('-created_at')
        return Response(ReviewSerializer(qs, many=True, context={'request': request}).data)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

class AdminReviewListView(ListAPIView):
    """GET /api/admin/reviews/ — all reviews, filterable."""
    permission_classes = [IsAdminRole]
    serializer_class   = AdminReviewSerializer

    def get_queryset(self):
        qs = Review.objects.select_related('reviewer', 'reviewed_user', 'listing', 'reply__author')
        p  = self.request.query_params
        if s := p.get('status'):
            qs = qs.filter(status=s)
        if rt := p.get('review_type'):
            qs = qs.filter(review_type=rt)
        if mr := p.get('min_rating'):
            try: qs = qs.filter(rating__gte=int(mr))
            except ValueError: pass
        if xr := p.get('max_rating'):
            try: qs = qs.filter(rating__lte=int(xr))
            except ValueError: pass
        if df := p.get('date_from'):
            qs = qs.filter(created_at__date__gte=df)
        if dt := p.get('date_to'):
            qs = qs.filter(created_at__date__lte=dt)
        return qs.order_by('-created_at')


class AdminReviewDetailView(APIView):
    """PATCH /api/admin/reviews/{id}/ — moderate a review."""
    permission_classes = [IsAdminRole]

    def patch(self, request, pk):
        try:
            review = Review.objects.select_related('reviewer', 'reviewed_user', 'listing').get(pk=pk)
        except Review.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = AdminReviewModerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        old_status    = review.status
        review.status = d['status']
        if 'admin_notes' in d:
            review.admin_notes = d.get('admin_notes', '')
        review.save(update_fields=['status', 'admin_notes', 'updated_at'])

        log_action(
            user=request.user,
            action='status_change',
            model_name='Review',
            object_id=review.id,
            old_value={'status': old_status},
            new_value={'status': review.status},
            ip_address=get_client_ip(request),
        )

        return Response(AdminReviewSerializer(review).data)


class AdminReviewStatsView(APIView):
    """GET /api/admin/reviews/stats/"""
    permission_classes = [IsAdminRole]

    def get(self, request):
        now   = timezone.now()
        week  = now - timedelta(days=7)
        month = now - timedelta(days=30)

        by_status = dict(
            Review.objects.values('status').annotate(c=Count('id')).values_list('status', 'c')
        )
        by_type = dict(
            Review.objects.values('review_type').annotate(c=Count('id')).values_list('review_type', 'c')
        )
        avg = Review.objects.filter(status='approved').aggregate(avg=Avg('rating'))['avg'] or 0

        return Response({
            'total':            Review.objects.count(),
            'by_status':        by_status,
            'by_type':          by_type,
            'platform_average': round(float(avg), 2),
            'this_week':        Review.objects.filter(created_at__gte=week).count(),
            'this_month':       Review.objects.filter(created_at__gte=month).count(),
            'pending_count':    by_status.get('pending', 0),
            'flagged_count':    by_status.get('flagged', 0),
        })


# ---------------------------------------------------------------------------
# Importer Order Review
# ---------------------------------------------------------------------------

class OrderReviewView(APIView):
    """
    POST /api/orders/{order_id}/review/ — submit a 4-dimension importer review
    GET  /api/orders/{order_id}/review/ — retrieve the review for this order
    """
    permission_classes = [permissions.IsAuthenticated]

    def _get_order(self, order_id, user):
        from orders.models import ImportOrder
        try:
            return ImportOrder.objects.select_related('buyer', 'importer').get(pk=order_id)
        except ImportOrder.DoesNotExist:
            return None

    def get(self, request, order_id):
        order = self._get_order(order_id, request.user)
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Only buyer, importer, or admin may read
        is_admin = request.user.is_staff or getattr(request.user, 'role', '') == 'admin'
        if (
            order.buyer_id != request.user.id
            and order.importer_id != request.user.id
            and not is_admin
        ):
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            review = order.review
        except Review.DoesNotExist:
            return Response({'detail': 'No review for this order yet.'}, status=status.HTTP_404_NOT_FOUND)

        return Response(ReviewSerializer(review, context={'request': request}).data)

    def post(self, request, order_id):
        order = self._get_order(order_id, request.user)
        if not order:
            return Response({'detail': 'Order not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = ImporterOrderReviewSerializer(
            data=request.data,
            context={'request': request, 'order': order},
        )
        serializer.is_valid(raise_exception=True)
        review = serializer.save()

        # Notify the importer
        if order.importer:
            notify(
                recipient=order.importer,
                notification_type='system',
                title='You received a new order review',
                message=(
                    f"{request.user.name} left you a {review.rating}-star review "
                    f"for order {order.order_number}."
                ),
            )

        log_action(
            user=request.user,
            action='create',
            model_name='Review',
            object_id=review.id,
            new_value={
                'rating':      review.rating,
                'review_type': review.review_type,
                'order_id':    order.id,
            },
            ip_address=get_client_ip(request),
        )

        return Response(
            ReviewSerializer(review, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class ImporterReviewsView(APIView):
    """
    GET /api/importers/{importer_id}/reviews/
    Returns all approved importer_order reviews for a given importer user,
    with per-dimension average breakdown.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, importer_id):
        qs = Review.objects.filter(
            reviewed_user_id=importer_id,
            review_type='importer_order',
            status='approved',
        ).select_related('reviewer', 'order', 'reply__author').order_by('-created_at')

        agg = qs.aggregate(
            avg_rating=Avg('rating'),
            avg_communication=Avg('communication_rating'),
            avg_accuracy=Avg('accuracy_rating'),
            avg_delivery_speed=Avg('delivery_speed_rating'),
            avg_overall=Avg('overall_rating'),
            total=Count('id'),
        )

        return Response({
            'summary': {
                'total_reviews':          agg['total'] or 0,
                'average_rating':         round(float(agg['avg_rating'] or 0), 2),
                'avg_communication':      round(float(agg['avg_communication'] or 0), 2),
                'avg_accuracy':           round(float(agg['avg_accuracy'] or 0), 2),
                'avg_delivery_speed':     round(float(agg['avg_delivery_speed'] or 0), 2),
                'avg_overall':            round(float(agg['avg_overall'] or 0), 2),
            },
            'reviews': ReviewSerializer(qs, many=True, context={'request': request}).data,
        })

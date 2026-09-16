from django.db.models import Count, Prefetch, Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import ListCreateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from auditlog.utils import get_client_ip, log_action

from .models import CommentReport, ListingComment, ListingLike
from .serializers import (
    CommentReportSerializer,
    ListingCommentCreateSerializer,
    ListingCommentSerializer,
)
from .throttles import CommentRateThrottle, LikeRateThrottle, ReportRateThrottle
from .visibility import get_public_listing_or_404


class CommentPagination(PageNumberPagination):
    page_size = 20


def _visible_replies_queryset():
    """One level, oldest first — the order a conversation is read in."""
    return (
        ListingComment.objects
        .filter(is_deleted=False, is_hidden=False)
        .select_related('user')
        .order_by('created_at')
    )


# ---------------------------------------------------------------------------
# Likes
# ---------------------------------------------------------------------------

@extend_schema(tags=['Social'])
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([LikeRateThrottle])
def toggle_like(request, listing_id):
    """
    POST /api/listings/{id}/like/ → {"liked": bool, "like_count": int}

    Idempotent-safe in both directions: a double tap from a flaky connection
    settles on one row via get_or_create, and a second delete is a no-op.
    """
    listing = get_public_listing_or_404(listing_id, request.user)

    like, created = ListingLike.objects.get_or_create(user=request.user, listing=listing)
    if not created:
        like.delete()

    return Response(
        {
            'liked': created,
            'like_count': ListingLike.objects.filter(listing=listing).count(),
        },
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@extend_schema(tags=['Social'])
class ListingCommentListCreateView(ListCreateAPIView):
    """
    GET  /api/listings/{id}/comments/  — anyone, paginated, newest first
    POST /api/listings/{id}/comments/  — authenticated; `parent` makes it a reply
    """

    pagination_class = CommentPagination
    permission_classes = [AllowAny]

    def get_throttles(self):
        # Reading is free; only writing is rated.
        if self.request.method == 'POST':
            return [CommentRateThrottle()]
        return []

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated()]
        return [AllowAny()]

    def get_serializer_class(self):
        return ListingCommentCreateSerializer if self.request.method == 'POST' \
            else ListingCommentSerializer

    def get_listing(self):
        if not hasattr(self, '_listing'):
            self._listing = get_public_listing_or_404(
                self.kwargs['listing_id'], self.request.user,
            )
        return self._listing

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['listing'] = self.get_listing()
        return context

    def get_queryset(self):
        listing = self.get_listing()
        user = self.request.user

        queryset = (
            ListingComment.objects
            .filter(listing=listing, parent__isnull=True, is_hidden=False)
            .select_related('user')
            .prefetch_related(Prefetch('replies', queryset=_visible_replies_queryset()))
            .annotate(
                visible_replies=Count(
                    'replies',
                    filter=Q(replies__is_deleted=False, replies__is_hidden=False),
                    distinct=True,
                ),
            )
            # An author-deleted comment stays in the thread only while it still
            # holds visible replies; on its own it simply disappears.
            .filter(Q(is_deleted=False) | Q(visible_replies__gt=0))
            .order_by('-created_at')
        )

        if user.is_authenticated and user.is_staff:
            queryset = queryset.annotate(report_total=Count('reports', distinct=True))

        return queryset

    def create(self, request, *args, **kwargs):
        listing = self.get_listing()

        # Only the seller may answer on their own car. Checked before
        # validation so it reads as 403 Forbidden rather than a field error:
        # the request is well-formed, the caller simply is not permitted.
        if request.data.get('parent') not in (None, '') and listing.owner_id != request.user.id:
            raise PermissionDenied('Only the listing owner can reply to comments.')

        write = ListingCommentCreateSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        write.is_valid(raise_exception=True)
        comment = write.save(user=request.user, listing=listing)

        read = ListingCommentSerializer(comment, context=self.get_serializer_context())
        return Response(read.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=['Social'])
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_comment(request, pk):
    """
    DELETE /api/comments/{id}/ → 204

    Soft delete, author only. A listing owner who dislikes a comment reports
    it; being the seller does not confer the power to erase a buyer's words.
    """
    try:
        comment = ListingComment.objects.select_related('listing').get(pk=pk)
    except ListingComment.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if comment.user_id != request.user.id:
        return Response(
            {'detail': 'You can only delete your own comments.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if not comment.is_deleted:
        comment.is_deleted = True
        comment.save(update_fields=['is_deleted', 'updated_at'])
        log_action(
            user=request.user,
            action='delete',
            model_name='ListingComment',
            object_id=comment.pk,
            old_value={'is_deleted': False},
            new_value={'is_deleted': True, 'listing_id': comment.listing_id},
            ip_address=get_client_ip(request),
        )

    return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Social'])
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ReportRateThrottle])
def report_comment(request, pk):
    """
    POST /api/comments/{id}/report/ {reason, note?}

    201 on the first report from this user, 200 if they have already reported
    it — reporting twice is a mistake, not an error worth failing.
    """
    try:
        comment = ListingComment.objects.select_related('listing').get(pk=pk)
    except ListingComment.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if comment.user_id == request.user.id:
        return Response(
            {'detail': 'You cannot report your own comment.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = CommentReportSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    report, created = CommentReport.objects.get_or_create(
        comment=comment,
        reporter=request.user,
        defaults={
            'reason': serializer.validated_data['reason'],
            'note': serializer.validated_data.get('note', ''),
        },
    )

    if not created:
        return Response(
            CommentReportSerializer(report).data,
            status=status.HTTP_200_OK,
        )

    log_action(
        user=request.user,
        action='create',
        model_name='CommentReport',
        object_id=report.pk,
        old_value=None,
        new_value={
            'comment_id': comment.pk,
            'reason': report.reason,
        },
        ip_address=get_client_ip(request),
    )

    # Distinct reporters, because unique_together already guarantees one row
    # per person — the count is the number of people, not of complaints.
    report_count = CommentReport.objects.filter(comment=comment).count()
    if report_count >= CommentReport.AUTO_HIDE_THRESHOLD and not comment.is_hidden:
        comment.is_hidden = True
        comment.save(update_fields=['is_hidden', 'updated_at'])
        log_action(
            user=request.user,
            action='status_change',
            model_name='ListingComment',
            object_id=comment.pk,
            old_value={'is_hidden': False},
            new_value={
                'is_hidden': True,
                'source': 'auto',
                'report_count': report_count,
            },
            ip_address=get_client_ip(request),
        )

    return Response(
        CommentReportSerializer(report).data,
        status=status.HTTP_201_CREATED,
    )

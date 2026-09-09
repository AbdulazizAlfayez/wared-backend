from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from .models import Notification, NotificationPreference
from .serializers import NotificationPreferenceSerializer, NotificationSerializer


@extend_schema(tags=['Notifications'])
class NotificationViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/notifications/                 — list user's notifications
    GET    /api/notifications/{id}/            — retrieve (recipient only → 404 for others)
    PATCH  /api/notifications/{id}/            — mark as read/unread
    POST   /api/notifications/mark-all-read/   — bulk mark all unread → read
    GET    /api/notifications/unread-count/    — lightweight badge count

    No DELETE — notification history is preserved.
    """

    permission_classes = [IsAuthenticated]
    serializer_class   = NotificationSerializer
    http_method_names  = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = Notification.objects.filter(recipient=self.request.user).select_related('listing')

        is_read = self.request.query_params.get('is_read')
        ntype   = self.request.query_params.get('notification_type')

        if is_read is not None:
            qs = qs.filter(is_read=is_read.lower() == 'true')
        if ntype:
            qs = qs.filter(notification_type=ntype)

        return qs.order_by('-created_at')

    def retrieve(self, request, *args, **kwargs):
        """404 if the notification doesn't belong to this user."""
        instance = self.get_object()
        if instance.recipient_id != request.user.pk:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(instance).data)

    def update(self, request, *args, **kwargs):
        """PATCH: only is_read can be changed."""
        instance = self.get_object()
        if instance.recipient_id != request.user.pk:
            return Response(status=status.HTTP_404_NOT_FOUND)
        is_read = request.data.get('is_read')
        if is_read is not None:
            instance.is_read = bool(is_read)
            instance.save(update_fields=['is_read'])
        return Response(self.get_serializer(instance).data)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        """Mark a single notification as read."""
        instance = self.get_object()
        if instance.recipient_id != request.user.pk:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not instance.is_read:
            instance.is_read = True
            instance.save(update_fields=['is_read'])
        return Response(self.get_serializer(instance).data)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        """Mark every unread notification for this user as read."""
        count = Notification.objects.filter(
            recipient=request.user, is_read=False,
        ).update(is_read=True)
        return Response({'marked_read': count})

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        """Lightweight badge count — single COUNT query."""
        count = Notification.objects.filter(
            recipient=request.user, is_read=False,
        ).count()
        return Response({'unread_count': count})

    # -------------------------------------------------------------------------
    # Phase 3.2 — Notification preferences
    # -------------------------------------------------------------------------

    @action(detail=False, methods=['get', 'patch'], url_path='preferences')
    def preferences(self, request):
        """
        GET  /api/notifications/preferences/ — retrieve user's preferences
        PATCH /api/notifications/preferences/ — update one or more toggle fields
        """
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)

        if request.method == 'PATCH':
            serializer = NotificationPreferenceSerializer(
                prefs, data=request.data, partial=True,
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
            prefs.refresh_from_db()

        return Response(NotificationPreferenceSerializer(prefs).data)

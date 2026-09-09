import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from .models import Conversation, Message
from .serializers import (
    ChatRequestSerializer,
    ChatResponseSerializer,
    ConversationDetailSerializer,
    ConversationListSerializer,
)
from .services import AssistantUnavailable, run_chat

logger = logging.getLogger(__name__)

UNAVAILABLE_MSG = (
    'The assistant is temporarily unavailable. Please try again later. '
    '| المساعد غير متاح حالياً، حاول مرة أخرى لاحقاً.'
)


# ---------------------------------------------------------------------------
# Throttles
# ---------------------------------------------------------------------------

class AssistantUserThrottle(UserRateThrottle):
    scope = 'assistant_user'


class AssistantAnonThrottle(AnonRateThrottle):
    scope = 'assistant_anon'


# ---------------------------------------------------------------------------
# POST /api/assistant/chat/
# ---------------------------------------------------------------------------

@extend_schema(
    tags=['Assistant'],
    request=ChatRequestSerializer,
    responses={200: ChatResponseSerializer},
)
class ChatView(APIView):
    """
    Chat with the WARED AI assistant.

    - Anonymous visitors: stateless; optionally send prior "history".
    - Authenticated users: conversations are persisted server-side.

    Response always includes cars/cars_meta/orders arrays (empty when unused).
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AssistantUserThrottle, AssistantAnonThrottle]

    def get_throttles(self):
        if self.request.user and self.request.user.is_authenticated:
            return [AssistantUserThrottle()]
        return [AssistantAnonThrottle()]

    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = request.user if request.user.is_authenticated else None
        message_text = data['message']

        try:
            if user:
                return self._handle_authenticated(user, message_text, data)
            else:
                return self._handle_anonymous(message_text, data)
        except AssistantUnavailable:
            return Response(
                {'detail': UNAVAILABLE_MSG},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    def _handle_authenticated(self, user, message_text, data):
        conv_id = data.get('conversation_id')

        if conv_id:
            try:
                conv = Conversation.objects.get(pk=conv_id, user=user)
            except Conversation.DoesNotExist:
                return Response(
                    {'detail': 'Conversation not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            conv = Conversation.objects.create(
                user=user,
                title=message_text[:60],
            )

        history = list(
            conv.messages
            .order_by('created_at')
            .values('role', 'content')
            [:settings.ASSISTANT_HISTORY_LIMIT]
        )

        Message.objects.create(
            conversation=conv, role='user', content=message_text,
        )

        reply_text, cards = run_chat(user, history, message_text)

        Message.objects.create(
            conversation=conv, role='assistant', content=reply_text,
            meta=cards,
        )

        Conversation.objects.filter(pk=conv.pk).update(updated_at=timezone.now())

        return Response({
            'conversation_id': conv.id,
            'reply': reply_text,
            'cars': cards.get('cars', []),
            'cars_meta': cards.get('cars_meta'),
            'orders': cards.get('orders', []),
        })

    def _handle_anonymous(self, message_text, data):
        history = data.get('history', [])
        reply_text, cards = run_chat(None, history, message_text)
        return Response({
            'reply': reply_text,
            'cars': cards.get('cars', []),
            'cars_meta': cards.get('cars_meta'),
            'orders': [],  # Never expose orders for anonymous
        })


# ---------------------------------------------------------------------------
# GET /api/assistant/conversations/
# ---------------------------------------------------------------------------

@extend_schema(tags=['Assistant'])
class ConversationListView(generics.ListAPIView):
    """List the authenticated user's assistant conversations."""
    serializer_class = ConversationListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)


# ---------------------------------------------------------------------------
# GET/DELETE /api/assistant/conversations/{id}/
# ---------------------------------------------------------------------------

@extend_schema(tags=['Assistant'])
class ConversationDetailView(generics.RetrieveDestroyAPIView):
    """Retrieve or delete a specific assistant conversation (owner only)."""
    serializer_class = ConversationDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)

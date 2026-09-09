"""
WebSocket consumer for real-time messaging.

Connects at: ws://localhost:8000/ws/messaging/?token=<JWT_access_token>
"""
import json

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth.models import AnonymousUser


class MessagingConsumer(AsyncJsonWebsocketConsumer):
    """
    Handles real-time messaging events for authenticated users.

    Client → Server:
        { "type": "typing", "conversation_id": 123, "is_typing": true }

    Server → Client:
        { "type": "new_message", "conversation_id": 123, "message": {...} }
        { "type": "typing_indicator", "conversation_id": 123, "user_id": 45, "is_typing": true }
        { "type": "message_read", "conversation_id": 123, "reader_id": 45 }
    """

    async def connect(self):
        user = self.scope.get('user')
        if not user or isinstance(user, AnonymousUser) or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.user = user
        self.group_name = f'user_{user.pk}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.send_json({
            'type': 'connected',
            'user_id': user.pk,
            'message': 'مرحباً! تم الاتصال بنجاح.',
        })

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        msg_type = content.get('type')

        if msg_type == 'typing':
            conversation_id = content.get('conversation_id')
            is_typing = content.get('is_typing', False)
            if conversation_id:
                # Broadcast typing indicator to the other party
                # We need to find who the other party is
                from channels.db import database_sync_to_async
                recipient_id = await self._get_other_party(conversation_id)
                if recipient_id:
                    await self.channel_layer.group_send(
                        f'user_{recipient_id}',
                        {
                            'type': 'typing.indicator',
                            'conversation_id': conversation_id,
                            'user_id': self.user.pk,
                            'is_typing': is_typing,
                        },
                    )

    # ── Group message handlers ──

    async def chat_message(self, event):
        """Handle new_message broadcast from views."""
        await self.send_json({
            'type': 'new_message',
            'conversation_id': event['conversation_id'],
            'message': event['message'],
        })

    async def typing_indicator(self, event):
        """Forward typing indicator to client."""
        await self.send_json({
            'type': 'typing_indicator',
            'conversation_id': event['conversation_id'],
            'user_id': event['user_id'],
            'is_typing': event['is_typing'],
        })

    async def message_read(self, event):
        """Forward read receipt to client."""
        await self.send_json({
            'type': 'message_read',
            'conversation_id': event['conversation_id'],
            'reader_id': event['reader_id'],
        })

    # ── Helpers ──

    async def _get_other_party(self, conversation_id):
        from channels.db import database_sync_to_async
        from .models import Conversation

        @database_sync_to_async
        def _lookup():
            try:
                conv = Conversation.objects.get(pk=conversation_id)
                if conv.buyer_id == self.user.pk:
                    return conv.seller_id
                elif conv.seller_id == self.user.pk:
                    return conv.buyer_id
            except Conversation.DoesNotExist:
                pass
            return None

        return await _lookup()

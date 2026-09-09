"""
WebSocket consumer for real-time notifications.

Connect:
    ws://localhost:8000/ws/notifications/?token=<access_token>

On connect:
    - Authenticates via JWTAuthMiddleware (token in query string)
    - Rejects anonymous connections with code 4001
    - Sends current unread_count immediately

Incoming messages from client (JSON):
    { "action": "mark_read",     "notification_id": 42 }
    { "action": "mark_all_read" }

Outgoing messages to client (JSON):
    { "type": "new_notification", "notification": { ... } }
    { "type": "unread_count",     "count": 5 }

Group name per user:  notifications_{user_id}
"""

import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class NotificationConsumer(AsyncJsonWebsocketConsumer):

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self):
        self.user = self.scope.get('user')
        if not self.user or self.user.is_anonymous:
            await self.close(code=4001)
            return

        self.group_name = f'notifications_{self.user.id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Greet client with current unread count
        unread = await self.get_unread_count()
        await self.send_json({'type': 'unread_count', 'count': unread})

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # ------------------------------------------------------------------
    # Incoming messages from the browser
    # ------------------------------------------------------------------

    async def receive_json(self, content):
        action = content.get('action')

        if action == 'mark_read':
            notification_id = content.get('notification_id')
            if notification_id:
                await self.mark_notification_read(notification_id)
                unread = await self.get_unread_count()
                await self.send_json({'type': 'unread_count', 'count': unread})

        elif action == 'mark_all_read':
            await self.mark_all_read()
            await self.send_json({'type': 'unread_count', 'count': 0})

        elif action == 'ping':
            await self.send_json({'type': 'pong'})

    # ------------------------------------------------------------------
    # Group message handlers (called by channel layer)
    # ------------------------------------------------------------------

    async def send_notification(self, event):
        """Push a new notification payload to this WebSocket client."""
        await self.send_json(event['data'])

    async def unread_count_update(self, event):
        """Push an updated unread badge count to this WebSocket client."""
        await self.send_json({'type': 'unread_count', 'count': event['count']})

    # ------------------------------------------------------------------
    # Database helpers (async-safe via database_sync_to_async)
    # ------------------------------------------------------------------

    @database_sync_to_async
    def get_unread_count(self):
        from notifications.models import Notification
        return Notification.objects.filter(recipient=self.user, is_read=False).count()

    @database_sync_to_async
    def mark_notification_read(self, notification_id):
        from notifications.models import Notification
        Notification.objects.filter(
            id=notification_id, recipient=self.user,
        ).update(is_read=True)

    @database_sync_to_async
    def mark_all_read(self):
        from notifications.models import Notification
        Notification.objects.filter(
            recipient=self.user, is_read=False,
        ).update(is_read=True)

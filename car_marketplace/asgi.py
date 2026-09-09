"""
ASGI config for car_marketplace project.

Routes HTTP requests to Django and WebSocket connections to Channels.
WebSocket endpoints:
  ws://localhost:8000/ws/notifications/?token=<access_token>
  ws://localhost:8000/ws/messaging/?token=<access_token>
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'car_marketplace.settings')

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter          # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from notifications.middleware import JWTAuthMiddleware               # noqa: E402
from notifications.routing import websocket_urlpatterns as notification_ws  # noqa: E402
from messaging.routing import websocket_urlpatterns as messaging_ws  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': JWTAuthMiddleware(
        URLRouter(notification_ws + messaging_ws)
    ),
})

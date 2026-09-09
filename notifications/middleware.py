"""
JWT authentication middleware for Django Channels WebSocket connections.

WebSocket clients cannot send cookies via the Upgrade request in most browsers,
so we accept the access token as a query-string parameter instead:

    ws://localhost:8000/ws/notifications/?token=<access_token>
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

User = get_user_model()


@database_sync_to_async
def get_user_from_token(token_str: str):
    """Validate a SimpleJWT AccessToken string and return the matching User, or AnonymousUser."""
    try:
        from rest_framework_simplejwt.tokens import AccessToken
        token = AccessToken(token_str)
        return User.objects.get(id=token['user_id'])
    except Exception:
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """
    Attaches an authenticated Django User to the ASGI scope.
    Reads the 'token' query-string parameter; falls back to AnonymousUser.
    """

    async def __call__(self, scope, receive, send):
        query_string = parse_qs(scope.get('query_string', b'').decode())
        token = query_string.get('token', [None])[0]
        scope['user'] = await get_user_from_token(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)

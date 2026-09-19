"""
Delivering a notification to a phone, through Expo.

Expo's push service takes a batch of messages and answers with a receipt per
message. Two failures matter and are handled differently:

  * `DeviceNotRegistered` — the app was uninstalled or the token was rotated.
    The token is dead forever, so the device row is deactivated rather than
    retried; otherwise every future send drags a corpse along.
  * anything else — a transport or Expo-side problem, worth a retry, which the
    calling task owns.

Deliberately no `exponent-server-sdk` dependency: the API is one POST and
`requests` is already in the project.
"""
import logging

import requests

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'

#: Expo accepts at most 100 messages per request.
CHUNK_SIZE = 100
TIMEOUT_SECONDS = 10


def looks_like_expo_token(token: str) -> bool:
    """Cheap shape check, so obvious rubbish never reaches the network."""
    return bool(token) and (
        token.startswith('ExponentPushToken[') or token.startswith('ExpoPushToken[')
    ) and token.endswith(']')


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def send_expo_push(tokens, title, body, data=None):
    """
    Sends one message to many tokens.

    Returns the list of tokens Expo reported as permanently dead, so the
    caller can deactivate them. Never raises for a per-message failure — a
    single bad token must not cost the other recipients their notification.
    """
    valid = [token for token in tokens if looks_like_expo_token(token)]
    if not valid:
        return []

    dead = []
    for chunk in _chunks(valid, CHUNK_SIZE):
        messages = [
            {
                'to': token,
                'title': title,
                'body': body,
                'sound': 'default',
                'data': data or {},
            }
            for token in chunk
        ]
        try:
            response = requests.post(
                EXPO_PUSH_URL,
                json=messages,
                timeout=TIMEOUT_SECONDS,
                headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
            )
            response.raise_for_status()
            receipts = (response.json() or {}).get('data') or []
        except Exception as exc:
            # Raised to the task, which owns the retry policy.
            logger.warning('Expo push failed for %s tokens: %s', len(chunk), exc)
            raise

        for token, receipt in zip(chunk, receipts):
            if receipt.get('status') == 'error':
                error = (receipt.get('details') or {}).get('error')
                if error == 'DeviceNotRegistered':
                    dead.append(token)
                else:
                    logger.warning('Expo rejected a push to %s: %s', token[:24], receipt)

    return dead

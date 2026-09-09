import logging

import requests
from django.conf import settings

from .base import BaseSMSBackend

logger = logging.getLogger('sms')


class TaqnyatSMSBackend(BaseSMSBackend):
    """Taqnyat SMS provider — Saudi-based."""

    API_URL = 'https://api.taqnyat.sa/v1/messages'

    def send_sms(self, phone_number: str, message: str) -> bool:
        try:
            response = requests.post(
                self.API_URL,
                json={
                    'recipients': [phone_number],
                    'body':       message,
                    'sender':     settings.TAQNYAT_SENDER_ID,
                },
                headers={'Authorization': f'Bearer {settings.TAQNYAT_API_KEY}'},
                timeout=10,
            )
            return response.status_code == 200
        except Exception as exc:
            logger.error("Taqnyat SMS failed: %s", exc)
            return False

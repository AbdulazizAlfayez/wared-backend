import logging

import requests
from django.conf import settings

from .base import BaseSMSBackend

logger = logging.getLogger('sms')


class UnifonicSMSBackend(BaseSMSBackend):
    """Unifonic SMS provider — popular in Saudi Arabia."""

    API_URL = 'https://el.cloud.unifonic.com/rest/SMS/messages'

    def send_sms(self, phone_number: str, message: str) -> bool:
        try:
            response = requests.post(self.API_URL, data={
                'AppSid':    settings.UNIFONIC_APP_SID,
                'Recipient': phone_number,
                'Body':      message,
                'SenderID':  settings.UNIFONIC_SENDER_ID,
            }, timeout=10)
            data = response.json()
            success = data.get('success', False)
            if not success:
                logger.error("Unifonic error: %s", data)
            return bool(success)
        except Exception as exc:
            logger.error("Unifonic SMS failed: %s", exc)
            return False

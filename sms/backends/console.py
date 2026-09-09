import logging

from .base import BaseSMSBackend

logger = logging.getLogger('sms')


class ConsoleSMSBackend(BaseSMSBackend):
    """Prints SMS to console — for development."""

    def send_sms(self, phone_number: str, message: str) -> bool:
        border = '=' * 50
        logger.info("\n%s\nSMS TO: %s\nMESSAGE: %s\n%s", border, phone_number, message, border)
        print(f"\n{border}\n📱 SMS TO: {phone_number}\n📝 MESSAGE: {message}\n{border}")
        return True

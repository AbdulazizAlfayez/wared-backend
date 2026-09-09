class BaseSMSBackend:
    """Base class for SMS backends."""

    def send_sms(self, phone_number: str, message: str) -> bool:
        raise NotImplementedError

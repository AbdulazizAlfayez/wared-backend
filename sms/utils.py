from django.conf import settings
from django.utils.module_loading import import_string


def get_sms_backend():
    backend_path = getattr(settings, 'SMS_BACKEND', 'sms.backends.console.ConsoleSMSBackend')
    backend_class = import_string(backend_path)
    return backend_class()


def send_sms(phone_number: str, message: str) -> bool:
    backend = get_sms_backend()
    return backend.send_sms(phone_number, message)

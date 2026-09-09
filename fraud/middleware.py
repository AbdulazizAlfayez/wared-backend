import threading

from django.utils.deprecation import MiddlewareMixin


_thread_local = threading.local()


def get_current_ip():
    return getattr(_thread_local, 'ip', None)


class IPLogMiddleware(MiddlewareMixin):
    """Stores client IP in thread-local so views can log it easily."""

    def process_request(self, request):
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded:
            ip = x_forwarded.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', '')
        _thread_local.ip = ip
        request.client_ip = ip  # also attach to request object

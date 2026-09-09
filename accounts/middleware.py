import sentry_sdk


class SentryUserContextMiddleware:
    """Adds user role + verification status to Sentry events for filtering."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            sentry_sdk.set_tag('user.role', getattr(request.user, 'role', 'unknown'))
            sentry_sdk.set_tag('user.is_verified', getattr(request.user, 'is_email_verified', False))
        return self.get_response(request)

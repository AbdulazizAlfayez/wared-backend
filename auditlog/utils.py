from .models import AuditLog


def log_action(
    user, action, model_name, object_id,
    old_value=None, new_value=None, ip_address=None, user_agent='',
):
    """Create an AuditLog entry."""
    AuditLog.objects.create(
        user=user,
        action=action,
        model_name=model_name,
        object_id=object_id,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
        user_agent=(user_agent or '')[:500],
    )


def get_client_ip(request):
    """Extract the real client IP, respecting X-Forwarded-For proxies."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def get_user_agent(request):
    """The caller's device string, truncated to what AuditLog stores."""
    return (request.META.get('HTTP_USER_AGENT') or '')[:500]

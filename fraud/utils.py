from .models import IPLog


def log_ip_action(user, ip_address, action, user_agent=''):
    """Create an IPLog entry. Safe to call anywhere."""
    if not ip_address:
        return
    IPLog.objects.create(
        user=user,
        ip_address=ip_address,
        action=action,
        user_agent=user_agent[:500],
    )


def check_and_enforce_limit(user):
    """
    Check daily listing limit for a user.
    Returns (allowed: bool, limit: int, used: int).
    Creates ListingLimit if it doesn't exist.
    """
    from django.utils import timezone
    from .models import ListingLimit

    today = timezone.now().date()
    limit_obj, _ = ListingLimit.objects.get_or_create(
        user=user,
        defaults={'daily_limit': _get_user_daily_limit(user), 'last_reset': today},
    )

    # Auto-reset if last_reset is in the past
    if limit_obj.last_reset < today:
        limit_obj.listings_today = 0
        limit_obj.last_reset     = today
        limit_obj.save(update_fields=['listings_today', 'last_reset'])

    # Also refresh daily_limit in case subscription changed
    limit_obj.daily_limit = _get_user_daily_limit(user)
    limit_obj.save(update_fields=['daily_limit'])

    if limit_obj.listings_today >= limit_obj.daily_limit:
        return False, limit_obj.daily_limit, limit_obj.listings_today

    return True, limit_obj.daily_limit, limit_obj.listings_today


def increment_listing_count(user):
    """Call after a listing is successfully created."""
    from django.utils import timezone
    from .models import ListingLimit

    today = timezone.now().date()
    limit_obj, _ = ListingLimit.objects.get_or_create(
        user=user,
        defaults={'daily_limit': _get_user_daily_limit(user), 'last_reset': today},
    )
    if limit_obj.last_reset < today:
        limit_obj.listings_today = 0
        limit_obj.last_reset     = today
    limit_obj.listings_today += 1
    limit_obj.save(update_fields=['listings_today', 'last_reset'])


def _get_user_daily_limit(user):
    """Return the daily listing limit based on user role/subscription."""
    if getattr(user, 'role', '') == 'admin':
        return 9999
    # Check subscription tier
    try:
        sub = user.subscription  # related_name from subscriptions app
        plan_name = getattr(sub.plan, 'name', '').lower() if sub.plan else ''
        if 'premium' in plan_name or 'gold' in plan_name or 'platinum' in plan_name:
            return 50
        if 'basic' in plan_name or 'starter' in plan_name:
            return 20
    except Exception:
        pass
    if getattr(user, 'role', '') == 'importer':
        return 20
    return 5  # regular user

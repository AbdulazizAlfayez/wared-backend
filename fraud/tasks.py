import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Avg, Count
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def check_duplicate_vin(self, listing_id):
    """Flag if another active listing has the same non-null VIN."""
    from cars.models import Listing
    from .models import FraudFlag
    try:
        listing = Listing.objects.get(pk=listing_id)
    except Listing.DoesNotExist:
        return

    if not listing.vin:
        return

    duplicates = Listing.objects.filter(
        vin=listing.vin, is_active=True
    ).exclude(pk=listing_id)

    if duplicates.exists():
        FraudFlag.objects.get_or_create(
            listing=listing,
            flag_type='duplicate_vin',
            is_resolved=False,
            defaults={
                'user':     listing.owner,
                'severity': 'critical',
                'details':  {
                    'vin':               listing.vin,
                    'duplicate_ids':     list(duplicates.values_list('id', flat=True)),
                    'duplicate_titles':  list(duplicates.values_list('title', flat=True)),
                },
            },
        )
        # Set listing back to pending review
        if listing.status == 'approved':
            listing.status = 'pending'
            listing.save(update_fields=['status'])
        _notify_admins_fraud('duplicate_vin', listing_id)


@shared_task(bind=True, max_retries=3)
def check_suspicious_pricing(self, listing_id):
    """Flag if price is implausibly low/high vs market or below 1000 SAR."""
    from cars.models import Listing
    from .models import FraudFlag
    try:
        listing = Listing.objects.get(pk=listing_id)
    except Listing.DoesNotExist:
        return

    price = listing.price
    if not price:
        return

    # Rule 1: absolute minimum
    if price < 1000:
        FraudFlag.objects.get_or_create(
            listing=listing,
            flag_type='suspicious_price',
            is_resolved=False,
            defaults={
                'user':     listing.owner,
                'severity': 'high',
                'details':  {'detected_price': float(price), 'reason': 'below_minimum_1000_sar'},
            },
        )
        return

    # Rule 2: vs market average for same make+model (year ±2)
    avg_data = Listing.objects.filter(
        make__iexact=listing.make,
        model__iexact=listing.model,
        year__range=(listing.year - 2, listing.year + 2),
        is_active=True,
        price__gt=1000,
    ).exclude(pk=listing_id).aggregate(avg=Avg('price'), cnt=Count('id'))

    market_avg = avg_data['avg']
    count      = avg_data['cnt'] or 0

    if market_avg and count >= 3:
        ratio = float(price) / float(market_avg)
        if ratio < 0.20:
            FraudFlag.objects.get_or_create(
                listing=listing,
                flag_type='suspicious_price',
                is_resolved=False,
                defaults={
                    'user':     listing.owner,
                    'severity': 'high',
                    'details':  {
                        'detected_price': float(price),
                        'market_average': float(market_avg),
                        'ratio':          round(ratio, 3),
                        'reason':         'price_below_20pct_of_market',
                    },
                },
            )
        elif ratio > 3.0:
            FraudFlag.objects.get_or_create(
                listing=listing,
                flag_type='price_manipulation',
                is_resolved=False,
                defaults={
                    'user':     listing.owner,
                    'severity': 'medium',
                    'details':  {
                        'detected_price': float(price),
                        'market_average': float(market_avg),
                        'ratio':          round(ratio, 3),
                        'reason':         'price_above_300pct_of_market',
                    },
                },
            )


@shared_task(bind=True, max_retries=3)
def check_rapid_posting(self, user_id):
    """Flag / suspend users who post too many listings in a short window."""
    from cars.models import Listing
    from .models import FraudFlag
    from accounts.models import User

    now      = timezone.now()
    hour_ago = now - timedelta(hours=1)
    day_ago  = now - timedelta(hours=24)

    count_hour = Listing.objects.filter(owner_id=user_id, created_at__gte=hour_ago).count()
    count_day  = Listing.objects.filter(owner_id=user_id, created_at__gte=day_ago).count()

    if count_hour >= 10:
        FraudFlag.objects.get_or_create(
            user_id=user_id,
            flag_type='rapid_posting',
            is_resolved=False,
            defaults={
                'severity': 'high',
                'details':  {'count_last_hour': count_hour, 'count_last_24h': count_day},
            },
        )

    if count_day >= 30:
        FraudFlag.objects.get_or_create(
            user_id=user_id,
            flag_type='excessive_listings',
            is_resolved=False,
            defaults={
                'severity': 'critical',
                'details':  {'count_last_24h': count_day},
            },
        )
        # Auto-suspend
        try:
            user = User.objects.get(pk=user_id)
            if not user.is_suspended and not user.is_banned:
                user.is_suspended     = True
                user.suspended_until  = now + timedelta(days=1)
                user.suspension_reason = 'Automatic suspension: excessive listing creation detected.'
                user.save(update_fields=['is_suspended', 'suspended_until', 'suspension_reason'])
        except User.DoesNotExist:
            pass


@shared_task(bind=True, max_retries=3)
def check_banned_keywords(self, listing_id):
    """Flag if listing title or description contains banned keywords."""
    from cars.models import Listing
    from .models import FraudFlag
    from .keywords import ALL_BANNED
    try:
        listing = Listing.objects.get(pk=listing_id)
    except Listing.DoesNotExist:
        return

    text    = ((listing.title or '') + ' ' + (listing.description or '')).lower()
    matched = [kw for kw in ALL_BANNED if kw.lower() in text]

    if matched:
        FraudFlag.objects.get_or_create(
            listing=listing,
            flag_type='banned_keywords',
            is_resolved=False,
            defaults={
                'user':     listing.owner,
                'severity': 'medium',
                'details':  {'matched_keywords': matched},
            },
        )


@shared_task
def check_ip_abuse():
    """Periodic: find IPs with 5+ accounts or 50+ actions in 1 hour."""
    from .models import IPLog, FraudFlag
    from django.db.models import Count

    hour_ago = timezone.now() - timedelta(hours=1)

    # Multiple accounts per IP
    suspicious_ips = (
        IPLog.objects
        .values('ip_address')
        .annotate(user_count=Count('user', distinct=True))
        .filter(user_count__gte=5)
    )
    for row in suspicious_ips:
        ip   = row['ip_address']
        uids = list(
            IPLog.objects.filter(ip_address=ip)
            .values_list('user_id', flat=True)
            .distinct()
        )
        # Use simple filter to avoid JSONField contains lookup issues
        if not FraudFlag.objects.filter(
            flag_type='ip_abuse',
            is_resolved=False,
        ).filter(details__ip_address=ip).exclude(details__reason='high_action_rate').exists():
            FraudFlag.objects.create(
                flag_type='ip_abuse',
                severity='high',
                details={'ip_address': ip, 'user_count': row['user_count'], 'user_ids': uids},
            )

    # High action rate
    burst_ips = (
        IPLog.objects
        .filter(created_at__gte=hour_ago)
        .values('ip_address')
        .annotate(action_count=Count('id'))
        .filter(action_count__gte=50)
    )
    for row in burst_ips:
        ip = row['ip_address']
        if not FraudFlag.objects.filter(
            flag_type='ip_abuse',
            is_resolved=False,
        ).filter(details__ip_address=ip, details__reason='high_action_rate').exists():
            FraudFlag.objects.create(
                flag_type='ip_abuse',
                severity='medium',
                details={'ip_address': ip, 'actions_last_hour': row['action_count'], 'reason': 'high_action_rate'},
            )


@shared_task
def daily_limit_reset():
    """Celery Beat: reset listings_today to 0 for all ListingLimit records."""
    from .models import ListingLimit
    today = timezone.now().date()
    ListingLimit.objects.filter(last_reset__lt=today).update(
        listings_today=0,
        last_reset=today,
    )


def _notify_admins_fraud(flag_type, listing_id=None):
    """Send system notification to all admins about a new fraud flag."""
    from notifications.utils import notify
    from accounts.models import User
    msg = f"New fraud flag: {flag_type}"
    if listing_id:
        msg += f" on listing #{listing_id}"
    for admin in User.objects.filter(role='admin', is_active=True):
        notify(recipient=admin, notification_type='system', title='Fraud Alert', message=msg)

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name='importers.tasks.check_cr_expirations')
def check_cr_expirations():
    """Daily task: check CR expiry dates, send reminders, auto-suspend expired."""
    from .models import CRVerificationHistory, ImporterProfile

    today = timezone.now().date()
    actions = {'reminded_60': 0, 'reminded_30': 0, 'reminded_7': 0, 'reminded_0': 0, 'suspended': 0}

    # 60-day reminder
    for ip in ImporterProfile.objects.filter(cr_expiry_date=today + timedelta(days=60), cr_verification_status='verified'):
        try:
            send_cr_reminder.delay(ip.id, 60)
        except Exception:
            send_cr_reminder(ip.id, 60)
        CRVerificationHistory.objects.create(importer_profile=ip, action='reminder_sent_60', cr_expiry_date=ip.cr_expiry_date)
        actions['reminded_60'] += 1

    # 30-day reminder + status change
    for ip in ImporterProfile.objects.filter(cr_expiry_date=today + timedelta(days=30), cr_verification_status='verified'):
        old = ip.cr_verification_status
        ip.cr_verification_status = 'expiring_soon'
        ip.save(update_fields=['cr_verification_status'])
        try:
            send_cr_reminder.delay(ip.id, 30)
        except Exception:
            send_cr_reminder(ip.id, 30)
        CRVerificationHistory.objects.create(importer_profile=ip, action='reminder_sent_30', old_status=old, new_status='expiring_soon', cr_expiry_date=ip.cr_expiry_date)
        actions['reminded_30'] += 1

    # 7-day reminder
    for ip in ImporterProfile.objects.filter(cr_expiry_date=today + timedelta(days=7), cr_verification_status='expiring_soon'):
        try:
            send_cr_reminder.delay(ip.id, 7)
        except Exception:
            send_cr_reminder(ip.id, 7)
        CRVerificationHistory.objects.create(importer_profile=ip, action='reminder_sent_7', cr_expiry_date=ip.cr_expiry_date)
        actions['reminded_7'] += 1

    # Expiry day
    for ip in ImporterProfile.objects.filter(cr_expiry_date=today, cr_verification_status='expiring_soon'):
        try:
            send_cr_reminder.delay(ip.id, 0)
        except Exception:
            send_cr_reminder(ip.id, 0)
        CRVerificationHistory.objects.create(importer_profile=ip, action='reminder_sent_expired', cr_expiry_date=ip.cr_expiry_date)
        actions['reminded_0'] += 1

    # Auto-suspend expired (1+ days past)
    for ip in ImporterProfile.objects.filter(cr_expiry_date__lt=today, cr_verification_status__in=['verified', 'expiring_soon']):
        auto_suspend_importer(ip.id)
        actions['suspended'] += 1

    return f"CR check: {actions}"


@shared_task(name='importers.tasks.send_cr_reminder')
def send_cr_reminder(importer_profile_id, days_remaining):
    """Send CR expiry reminder via email + in-app notification."""
    from .models import ImporterProfile
    from notifications.utils import notify

    try:
        ip = ImporterProfile.objects.select_related('user').get(id=importer_profile_id)
    except ImporterProfile.DoesNotExist:
        return f"ImporterProfile {importer_profile_id} not found"

    user = ip.user
    templates = {
        60: ('cr_reminder_60', 'Your CR expires in 60 days'),
        30: ('cr_reminder_30', 'Action needed: CR expires in 30 days'),
        7:  ('cr_reminder_7', 'URGENT: CR expires in 7 days'),
        0:  ('cr_expiry_day', 'Your CR expires today'),
    }
    template, subject = templates.get(days_remaining, ('cr_reminder_30', 'CR Expiry Reminder'))

    notify(
        recipient=user,
        notification_type='system',
        title=subject,
        message=f"Your Commercial Registration expires {'today' if days_remaining == 0 else f'in {days_remaining} days'} on {ip.cr_expiry_date}. Please renew to avoid suspension.",
    )

    try:
        from notifications.emails import send_templated_email
        send_templated_email(
            to_email=user.email,
            subject=f"{subject} — WARED",
            template_name=template,
            context={
                'name': user.name,
                'business_name': ip.business_name,
                'expiry_date': str(ip.cr_expiry_date),
                'days_remaining': days_remaining,
            },
        )
    except Exception as e:
        logger.warning("CR reminder email failed for %s: %s", user.email, e)

    return f"CR reminder ({days_remaining}d) sent to {user.email}"


@shared_task(name='importers.tasks.auto_suspend_importer')
def auto_suspend_importer(importer_profile_id):
    """Auto-suspend importer when CR has expired."""
    from .models import CRVerificationHistory, ImporterProfile
    from cars.models import Listing
    from notifications.utils import notify

    try:
        ip = ImporterProfile.objects.select_related('user').get(id=importer_profile_id)
    except ImporterProfile.DoesNotExist:
        return f"ImporterProfile {importer_profile_id} not found"

    if ip.cr_verification_status == 'suspended':
        return f"Already suspended: {ip.business_name}"

    old_status = ip.cr_verification_status
    ip.cr_verification_status = 'suspended'
    ip.cr_suspended_at = timezone.now()
    ip.save(update_fields=['cr_verification_status', 'cr_suspended_at'])

    # Suspend active listings (not sold/archived)
    Listing.objects.filter(
        owner=ip.user, is_active=True,
    ).exclude(status__in=['sold', 'archived']).update(
        status='archived', is_active=False,
    )

    CRVerificationHistory.objects.create(
        importer_profile=ip,
        action='auto_suspended',
        old_status=old_status,
        new_status='suspended',
        cr_expiry_date=ip.cr_expiry_date,
        notes=f"CR expired on {ip.cr_expiry_date}. Auto-suspended by system.",
    )

    notify(
        recipient=ip.user,
        notification_type='system',
        title='Account Suspended — CR Expired',
        message='Your Commercial Registration has expired and your account has been suspended. Please renew your CR.',
    )

    try:
        from notifications.emails import send_templated_email
        send_templated_email(
            to_email=ip.user.email,
            subject='Account Suspended: CR Expired — WARED',
            template_name='cr_suspended',
            context={
                'name': ip.user.name,
                'business_name': ip.business_name,
                'expiry_date': str(ip.cr_expiry_date),
            },
        )
    except Exception as e:
        logger.warning("CR suspension email failed for %s: %s", ip.user.email, e)

    return f"Auto-suspended: {ip.business_name}"

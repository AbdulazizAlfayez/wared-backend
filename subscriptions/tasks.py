"""
Celery tasks for subscription lifecycle management.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name='subscriptions.check_expired_subscriptions')
def check_expired_subscriptions():
    """
    Daily task: expire active subscriptions past their end date,
    downgrade them to the Free plan, and notify the dealer.
    """
    from .models import DealerSubscription, SubscriptionHistory, SubscriptionPlan

    now    = timezone.now()
    count  = 0

    try:
        free_plan = SubscriptionPlan.objects.get(slug='free')
    except SubscriptionPlan.DoesNotExist:
        logger.error("Free plan not found — cannot run expiry task.")
        return "Free plan missing."

    expired_qs = DealerSubscription.objects.filter(
        expires_at__lt=now,
        status='active',
    ).select_related('plan', 'dealer')

    for sub in expired_qs:
        old_plan    = sub.plan
        sub.status  = 'expired'
        sub.plan    = free_plan
        sub.expires_at = now + timedelta(days=3650)
        sub.save()

        SubscriptionHistory.objects.create(
            dealer=sub.dealer,
            plan=free_plan,
            action='expired',
            old_plan=old_plan,
            billing_cycle=sub.billing_cycle,
            notes=f"Auto-expired and downgraded from {old_plan.name}.",
        )

        # In-app notification
        try:
            from notifications.utils import notify
            notify(
                recipient=sub.dealer,
                notification_type='system',
                title="Your subscription has expired",
                message=(
                    f"Your {old_plan.name} subscription has expired. "
                    "You've been moved to the Free plan. Upgrade to continue enjoying premium features."
                ),
            )
        except Exception:
            pass

        # Email
        try:
            from notifications.emails import send_templated_email
            from django.conf import settings
            send_templated_email(
                to_email=sub.dealer.email,
                subject="Your subscription has expired",
                template_name='subscription_expired',
                context={
                    'user_name':  sub.dealer.name,
                    'plan_name':  old_plan.name,
                    'frontend_url': getattr(settings, 'FRONTEND_URL', ''),
                },
            )
        except Exception:
            pass

        count += 1
        logger.info("Expired subscription for dealer %s (was %s)", sub.dealer.email, old_plan.name)

    return f"Expired {count} subscription(s)."


@shared_task(name='subscriptions.send_expiry_reminders')
def send_expiry_reminders():
    """
    Daily task: email dealers whose subscriptions expire in 7 or 1 days.
    """
    from .models import DealerSubscription

    now     = timezone.now()
    sent    = 0

    for days, urgency in [(7, '7 days'), (1, '1 day')]:
        window_start = now + timedelta(days=days - 1)
        window_end   = now + timedelta(days=days)

        expiring = DealerSubscription.objects.filter(
            expires_at__gte=window_start,
            expires_at__lt=window_end,
            status='active',
        ).select_related('plan', 'dealer')

        for sub in expiring:
            try:
                from notifications.utils import notify
                notify(
                    recipient=sub.dealer,
                    notification_type='system',
                    title=f"Your subscription expires in {urgency}",
                    message=(
                        f"Your {sub.plan.name} subscription expires on "
                        f"{sub.expires_at.strftime('%d %b %Y')}. Renew now to avoid interruption."
                    ),
                )
            except Exception:
                pass

            try:
                from notifications.emails import send_templated_email
                from django.conf import settings
                send_templated_email(
                    to_email=sub.dealer.email,
                    subject=f"Your {sub.plan.name} subscription expires in {urgency}",
                    template_name='subscription_expiring',
                    context={
                        'user_name':    sub.dealer.name,
                        'plan_name':    sub.plan.name,
                        'days':         days,
                        'expires_at':   sub.expires_at.strftime('%d %b %Y'),
                        'frontend_url': getattr(settings, 'FRONTEND_URL', ''),
                    },
                )
            except Exception:
                pass

            sent += 1

    return f"Sent {sent} expiry reminder(s)."

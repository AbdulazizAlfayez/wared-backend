"""
Celery tasks for review reminders.

Tasks:
  send_review_reminder_3day  — remind buyer 3 days after order delivered
  send_review_reminder_14day — final reminder 14 days after order delivered
  schedule_review_reminders  — periodic beat task that enqueues both reminders
"""
import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_pending_review_orders(days_since_delivered):
    """
    Return ImportOrders that:
      - have status delivered or completed
      - actual_delivery_date is approximately `days_since_delivered` days ago
      - do NOT yet have a review
    """
    from datetime import timedelta

    from orders.models import ImportOrder
    from reviews.models import Review

    cutoff_start = timezone.now() - timedelta(days=days_since_delivered + 1)
    cutoff_end   = timezone.now() - timedelta(days=days_since_delivered)

    reviewed_order_ids = Review.objects.filter(
        review_type='importer_order',
        order__isnull=False,
    ).values_list('order_id', flat=True)

    return ImportOrder.objects.filter(
        status__in=['delivered', 'completed'],
        actual_delivery_date__range=[cutoff_start.date(), cutoff_end.date()],
    ).exclude(
        id__in=reviewed_order_ids,
    ).select_related('buyer', 'importer', 'car')


# ---------------------------------------------------------------------------
# Task 1 — 3-day reminder
# ---------------------------------------------------------------------------

@shared_task(
    name='reviews.tasks.send_review_reminder_3day',
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def send_review_reminder_3day(self, order_id):
    """Send a 3-day post-delivery review reminder email to the buyer."""
    from django.core.mail import EmailMessage
    from django.template.loader import render_to_string

    from orders.models import ImportOrder
    from reviews.models import Review

    try:
        order = ImportOrder.objects.select_related('buyer', 'importer', 'car').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.error("send_review_reminder_3day: order %s not found", order_id)
        return f"Order {order_id} not found"

    # Skip if already reviewed
    if Review.objects.filter(order=order).exists():
        return f"Order {order_id} already has a review — skipping"

    buyer = order.buyer
    if not buyer.email:
        return f"Buyer {buyer.id} has no email — skipping"

    frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
    review_url   = f"{frontend_url}/orders/{order.pk}/review"

    importer_name = order.importer.name if order.importer else 'Your Importer'

    context = {
        'buyer_name':    buyer.name,
        'order_number':  order.order_number,
        'car_year':      order.car.year,
        'car_make':      order.car.make,
        'car_model':     order.car.model,
        'importer_name': importer_name,
        'review_url':    review_url,
        'frontend_url':  frontend_url,
        'reminder_day':  3,
        'current_year':  timezone.now().year,
    }

    try:
        html_body = render_to_string('emails/review_reminder.html', context)
        email = EmailMessage(
            subject=f"How was your experience? Leave a review for order {order.order_number}",
            body=html_body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@wared.sa'),
            to=[buyer.email],
        )
        email.content_subtype = 'html'
        email.send(fail_silently=False)
        logger.info("3-day reminder sent to %s for order %s", buyer.email, order.order_number)
        return f"3-day reminder sent to {buyer.email} for order {order.order_number}"
    except Exception as exc:
        logger.exception("send_review_reminder_3day failed for order %s: %s", order_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task 2 — 14-day reminder
# ---------------------------------------------------------------------------

@shared_task(
    name='reviews.tasks.send_review_reminder_14day',
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def send_review_reminder_14day(self, order_id):
    """Send a 14-day post-delivery final review reminder email to the buyer."""
    from django.core.mail import EmailMessage
    from django.template.loader import render_to_string

    from orders.models import ImportOrder
    from reviews.models import Review

    try:
        order = ImportOrder.objects.select_related('buyer', 'importer', 'car').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.error("send_review_reminder_14day: order %s not found", order_id)
        return f"Order {order_id} not found"

    # Skip if already reviewed
    if Review.objects.filter(order=order).exists():
        return f"Order {order_id} already has a review — skipping"

    buyer = order.buyer
    if not buyer.email:
        return f"Buyer {buyer.id} has no email — skipping"

    frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
    review_url   = f"{frontend_url}/orders/{order.pk}/review"

    importer_name = order.importer.name if order.importer else 'Your Importer'

    context = {
        'buyer_name':    buyer.name,
        'order_number':  order.order_number,
        'car_year':      order.car.year,
        'car_make':      order.car.make,
        'car_model':     order.car.model,
        'importer_name': importer_name,
        'review_url':    review_url,
        'frontend_url':  frontend_url,
        'reminder_day':  14,
        'current_year':  timezone.now().year,
    }

    try:
        html_body = render_to_string('emails/review_reminder.html', context)
        email = EmailMessage(
            subject=f"Last chance — share your experience for order {order.order_number}",
            body=html_body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@wared.sa'),
            to=[buyer.email],
        )
        email.content_subtype = 'html'
        email.send(fail_silently=False)
        logger.info("14-day reminder sent to %s for order %s", buyer.email, order.order_number)
        return f"14-day reminder sent to {buyer.email} for order {order.order_number}"
    except Exception as exc:
        logger.exception("send_review_reminder_14day failed for order %s: %s", order_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task 3 — periodic scheduler (called by Celery Beat daily)
# ---------------------------------------------------------------------------

@shared_task(name='reviews.tasks.schedule_review_reminders')
def schedule_review_reminders():
    """
    Run daily via Celery Beat.
    Enqueues 3-day and 14-day reminder tasks for eligible orders.
    """
    orders_3day  = _get_pending_review_orders(days_since_delivered=3)
    orders_14day = _get_pending_review_orders(days_since_delivered=14)

    queued_3  = 0
    queued_14 = 0

    for order in orders_3day:
        send_review_reminder_3day.delay(order.id)
        queued_3 += 1

    for order in orders_14day:
        send_review_reminder_14day.delay(order.id)
        queued_14 += 1

    logger.info(
        "schedule_review_reminders: queued %d 3-day reminders, %d 14-day reminders",
        queued_3, queued_14,
    )
    return f"Queued: {queued_3} (3-day), {queued_14} (14-day)"

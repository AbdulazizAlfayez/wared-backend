"""
Order status change signals.

pre_save  → stores old status on the instance as _previous_status
post_save → queues Celery notification tasks when status changes
"""
import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import ImportOrder, ImportTimeline

logger = logging.getLogger(__name__)

# Statuses that trigger an SMS in addition to email / in-app.
MAJOR_MILESTONES = {'shipped', 'arrived_port', 'customs_cleared', 'ready', 'delivered', 'cancelled'}


# ---------------------------------------------------------------------------
# pre_save — capture old status before write
# ---------------------------------------------------------------------------

@receiver(pre_save, sender=ImportOrder)
def track_status_change(sender, instance, **kwargs):
    """Store previous status on instance for post_save use."""
    if instance.pk:
        try:
            old = ImportOrder.objects.get(pk=instance.pk)
            instance._previous_status = old.status
        except ImportOrder.DoesNotExist:
            instance._previous_status = None
    else:
        instance._previous_status = None


# ---------------------------------------------------------------------------
# post_save ImportOrder — queue notification tasks on status change
# ---------------------------------------------------------------------------

@receiver(post_save, sender=ImportOrder)
def on_order_status_change(sender, instance, created, **kwargs):
    """Queue notification tasks when an existing order's status changes."""
    if created:
        # New order — queue the initial "order placed" notifications
        _queue_notifications(instance, old_status=None, new_status=instance.status)
        return

    old_status = getattr(instance, '_previous_status', None)
    new_status = instance.status

    if old_status is None or old_status == new_status:
        return  # no change worth notifying

    _queue_notifications(instance, old_status=old_status, new_status=new_status)


def _queue_notifications(order, old_status, new_status):
    """Dispatch all notification tasks for a status change."""
    from django.conf import settings

    order_id = order.pk
    buyer_id = order.buyer_id
    buyer = order.buyer

    # Determine human-readable status label
    status_display = dict(ImportOrder.STATUS_CHOICES).get(new_status, new_status)

    # Build a short title/message for in-app notification
    order_url = f"{getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')}/orders/{order_id}"
    title   = f"Order {order.order_number} — {status_display}"
    message = (
        f"Your order for {order.car.year} {order.car.make} {order.car.model} "
        f"is now: {status_display}."
    )

    try:
        from .tasks import (
            create_in_app_notification,
            send_order_status_email,
            send_order_status_sms,
            send_order_websocket_notification,
        )

        # 1. Email
        send_order_status_email.delay(order_id, old_status, new_status)

        # 2. SMS — only for major milestones
        if new_status in MAJOR_MILESTONES:
            send_order_status_sms.delay(order_id, new_status)

        # 3. WebSocket
        send_order_websocket_notification.delay(
            order_id=order_id,
            event_type='order_status_changed',
            event_data={
                'order_id':    order_id,
                'order_number': order.order_number,
                'old_status':  old_status,
                'new_status':  new_status,
                'status_display': status_display,
                'message':     message,
            },
        )

        # 4. In-app notification
        create_in_app_notification.delay(
            user_id=buyer_id,
            order_id=order_id,
            title=title,
            message=message,
            notification_type='system',
        )

    except Exception as exc:
        logger.exception(
            "Failed to queue notifications for order %s → %s: %s",
            order.order_number, new_status, exc,
        )

    # v3 branded import emails (Phase 3.4)
    try:
        from notifications.tasks import (
            send_order_confirmed_email,
            send_order_status_update_email,
            send_ready_for_delivery_email,
            send_order_cancelled_email,
            send_deposit_received_email,
        )

        STATUS_TO_MILESTONE = {
            'purchased': 'car_purchased',
            'shipped': 'shipped',
            'arrived_port': 'arrived_port',
            'customs_cleared': 'customs_cleared',
        }

        if new_status == 'confirmed':
            send_order_confirmed_email.delay(order_id)
        elif new_status == 'deposit_paid':
            send_deposit_received_email.delay(order_id)
        elif new_status in STATUS_TO_MILESTONE:
            send_order_status_update_email.delay(order_id, STATUS_TO_MILESTONE[new_status])
        elif new_status == 'inspection' and getattr(order.car, 'vehicle_inspection_result', '') == 'passed':
            send_order_status_update_email.delay(order_id, 'inspection_passed')
        elif new_status == 'ready':
            send_ready_for_delivery_email.delay(order_id)
        elif new_status == 'cancelled':
            send_order_cancelled_email.delay(order_id)

    except Exception as exc:
        logger.warning("v3 email dispatch failed for order %s: %s", order.order_number, exc)


# ---------------------------------------------------------------------------
# post_save ImportTimeline — notify buyer of new public timeline event
# ---------------------------------------------------------------------------

@receiver(post_save, sender=ImportTimeline)
def on_timeline_event_created(sender, instance, created, **kwargs):
    """Notify the buyer when a new public timeline event is added."""
    if not created:
        return
    if not instance.is_public:
        return  # private events are importer/admin-only

    order = instance.order

    try:
        from .tasks import create_in_app_notification

        create_in_app_notification.delay(
            user_id=order.buyer_id,
            order_id=order.pk,
            title=f"Update on your order {order.order_number}",
            message=instance.title,
            notification_type='system',
        )
    except Exception as exc:
        logger.exception(
            "Failed to queue timeline notification for order %s: %s",
            order.order_number, exc,
        )

"""
Celery tasks for the order notification system.

Tasks:
  send_order_status_email          — HTML email on every status change
  send_order_status_sms            — SMS for major milestones only
  send_order_websocket_notification — push event to buyer's WS channel
  create_in_app_notification        — create Notification record in DB
"""
import logging

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status → email template mapping
# ---------------------------------------------------------------------------
STATUS_TO_TEMPLATE = {
    'pending':          'emails/orders/order_placed.html',
    'deposit_paid':     'emails/orders/deposit_received.html',
    'confirmed':        'emails/orders/order_confirmed.html',
    'purchased':        'emails/orders/car_purchased.html',
    'shipped':          'emails/orders/car_shipped.html',
    'arrived_port':     'emails/orders/car_arrived.html',
    'in_customs':       'emails/orders/in_customs.html',
    'customs_cleared':  'emails/orders/customs_cleared.html',
    'ready':            'emails/orders/ready_for_delivery.html',
    'delivered':        'emails/orders/delivered.html',
    'cancelled':        'emails/orders/order_cancelled.html',
}

# Statuses that warrant an SMS in addition to email
SMS_MILESTONES = {'shipped', 'arrived_port', 'customs_cleared', 'ready', 'delivered', 'cancelled'}

SMS_MESSAGES = {
    'shipped': (
        'سيارتك {make} {model} في طريقها | '
        'Your {make} {model} is on its way. Track: {url}'
    ),
    'arrived_port': (
        'سيارتك وصلت ميناء {port} | '
        'Your car arrived at {port}. Track: {url}'
    ),
    'customs_cleared': (
        'تم الإفراج الجمركي | Customs cleared! Track: {url}'
    ),
    'ready': (
        'سيارتك جاهزة للاستلام | '
        'Your car is ready for delivery. Track: {url}'
    ),
    'delivered': (
        'مبروك! تم تسليم سيارتك | '
        'Congratulations! Your car has been delivered. Track: {url}'
    ),
    'cancelled': (
        'تم إلغاء طلبك | '
        'Your order has been cancelled. Track: {url}'
    ),
}

# ---------------------------------------------------------------------------
# Task 1 — send_order_status_email
# ---------------------------------------------------------------------------

@shared_task(
    name='orders.tasks.send_order_status_email',
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_order_status_email(self, order_id, old_status, new_status):
    """Send an HTML status-update email to the buyer."""
    from django.core.mail import EmailMessage
    from django.template.loader import render_to_string
    from django.utils import timezone

    from auditlog.utils import log_action
    from notifications.models import NotificationPreference
    from orders.models import ImportOrder

    try:
        order = ImportOrder.objects.select_related(
            'buyer', 'car', 'importer'
        ).get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.error("send_order_status_email: order %s not found", order_id)
        return f"Order {order_id} not found"

    buyer = order.buyer

    # Respect email notification preference
    try:
        prefs, _ = NotificationPreference.objects.get_or_create(user=buyer)
        if not prefs.order_updates_email:
            logger.info(
                "send_order_status_email: buyer %s has disabled order email notifications",
                buyer.id,
            )
            return "Skipped — user opted out of order email notifications"
    except Exception as exc:
        logger.warning("Could not check email preferences for buyer %s: %s", buyer.id, exc)

    # Pick template
    template_name = STATUS_TO_TEMPLATE.get(new_status)
    if not template_name:
        logger.info(
            "send_order_status_email: no template for status '%s', skipping", new_status
        )
        return f"No email template for status '{new_status}'"

    frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
    order_url    = f"{frontend_url}/orders/{order.pk}"

    # Build importer display name
    importer = order.importer
    importer_name = importer.name if importer else 'Your Importer'

    # Status-specific extra context
    extra_ctx = {}
    if new_status == 'shipped':
        # Pull from latest timeline event if available
        latest = order.timeline_events.filter(event_type='shipped').order_by('-created_at').first()
        extra_ctx['vessel_name']       = getattr(latest, 'description', '') if latest else ''
        extra_ctx['estimated_arrival'] = (
            order.estimated_delivery_date.strftime('%d %B %Y')
            if order.estimated_delivery_date else ''
        )
    elif new_status == 'arrived_port':
        latest = order.timeline_events.filter(event_type='arrived_port').order_by('-created_at').first()
        extra_ctx['port_name'] = getattr(latest, 'description', '') if latest else 'Saudi Port'
    elif new_status == 'cancelled':
        extra_ctx['cancellation_reason'] = order.cancellation_reason or ''

    context = {
        'buyer_name':    buyer.name,
        'order_number':  order.order_number,
        'car_year':      order.car.year,
        'car_make':      order.car.make,
        'car_model':     order.car.model,
        'importer_name': importer_name,
        'order_url':     order_url,
        'frontend_url':  frontend_url,
        'current_year':  timezone.now().year,
        **extra_ctx,
    }

    try:
        html_body = render_to_string(template_name, context)
        status_display = dict(ImportOrder.STATUS_CHOICES).get(new_status, new_status)
        subject = f"Order {order.order_number} — {status_display} | WARED"

        email = EmailMessage(
            subject=subject,
            body=html_body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@wared.sa'),
            to=[buyer.email],
        )
        email.content_subtype = 'html'
        email.send(fail_silently=False)

        # Audit log
        log_action(
            user=buyer,
            action='status_change',
            model_name='ImportOrder',
            object_id=order.pk,
            old_value=old_status,
            new_value=new_status,
        )

        return f"Email sent to {buyer.email} for order {order.order_number} → {new_status}"

    except Exception as exc:
        logger.exception(
            "send_order_status_email failed for order %s: %s", order_id, exc
        )
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task 2 — send_order_status_sms
# ---------------------------------------------------------------------------

@shared_task(
    name='orders.tasks.send_order_status_sms',
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_order_status_sms(self, order_id, new_status):
    """Send an SMS for major milestone statuses."""
    if new_status not in SMS_MILESTONES:
        return f"SMS not configured for status '{new_status}'"

    from auditlog.utils import log_action
    from notifications.models import NotificationPreference
    from orders.models import ImportOrder

    try:
        order = ImportOrder.objects.select_related('buyer', 'car').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.error("send_order_status_sms: order %s not found", order_id)
        return f"Order {order_id} not found"

    buyer = order.buyer

    # Respect SMS preference
    try:
        prefs, _ = NotificationPreference.objects.get_or_create(user=buyer)
        if not prefs.order_updates_sms:
            logger.info(
                "send_order_status_sms: buyer %s has disabled order SMS notifications",
                buyer.id,
            )
            return "Skipped — user opted out of order SMS notifications"
    except Exception as exc:
        logger.warning("Could not check SMS preferences for buyer %s: %s", buyer.id, exc)

    phone = buyer.phone
    if not phone:
        logger.info("send_order_status_sms: buyer %s has no phone number", buyer.id)
        return "Skipped — buyer has no phone number"

    frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
    order_url    = f"{frontend_url}/orders/{order.pk}"

    template = SMS_MESSAGES.get(new_status, '')
    if not template:
        return f"No SMS template for status '{new_status}'"

    # Resolve port name for arrived_port
    port = 'port'
    if new_status == 'arrived_port':
        latest = order.timeline_events.filter(event_type='arrived_port').order_by('-created_at').first()
        if latest and latest.description:
            port = latest.description

    message = template.format(
        make=order.car.make,
        model=order.car.model,
        port=port,
        url=order_url,
    )

    # Try to send via configured SMS backend
    try:
        from sms.utils import send_sms
        result = send_sms(phone_number=phone, message=message)
        if result:
            log_action(
                user=buyer,
                action='status_change',
                model_name='ImportOrder',
                object_id=order.pk,
                old_value=None,
                new_value=f"sms_sent:{new_status}",
            )
            return f"SMS sent to {phone} for order {order.order_number} → {new_status}"
        else:
            logger.warning(
                "SMS backend returned failure for order %s → %s", order.order_number, new_status
            )
            return "SMS backend returned failure"
    except ImportError:
        logger.warning("SMS backend not available — skipping SMS for order %s", order_id)
        return "Skipped — SMS backend not configured"
    except Exception as exc:
        logger.exception(
            "send_order_status_sms failed for order %s: %s", order_id, exc
        )
        # Retry SMS failures
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error("Max SMS retries exceeded for order %s", order_id)
            return f"SMS failed after max retries: {exc}"


# ---------------------------------------------------------------------------
# Task 3 — send_order_websocket_notification
# ---------------------------------------------------------------------------

@shared_task(name='orders.tasks.send_order_websocket_notification')
def send_order_websocket_notification(order_id, event_type, event_data):
    """Push an order update event to the buyer's WebSocket channel group."""
    from orders.models import ImportOrder

    try:
        order = ImportOrder.objects.select_related('buyer').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.error("send_order_websocket_notification: order %s not found", order_id)
        return f"Order {order_id} not found"

    buyer_id = order.buyer_id

    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning("WebSocket channel layer not configured — skipping WS push")
            return "Skipped — channel layer not configured"

        async_to_sync(channel_layer.group_send)(
            f"user_{buyer_id}",
            {
                'type':       'order_update',
                'event_type': event_type,
                'data':       event_data,
            },
        )
        return f"WS event '{event_type}' sent to user_{buyer_id}"

    except ImportError:
        logger.warning(
            "django-channels not installed — skipping WS push for order %s", order_id
        )
        return "Skipped — channels not installed"
    except Exception as exc:
        logger.exception(
            "send_order_websocket_notification failed for order %s: %s", order_id, exc
        )
        return f"WS push failed: {exc}"


# ---------------------------------------------------------------------------
# Task 4 — create_in_app_notification
# ---------------------------------------------------------------------------

@shared_task(name='orders.tasks.create_in_app_notification')
def create_in_app_notification(user_id, order_id, title, message, notification_type='system'):
    """Create an in-app Notification record for the buyer."""
    from django.contrib.auth import get_user_model

    from notifications.models import Notification, NotificationPreference

    User = get_user_model()

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("create_in_app_notification: user %s not found", user_id)
        return f"User {user_id} not found"

    # Respect in-app preference (uses 'system' pref for order updates)
    try:
        prefs, _ = NotificationPreference.objects.get_or_create(user=user)
        if not prefs.order_updates_push:
            logger.info(
                "create_in_app_notification: user %s has disabled push notifications",
                user_id,
            )
            return "Skipped — user opted out of push notifications"
    except Exception as exc:
        logger.warning("Could not check push preferences for user %s: %s", user_id, exc)

    try:
        notif = Notification.objects.create(
            recipient=user,
            notification_type=notification_type,
            title=title,
            message=message,
            metadata={'order_id': order_id},
        )

        # Best-effort WebSocket push via existing notify utility
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            if channel_layer is not None:
                async_to_sync(channel_layer.group_send)(
                    f'notifications_{user_id}',
                    {
                        'type': 'send_notification',
                        'data': {
                            'type':              'new_notification',
                            'notification': {
                                'id':                notif.id,
                                'notification_type': notification_type,
                                'title':             title,
                                'message':           message,
                                'is_read':           False,
                                'created_at':        notif.created_at.isoformat(),
                                'metadata':          {'order_id': order_id},
                            },
                        },
                    },
                )
        except Exception:
            pass  # WS push is best-effort only

        return f"In-app notification {notif.id} created for user {user_id}"

    except Exception as exc:
        logger.exception(
            "create_in_app_notification failed for user %s order %s: %s",
            user_id, order_id, exc,
        )
        return f"Failed to create notification: {exc}"

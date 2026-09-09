"""
Lightweight helper for creating Notification records and pushing them via WebSocket.

Usage:
    from notifications.utils import notify
    notify(recipient=user, notification_type='listing_approved', title='...', message='...')

notify() will:
  1. Check the user's NotificationPreference — skip if that type is disabled.
  2. Create the Notification DB record.
  3. Best-effort push to the user's WebSocket channel group.
"""

# Mapping from notification_type string → NotificationPreference field name.
# Types not listed here are always delivered (e.g. 'user_blocked').
_TYPE_TO_PREF_FIELD = {
    'listing_approved':      'listing_approved',
    'listing_rejected':      'listing_rejected',
    'new_lead':              'new_lead',
    'lead_status_changed':   'lead_status_changed',
    'new_message':           'new_message',
    'conversation_started':  'conversation_started',
    'appointment_confirmed': 'appointment_confirmed',
    'appointment_rejected':  'appointment_rejected',
    'appointment_cancelled': 'appointment_cancelled',
    'price_drop':            'price_drop',
    'listing_expiring':      'listing_expiring',
    'system':                'system',
}


def notify(
    recipient,
    notification_type,
    title,
    message,
    listing=None,
    lead=None,
    appointment=None,
    metadata=None,
):
    """
    Create and return a Notification for *recipient*, respecting their preferences,
    and push it to their active WebSocket connection (best-effort).
    Returns None if the recipient has disabled that notification type.
    """
    from .models import Notification, NotificationPreference

    # --- Preference check ---------------------------------------------------
    pref_field = _TYPE_TO_PREF_FIELD.get(notification_type)
    if pref_field:
        prefs, _ = NotificationPreference.objects.get_or_create(user=recipient)
        if not getattr(prefs, pref_field, True):
            return None  # user opted out — skip silently

    # --- Create DB record ---------------------------------------------------
    notification = Notification.objects.create(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        message=message,
        listing=listing,
        lead=lead,
        appointment=appointment,
        metadata=metadata or {},
    )

    # --- WebSocket push (best-effort) ----------------------------------------
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is not None:
            async_to_sync(channel_layer.group_send)(
                f'notifications_{recipient.id}',
                {
                    'type': 'send_notification',
                    'data': {
                        'type': 'new_notification',
                        'notification': {
                            'id':                notification.id,
                            'notification_type': notification_type,
                            'title':             title,
                            'message':           message,
                            'listing_id':        listing.id if listing else None,
                            'is_read':           False,
                            'created_at':        notification.created_at.isoformat(),
                        },
                    },
                },
            )
    except Exception:
        pass  # Never let a WebSocket failure break the main action

    return notification


def notify_admins(title, message, listing=None):
    """Send a system notification to every active WARED admin.

    Used so admins always know about key marketplace events:
    reservation accepted/rejected, order cancelled, payment confirmed.
    Best-effort — never raises.
    """
    try:
        from accounts.models import User
        for admin in User.objects.filter(role='admin', is_active=True):
            try:
                notify(
                    recipient=admin,
                    notification_type='system',
                    title=title,
                    message=message,
                    listing=listing,
                )
            except Exception:
                continue
    except Exception:
        pass

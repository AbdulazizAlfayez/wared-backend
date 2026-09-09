"""
Phase 3.4 — Celery tasks for SMS notifications.

Every task:
  • Checks NotificationPreference.sms_notifications before sending.
  • Checks that the recipient has a verified phone number.
  • Logs errors and returns silently — never crashes the caller.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def _sms_enabled(user) -> bool:
    """Return True if the user has sms_notifications turned on and a verified phone."""
    from notifications.models import NotificationPreference
    if not getattr(user, 'is_phone_verified', False):
        return False
    if not user.phone:
        return False
    prefs, _ = NotificationPreference.objects.get_or_create(user=user)
    return prefs.sms_notifications


@shared_task(name='notifications.sms_tasks.send_new_lead_sms')
def send_new_lead_sms(lead_id: int) -> str:
    from leads.models import Lead
    try:
        lead = Lead.objects.select_related('dealer', 'listing').get(pk=lead_id)
    except Lead.DoesNotExist:
        logger.warning("send_new_lead_sms: Lead %s not found", lead_id)
        return f"Lead {lead_id} not found"

    dealer = lead.dealer
    if not _sms_enabled(dealer):
        return f"SMS notifications disabled or phone not verified for user {dealer.pk}"

    from sms.utils import send_sms
    message = (
        f"New inquiry for your {lead.listing.make} {lead.listing.model} "
        f"on WARED. Check your dashboard."
    )
    try:
        send_sms(dealer.phone, message)
        return f"New lead SMS sent to {dealer.phone}"
    except Exception as exc:
        logger.error("send_new_lead_sms failed for lead %s: %s", lead_id, exc)
        raise


@shared_task(name='notifications.sms_tasks.send_appointment_sms')
def send_appointment_sms(appointment_id: int, event_type: str) -> str:
    from bookings.models import Appointment
    try:
        appt = Appointment.objects.select_related('buyer', 'listing').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        logger.warning("send_appointment_sms: Appointment %s not found", appointment_id)
        return f"Appointment {appointment_id} not found"

    buyer = appt.buyer
    if not _sms_enabled(buyer):
        return f"SMS notifications disabled or phone not verified for user {buyer.pk}"

    from sms.utils import send_sms
    if event_type == 'confirmed':
        message = (
            f"Your appointment for {appt.listing.make} {appt.listing.model} "
            f"on {appt.appointment_date} has been confirmed. See you there!"
        )
    elif event_type == 'rejected':
        message = (
            f"Your appointment for {appt.listing.make} {appt.listing.model} "
            f"could not be confirmed. Visit WARED to book another time."
        )
    else:
        message = (
            f"Your appointment for {appt.listing.make} {appt.listing.model} "
            f"has been updated. Check WARED for details."
        )

    try:
        send_sms(buyer.phone, message)
        return f"Appointment SMS ({event_type}) sent to {buyer.phone}"
    except Exception as exc:
        logger.error("send_appointment_sms failed for appt %s: %s", appointment_id, exc)
        raise

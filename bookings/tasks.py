"""
Celery tasks for the bookings app.

Phase 3 will implement the actual notification sending.
These tasks are registered now so they can be configured in Celery Beat.
"""

from celery import shared_task


@shared_task
def send_appointment_reminder(appointment_id: int) -> str:
    """
    Placeholder: send a reminder notification 24 hours before an appointment.
    Called via: send_appointment_reminder.apply_async(args=[appt.pk], eta=reminder_time)

    Phase 3 implementation will:
    - Load the Appointment by id
    - Send an SMS / email to buyer and seller
    - Mark Appointment.last_reminded_at
    """
    return f"[placeholder] Reminder would be sent for appointment #{appointment_id}"


@shared_task
def auto_complete_past_appointments() -> str:
    """
    Batch task: marks confirmed appointments whose date has already passed as 'completed'.

    Intended to run nightly via Celery Beat.
    Uses QuerySet.update() to bypass model.save() for performance — the transition
    confirmed → completed is always valid so skipping clean() is safe here.
    """
    from django.utils import timezone
    from .models import Appointment

    today = timezone.now().date()
    updated = Appointment.objects.filter(
        status='confirmed',
        appointment_date__lt=today,
    ).update(status='completed')

    return f"Auto-completed {updated} past appointment(s)"

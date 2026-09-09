"""
Celery tasks for transactional emails.

Every task:
  • Fetches the relevant object(s) by PK (handles DoesNotExist gracefully).
  • Checks NotificationPreference.email_notifications before sending.
  • Retries up to 3 times (60 s apart) on send failure.
  • Uses send_templated_email() for consistent, template-driven HTML mail.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from .emails import send_templated_email
from .models import NotificationPreference

logger = logging.getLogger(__name__)

User = get_user_model()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _email_enabled(user) -> bool:
    """Return True if the user has email_notifications turned on (default: True)."""
    prefs, _ = NotificationPreference.objects.get_or_create(user=user)
    return prefs.email_notifications


# ---------------------------------------------------------------------------
# Listing emails
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='notifications.tasks.send_listing_approved_email')
def send_listing_approved_email(self, listing_id: int) -> str:
    from cars.models import Listing
    try:
        listing = Listing.objects.select_related('owner').get(pk=listing_id)
    except Listing.DoesNotExist:
        logger.warning("send_listing_approved_email: Listing %s not found", listing_id)
        return f"Listing {listing_id} not found"

    owner = listing.owner
    if not _email_enabled(owner):
        return f"Email notifications disabled for user {owner.pk}"

    try:
        send_templated_email(
            to_email=owner.email,
            subject=f"Your {listing.make} {listing.model} listing has been approved!",
            template_name='listing_approved',
            context={
                'name':       owner.name or owner.email,
                'make':       listing.make,
                'model':      listing.model,
                'year':       listing.year,
                'price':      listing.price,
                'listing_id': listing.pk,
            },
        )
        logger.info("Listing approved email sent to %s (listing %s)", owner.email, listing_id)
        return f"Listing approved email sent to {owner.email}"
    except Exception as exc:
        logger.error("send_listing_approved_email failed for listing %s: %s", listing_id, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='notifications.tasks.send_listing_rejected_email')
def send_listing_rejected_email(self, listing_id: int, rejection_reason: str) -> str:
    from cars.models import Listing
    try:
        listing = Listing.objects.select_related('owner').get(pk=listing_id)
    except Listing.DoesNotExist:
        logger.warning("send_listing_rejected_email: Listing %s not found", listing_id)
        return f"Listing {listing_id} not found"

    owner = listing.owner
    if not _email_enabled(owner):
        return f"Email notifications disabled for user {owner.pk}"

    try:
        send_templated_email(
            to_email=owner.email,
            subject=f"Action needed: your {listing.make} {listing.model} listing",
            template_name='listing_rejected',
            context={
                'name':             owner.name or owner.email,
                'make':             listing.make,
                'model':            listing.model,
                'year':             listing.year,
                'listing_id':       listing.pk,
                'rejection_reason': rejection_reason,
            },
        )
        logger.info("Listing rejected email sent to %s (listing %s)", owner.email, listing_id)
        return f"Listing rejected email sent to {owner.email}"
    except Exception as exc:
        logger.error("send_listing_rejected_email failed for listing %s: %s", listing_id, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='notifications.tasks.send_listing_changes_requested_email')
def send_listing_changes_requested_email(self, listing_id: int, admin_note: str) -> str:
    from cars.models import Listing
    try:
        listing = Listing.objects.select_related('owner').get(pk=listing_id)
    except Listing.DoesNotExist:
        logger.warning("send_listing_changes_requested_email: Listing %s not found", listing_id)
        return f"Listing {listing_id} not found"

    owner = listing.owner
    if not _email_enabled(owner):
        return f"Email notifications disabled for user {owner.pk}"

    try:
        send_templated_email(
            to_email=owner.email,
            subject=f"مطلوب تعديل على إعلانك · {listing.make} {listing.model}",
            template_name='listing_changes_requested',
            context={
                'name': owner.name or owner.email,
                'make': listing.make, 'model': listing.model,
                'year': listing.year, 'listing_id': listing.pk,
                'admin_note': admin_note,
            },
        )
        return f"Changes requested email sent to {owner.email}"
    except Exception as exc:
        logger.error("send_listing_changes_requested_email failed: %s", exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Lead email
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='notifications.tasks.send_new_lead_email')
def send_new_lead_email(self, lead_id: int) -> str:
    from leads.models import Lead
    try:
        lead = Lead.objects.select_related(
            'dealer', 'buyer', 'listing'
        ).get(pk=lead_id)
    except Lead.DoesNotExist:
        logger.warning("send_new_lead_email: Lead %s not found", lead_id)
        return f"Lead {lead_id} not found"

    dealer = lead.dealer
    if not _email_enabled(dealer):
        return f"Email notifications disabled for user {dealer.pk}"

    try:
        send_templated_email(
            to_email=dealer.email,
            subject=f"New inquiry for your {lead.listing.make} {lead.listing.model}",
            template_name='new_lead',
            context={
                'name':            dealer.name or dealer.email,
                'make':            lead.listing.make,
                'model':           lead.listing.model,
                'year':            lead.listing.year,
                'buyer_name':      lead.buyer.name or lead.buyer.email,
                'message_preview': (lead.message or '')[:200],
                'preferred_time':  lead.preferred_time or '',
            },
        )
        logger.info("New lead email sent to %s (lead %s)", dealer.email, lead_id)
        return f"New lead email sent to {dealer.email}"
    except Exception as exc:
        logger.error("send_new_lead_email failed for lead %s: %s", lead_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Message email
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='notifications.tasks.send_new_message_email')
def send_new_message_email(self, message_id: int) -> str:
    from messaging.models import Message
    try:
        msg = Message.objects.select_related(
            'sender',
            'conversation__buyer',
            'conversation__seller',
            'conversation__listing',
        ).get(pk=message_id)
    except Message.DoesNotExist:
        logger.warning("send_new_message_email: Message %s not found", message_id)
        return f"Message {message_id} not found"

    conv = msg.conversation
    recipient = conv.seller if msg.sender_id == conv.buyer_id else conv.buyer

    if not _email_enabled(recipient):
        return f"Email notifications disabled for user {recipient.pk}"

    listing = conv.listing
    try:
        send_templated_email(
            to_email=recipient.email,
            subject=f"New message from {msg.sender.name or msg.sender.email}",
            template_name='new_message',
            context={
                'name':            recipient.name or recipient.email,
                'sender_name':     msg.sender.name or msg.sender.email,
                'make':            listing.make,
                'model':           listing.model,
                'year':            listing.year,
                'message_preview': (msg.content or '')[:200],
                'conversation_id': conv.pk,
            },
        )
        logger.info("New message email sent to %s (message %s)", recipient.email, message_id)
        return f"New message email sent to {recipient.email}"
    except Exception as exc:
        logger.error("send_new_message_email failed for message %s: %s", message_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Appointment emails
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='notifications.tasks.send_appointment_confirmed_email')
def send_appointment_confirmed_email(self, appointment_id: int) -> str:
    from bookings.models import Appointment
    try:
        appt = Appointment.objects.select_related(
            'buyer', 'listing'
        ).get(pk=appointment_id)
    except Appointment.DoesNotExist:
        logger.warning("send_appointment_confirmed_email: Appointment %s not found", appointment_id)
        return f"Appointment {appointment_id} not found"

    buyer = appt.buyer
    if not _email_enabled(buyer):
        return f"Email notifications disabled for user {buyer.pk}"

    try:
        send_templated_email(
            to_email=buyer.email,
            subject=f"Your appointment for {appt.listing.make} {appt.listing.model} is confirmed!",
            template_name='appointment_confirmed',
            context={
                'name':             buyer.name or buyer.email,
                'make':             appt.listing.make,
                'model':            appt.listing.model,
                'year':             appt.listing.year,
                'listing_id':       appt.listing.pk,
                'appointment_date': appt.appointment_date.strftime('%A, %d %B %Y'),
                'appointment_time': appt.appointment_time.strftime('%I:%M %p'),
                'location':         appt.location or '',
                'seller_notes':     appt.seller_notes or '',
            },
        )
        logger.info("Appointment confirmed email sent to %s (appt %s)", buyer.email, appointment_id)
        return f"Appointment confirmed email sent to {buyer.email}"
    except Exception as exc:
        logger.error("send_appointment_confirmed_email failed for appt %s: %s", appointment_id, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='notifications.tasks.send_appointment_rejected_email')
def send_appointment_rejected_email(self, appointment_id: int) -> str:
    from bookings.models import Appointment
    try:
        appt = Appointment.objects.select_related(
            'buyer', 'listing'
        ).get(pk=appointment_id)
    except Appointment.DoesNotExist:
        logger.warning("send_appointment_rejected_email: Appointment %s not found", appointment_id)
        return f"Appointment {appointment_id} not found"

    buyer = appt.buyer
    if not _email_enabled(buyer):
        return f"Email notifications disabled for user {buyer.pk}"

    try:
        send_templated_email(
            to_email=buyer.email,
            subject=f"Appointment update for your {appt.listing.make} {appt.listing.model} viewing",
            template_name='appointment_rejected',
            context={
                'name':             buyer.name or buyer.email,
                'make':             appt.listing.make,
                'model':            appt.listing.model,
                'year':             appt.listing.year,
                'listing_id':       appt.listing.pk,
                'appointment_date': appt.appointment_date.strftime('%A, %d %B %Y'),
                'appointment_time': appt.appointment_time.strftime('%I:%M %p'),
            },
        )
        logger.info("Appointment rejected email sent to %s (appt %s)", buyer.email, appointment_id)
        return f"Appointment rejected email sent to {buyer.email}"
    except Exception as exc:
        logger.error("send_appointment_rejected_email failed for appt %s: %s", appointment_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Service booking emails
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='notifications.tasks.send_service_booking_email')
def send_service_booking_email(self, booking_id: int, status: str) -> str:
    from bookings.models import ServiceBooking
    try:
        booking = ServiceBooking.objects.select_related(
            'customer', 'workshop', 'service'
        ).get(pk=booking_id)
    except ServiceBooking.DoesNotExist:
        logger.warning("send_service_booking_email: ServiceBooking %s not found", booking_id)
        return f"ServiceBooking {booking_id} not found"

    customer = booking.customer
    if not customer or not customer.email:
        return "No customer email"
    if not _email_enabled(customer):
        return f"Email notifications disabled for user {customer.pk}"

    template = 'service_booking_confirmed' if status == 'confirmed' else 'service_booking_completed'
    subject = (
        'Your service booking has been confirmed!'
        if status == 'confirmed'
        else 'Your vehicle service is complete!'
    )
    try:
        send_templated_email(
            to_email=customer.email,
            subject=subject,
            template_name=template,
            context={
                'workshop_name':  booking.workshop.name,
                'workshop_id':    booking.workshop.pk,
                'service_name':   booking.service.name if booking.service else None,
                'vehicle_make':   booking.vehicle_make,
                'vehicle_model':  booking.vehicle_model,
                'vehicle_year':   booking.vehicle_year,
                'vehicle_plate':  booking.vehicle_plate,
                'booking_date':   booking.booking_date,
                'booking_time':   str(booking.booking_time)[:5],
                'estimated_cost': booking.estimated_cost,
                'final_cost':     booking.final_cost,
                'notes':          booking.notes,
            },
        )
        logger.info("Service booking email (%s) sent to %s (booking %s)", status, customer.email, booking_id)
        return f"Service booking email ({status}) sent to {customer.email}"
    except Exception as exc:
        logger.error("send_service_booking_email failed for booking %s: %s", booking_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Weekly digest  (batch job — no per-task retry; individual failures are caught inline)
# ---------------------------------------------------------------------------

@shared_task(name='notifications.tasks.send_weekly_digest')
def send_weekly_digest() -> str:
    """
    For every user with at least one SavedSearch(notify=True), find listings
    created in the last 7 days that match their filters.  Send one digest
    email per user (skipped if no matches or email_notifications is off).
    """
    from cars.models import Listing, SavedSearch
    from cars.filters import ListingFilter

    since = timezone.now() - timedelta(days=7)
    base_qs = Listing.objects.filter(
        status='approved',
        is_active=True,
        created_at__gte=since,
    ).select_related('owner').order_by('-created_at')

    searches = (
        SavedSearch.objects
        .filter(notify=True)
        .select_related('user')
    )

    user_data: dict[int, dict] = {}
    for search in searches:
        user = search.user
        if user.pk not in user_data:
            user_data[user.pk] = {'user': user, 'saved_searches': []}

        filtered_qs = ListingFilter(search.filters, queryset=base_qs).qs
        matches = list(filtered_qs[:3])
        total   = filtered_qs.count()

        if total > 0:
            user_data[user.pk]['saved_searches'].append({
                'name':      search.name,
                'new_count': total,
                'listings':  matches,
            })

    sent_count = 0
    for uid, data in user_data.items():
        user = data['user']
        if not data['saved_searches']:
            continue
        if not _email_enabled(user):
            continue

        total_new = sum(s['new_count'] for s in data['saved_searches'])
        try:
            send_templated_email(
                to_email=user.email,
                subject=f"Your weekly car update — {total_new} new match{'es' if total_new != 1 else ''}!",
                template_name='weekly_digest',
                context={
                    'name':               user.name or user.email,
                    'saved_searches':     data['saved_searches'],
                    'new_listings_count': total_new,
                },
            )
            sent_count += 1
        except Exception as exc:
            logger.error("send_weekly_digest failed for user %s: %s", uid, exc)

    return f"Weekly digest sent to {sent_count} user(s)"


# ═══════════════════════════════════════════════════════════════════════════════
# v3 Import Order Email Tasks (Phase 3.4)
# ═══════════════════════════════════════════════════════════════════════════════

def _check_email_pref(user, field='order_updates_email'):
    """Return True if user has email notifications enabled for this type."""
    try:
        prefs = NotificationPreference.objects.get(user=user)
        if not prefs.email_notifications:
            return False
        return getattr(prefs, field, True)
    except NotificationPreference.DoesNotExist:
        return True  # default on


def _order_context(order):
    """Build common context dict from an ImportOrder."""
    car = order.car
    return {
        'order_number': order.order_number,
        'order_id': order.pk,
        'year': car.year if car else '',
        'make': car.make if car else '',
        'model': car.model if car else '',
        'source_country': getattr(car, 'source_country', '') or '',
        'auction_source': getattr(car, 'auction_source', '') or '',
        'auction_lot_number': getattr(car, 'auction_lot_number', '') or '',
        'deposit_amount': str(order.deposit_amount or 0),
        'remaining_balance': str(order.remaining_balance or 0),
        'final_price': str(order.total_price or 0),
    }


@shared_task(name='notifications.tasks.send_order_created_email')
def send_order_created_email(order_id):
    """Sent to importer when buyer creates an order."""
    from orders.models import ImportOrder
    try:
        order = ImportOrder.objects.select_related('car', 'buyer', 'importer').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.warning("send_order_created_email: order %s not found", order_id)
        return

    if not _check_email_pref(order.importer):
        logger.info("send_order_created_email: importer %s has email off", order.importer_id)
        return

    buyer = order.buyer
    ctx = _order_context(order)
    ctx.update({
        'placed_at': order.created_at.strftime('%d %b %Y, %H:%M') if order.created_at else '',
        'buyer_initials': ''.join(w[0] for w in (buyer.name or buyer.email).split()[:2]).upper(),
        'buyer_name': buyer.name or buyer.email,
        'buyer_email': buyer.email,
        'buyer_phone': buyer.phone or '—',
        'car_price_source_currency_display': f"{order.car.source_price or order.car.price} {order.car.source_currency or 'SAR'}".strip() if order.car else '',
        'car_price_sar': str(order.car.price) if order.car else '',
        'shipping_cost': str(getattr(order.car, 'shipping_cost', 0) or 0),
        'customs_duty': str(getattr(order.car, 'customs_duty_amount', 0) or 0),
        'vat_amount': str(getattr(order.car, 'vat_amount', 0) or 0),
        'fees_total': str(getattr(order.car, 'inspection_fee', 0) or 0),
    })

    try:
        send_templated_email(
            to_email=order.importer.email,
            subject=f"New order · {ctx['year']} {ctx['make']} {ctx['model']} · {ctx['order_number']}",
            template_name='order_created',
            context=ctx,
        )
    except Exception as exc:
        logger.error("send_order_created_email failed for order %s: %s", order_id, exc)


@shared_task(name='notifications.tasks.send_order_confirmed_email')
def send_order_confirmed_email(order_id):
    """Sent to buyer when importer confirms the order."""
    from orders.models import ImportOrder
    try:
        order = ImportOrder.objects.select_related('car', 'buyer', 'importer').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.warning("send_order_confirmed_email: order %s not found", order_id)
        return

    if not _check_email_pref(order.buyer):
        return

    car = order.car
    ctx = _order_context(order)
    ctx.update({
        'importer_business_name': getattr(order.importer, 'importer_profile', None) and order.importer.importer_profile.business_name or order.importer.name,
        'car_spec_line': f"{getattr(car, 'source_country', '')} · {getattr(car, 'spec_origin', '')}".strip(' ·') if car else '',
        'payment_url': f"{ctx.get('frontend_url', '')}/orders/{order.pk}/pay-deposit",
        'auction_source_display': getattr(car, 'auction_source', '') or 'auction',
        'source_country_display': getattr(car, 'source_country', '') or '',
        'port_of_entry_display': getattr(car, 'port_of_entry', '') or 'Jeddah',
        'estimated_delivery_date': str(order.estimated_delivery_date or 'TBD'),
    })

    try:
        send_templated_email(
            to_email=order.buyer.email,
            subject='Your import is ready to start · pay deposit',
            template_name='order_confirmed',
            context=ctx,
        )
    except Exception as exc:
        logger.error("send_order_confirmed_email failed for order %s: %s", order_id, exc)


@shared_task(name='notifications.tasks.send_deposit_received_email')
def send_deposit_received_email(order_id, transaction_id='—', payment_method_display='Card'):
    """Sent to buyer after successful deposit payment."""
    from orders.models import ImportOrder
    try:
        order = ImportOrder.objects.select_related('car', 'buyer', 'importer').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.warning("send_deposit_received_email: order %s not found", order_id)
        return

    if not _check_email_pref(order.buyer):
        return

    car = order.car
    ctx = _order_context(order)
    ctx.update({
        'transaction_id': transaction_id,
        'payment_method_display': payment_method_display,
        'payment_date': (order.deposit_paid_at or timezone.now()).strftime('%d %b %Y'),
        'importer_business_name': getattr(order.importer, 'importer_profile', None) and order.importer.importer_profile.business_name or order.importer.name,
        'auction_source_display': getattr(car, 'auction_source', '') or 'auction',
        'source_country_display': getattr(car, 'source_country', '') or '',
        'model': car.model if car else '',
    })

    try:
        send_templated_email(
            to_email=order.buyer.email,
            subject='Deposit received · sourcing your car has begun',
            template_name='deposit_received',
            context=ctx,
        )
    except Exception as exc:
        logger.error("send_deposit_received_email failed for order %s: %s", order_id, exc)


# ---------------------------------------------------------------------------
# Milestone config for the generic status update email
# ---------------------------------------------------------------------------

_PROGRESS_STEPS = [
    ('order_confirmed', 'Order confirmed'),
    ('deposit_paid', 'Deposit paid'),
    ('car_purchased', 'Car purchased'),
    ('shipping', 'Shipping'),
    ('arrived_port', 'Arrived at port'),
    ('customs_cleared', 'Customs cleared'),
    ('inspection_passed', 'Inspection passed'),
    ('ready', 'Ready for delivery'),
]

MILESTONE_CONFIG = {
    'car_purchased': {
        'number': 3,
        'label': 'PURCHASED',
        'subject': 'Your car has been purchased · {order_number}',
        'headline': 'Your car has been purchased.',
        'intro': '{importer} secured your {year} {make} {model} at the {auction} auction. Next stop: shipping preparation.',
        'detail_keys': [('Auction', 'auction_source', False), ('Lot number', 'auction_lot_number', True), ('Purchase price', 'car_price_display', False), ('Date', 'event_date', False), ('VIN', 'vin', True)],
        'cta_label': 'View purchase receipt →',
        'caption': "We'll notify you when the car is loaded onto a vessel.",
    },
    'shipped': {
        'number': 4,
        'label': 'SHIPPING',
        'subject': 'Your car is shipping · {vessel_name} departed {port_of_origin}',
        'headline': 'Your car is on the water.',
        'intro': 'The {vessel_name} departed {port_of_origin} today carrying your {year} {make} {model}. Expected arrival at {port_of_entry} is {eta_range}.',
        'detail_keys': [('Vessel', 'vessel_name', False), ('Shipping line', 'shipping_line', False), ('Container', 'container_number', True), ('Bill of lading', 'bill_of_lading_number', True), ('Departed', 'event_date', False), ('Arriving', 'eta_range', False)],
        'cta_label': 'Track vessel in real time →',
        'caption': "We'll email you the moment your car arrives at {port_of_entry}.",
    },
    'arrived_port': {
        'number': 5,
        'label': 'ARRIVED AT PORT',
        'subject': 'Your car arrived in Saudi Arabia · {order_number}',
        'headline': 'Your car has reached Saudi Arabia.',
        'intro': 'The {vessel_name} docked at {port_of_entry} today. Customs clearance begins next — typically 3 to 5 business days.',
        'detail_keys': [('Port', 'port_of_entry', False), ('Arrival date', 'event_date', False), ('Container', 'container_number', True), ('Status', 'status_display', False)],
        'cta_label': 'View port arrival documents →',
        'caption': '{importer} is now handling customs paperwork.',
    },
    'customs_cleared': {
        'number': 6,
        'label': 'CUSTOMS CLEARED',
        'subject': 'Customs cleared · inspection up next',
        'headline': 'Cleared by Saudi customs.',
        'intro': 'All duties and taxes have been paid. Your car moves to the SASO vehicle inspection stage next.',
        'detail_keys': [('Declaration number', 'customs_declaration_number', True), ('Clearance date', 'event_date', False), ('Duties paid', 'customs_duty_display', False), ('VAT paid', 'vat_display', False)],
        'cta_label': 'View customs documents →',
        'caption': 'SASO inspection usually completes within 1–2 business days.',
    },
    'inspection_passed': {
        'number': 7,
        'label': 'INSPECTION PASSED',
        'subject': 'Inspection passed · your car is almost ready',
        'headline': 'Inspection complete.',
        'intro': 'Your car passed the full SASO inspection. The conformity certificate has been issued and the car is being prepared for delivery.',
        'detail_keys': [('Certificate number', 'conformity_certificate_number', True), ('Inspection date', 'event_date', False), ('Center', 'inspection_center', False), ('Result', 'inspection_result', False)],
        'cta_label': 'View inspection report →',
        'caption': 'Final delivery instructions arrive in your next email.',
    },
}


@shared_task(name='notifications.tasks.send_order_status_update_email')
def send_order_status_update_email(order_id, milestone_key):
    """Generic status-update email using the milestone config."""
    from orders.models import ImportOrder
    try:
        order = ImportOrder.objects.select_related('car', 'buyer', 'importer').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.warning("send_order_status_update_email: order %s not found", order_id)
        return

    if not _check_email_pref(order.buyer):
        return

    config = MILESTONE_CONFIG.get(milestone_key)
    if not config:
        logger.warning("Unknown milestone_key: %s", milestone_key)
        return

    car = order.car
    importer_name = getattr(order.importer, 'importer_profile', None) and order.importer.importer_profile.business_name or order.importer.name
    fmt = {
        'order_number': order.order_number,
        'year': car.year if car else '',
        'make': car.make if car else '',
        'model': car.model if car else '',
        'importer': importer_name,
        'auction': getattr(car, 'auction_source', '') or '',
        'vessel_name': getattr(car, 'vessel_name', '') or '',
        'port_of_origin': getattr(car, 'port_of_origin', '') or '',
        'port_of_entry': getattr(car, 'port_of_entry', '') or 'Jeddah',
        'eta_range': str(order.estimated_delivery_date or 'TBD'),
    }

    # Build detail rows
    detail_values = {
        'auction_source': getattr(car, 'auction_source', '') or '',
        'auction_lot_number': getattr(car, 'auction_lot_number', '') or '',
        'car_price_display': f"SAR {car.price}" if car else '',
        'event_date': timezone.now().strftime('%d %b %Y'),
        'vin': getattr(car, 'vin', '') or '',
        'vessel_name': getattr(car, 'vessel_name', '') or '',
        'shipping_line': getattr(car, 'shipping_line', '') or '',
        'container_number': getattr(car, 'container_number', '') or '',
        'bill_of_lading_number': getattr(car, 'bill_of_lading_number', '') or '',
        'eta_range': fmt['eta_range'],
        'port_of_entry': fmt['port_of_entry'],
        'status_display': 'In customs processing',
        'customs_declaration_number': getattr(car, 'customs_declaration_number', '') or '',
        'customs_duty_display': f"SAR {getattr(car, 'customs_duty_amount', 0) or 0}",
        'vat_display': f"SAR {getattr(car, 'vat_amount', 0) or 0}",
        'conformity_certificate_number': getattr(car, 'conformity_certificate_number', '') or '',
        'inspection_center': 'SASO',
        'inspection_result': 'Passed',
    }
    detail_rows = [{'label': label, 'value': detail_values.get(key, ''), 'mono': mono} for label, key, mono in config['detail_keys']]

    # Build progress steps
    step_order = [s[0] for s in _PROGRESS_STEPS]
    current_idx = step_order.index(milestone_key) if milestone_key in step_order else -1
    progress_steps = []
    for i, (key, label) in enumerate(_PROGRESS_STEPS):
        if i < current_idx:
            state = 'completed'
        elif i == current_idx:
            state = 'current'
        else:
            state = 'upcoming'
        progress_steps.append({'label': label, 'state': state, 'date': ''})

    ctx = _order_context(order)
    ctx.update({
        'subject_line': config['subject'].format(**fmt),
        'milestone_number': config['number'],
        'milestone_label': config['label'],
        'headline': config['headline'],
        'intro_paragraph': config['intro'].format(**fmt),
        'detail_rows': detail_rows,
        'progress_steps': progress_steps,
        'cta_label': config['cta_label'],
        'cta_url': f"{ctx.get('frontend_url', '')}/orders/{order.pk}",
        'caption': config['caption'].format(**fmt),
    })

    try:
        send_templated_email(
            to_email=order.buyer.email,
            subject=ctx['subject_line'],
            template_name='order_status_update',
            context=ctx,
        )
    except Exception as exc:
        logger.error("send_order_status_update_email failed for order %s (%s): %s", order_id, milestone_key, exc)


@shared_task(name='notifications.tasks.send_ready_for_delivery_email')
def send_ready_for_delivery_email(order_id):
    """Sent to buyer when car passes inspection and is ready."""
    from orders.models import ImportOrder
    try:
        order = ImportOrder.objects.select_related('car', 'buyer', 'importer').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.warning("send_ready_for_delivery_email: order %s not found", order_id)
        return

    if not _check_email_pref(order.buyer):
        return

    car = order.car
    profile = getattr(order.importer, 'importer_profile', None)
    ctx = _order_context(order)
    ctx.update({
        'importer_business_name': profile.business_name if profile else order.importer.name,
        'importer_address': profile.address if profile else '',
        'working_hours': 'Sun–Thu, 9 AM – 6 PM',
        'ready_date': timezone.now().strftime('%d %b %Y'),
        'delivery_fee': '500',
        'buyer_city': getattr(order.buyer, 'city_obj', None) and order.buyer.city_obj.name or 'your city',
        'delivery_window': '2–3 business days',
        'payment_url': f"{ctx.get('frontend_url', '')}/orders/{order.pk}/pay-balance",
        'odometer': str(car.mileage) if car else '0',
        'saso_cert_number': getattr(car, 'conformity_certificate_number', '') or '—',
    })

    try:
        send_templated_email(
            to_email=order.buyer.email,
            subject='Your car is ready · choose pickup or delivery',
            template_name='ready_for_delivery',
            context=ctx,
        )
    except Exception as exc:
        logger.error("send_ready_for_delivery_email failed for order %s: %s", order_id, exc)


@shared_task(name='notifications.tasks.send_order_cancelled_email')
def send_order_cancelled_email(order_id):
    """Sent to BOTH buyer and importer when order is cancelled."""
    from orders.models import ImportOrder
    try:
        order = ImportOrder.objects.select_related('car', 'buyer', 'importer').get(pk=order_id)
    except ImportOrder.DoesNotExist:
        logger.warning("send_order_cancelled_email: order %s not found", order_id)
        return

    car = order.car
    base = _order_context(order)
    base.update({
        'cancelled_at': (order.cancelled_at or timezone.now()).strftime('%d %b %Y'),
        'cancellation_reason': order.cancellation_reason or '',
        'payment_method_display': 'Original payment method',
        'refund_initiated_date': timezone.now().strftime('%d %b %Y'),
        'refund_arrives_by_date': (timezone.now() + timedelta(days=7)).strftime('%d %b %Y'),
        'car_id': car.pk if car else '',
    })

    # Send to buyer
    if _check_email_pref(order.buyer):
        try:
            ctx = dict(base, recipient_role='buyer')
            send_templated_email(
                to_email=order.buyer.email,
                subject='Order cancelled · refund on the way',
                template_name='order_cancelled',
                context=ctx,
            )
        except Exception as exc:
            logger.error("send_order_cancelled_email (buyer) failed for order %s: %s", order_id, exc)

    # Send to importer
    if _check_email_pref(order.importer):
        try:
            ctx = dict(base, recipient_role='importer')
            send_templated_email(
                to_email=order.importer.email,
                subject=f"Order {order.order_number} cancelled by buyer",
                template_name='order_cancelled',
                context=ctx,
            )
        except Exception as exc:
            logger.error("send_order_cancelled_email (importer) failed for order %s: %s", order_id, exc)

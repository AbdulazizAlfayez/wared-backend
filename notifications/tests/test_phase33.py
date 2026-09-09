"""
Phase 3.3 — Email System Tests

Covers:
  1. send_templated_email helper
  2. accounts.tasks: send_welcome_email, send_password_reset_email
  3. notifications.tasks: all 7 Celery tasks
  4. View integrations: .delay() is called at the right moments
"""

import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

User = get_user_model()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, role='buyer', name='Test User'):
    return User.objects.create_user(
        email=email,
        password='testpass123',
        name=name,
        role=role,
    )


def make_listing(owner, status='approved'):
    from cars.models import Listing
    return Listing.objects.create(
        title='2022 Toyota Camry',
        make='Toyota',
        model='Camry',
        year=2022,
        price=85000,
        mileage=30000,
        city='Riyadh',
        owner=owner,
        status=status,
        is_active=True,
        fuel_type='petrol',
        transmission='automatic',
    )


# ---------------------------------------------------------------------------
# 1. send_templated_email helper
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   DEFAULT_FROM_EMAIL='noreply@wared.sa')
class SendTemplatedEmailTests(TestCase):

    def test_sends_multipart_email(self):
        from notifications.emails import send_templated_email
        send_templated_email(
            to_email='buyer@test.com',
            subject='Test Subject',
            template_name='welcome',
            context={'name': 'Aziz'},
        )
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['buyer@test.com'])
        self.assertEqual(msg.subject, 'Test Subject')
        self.assertEqual(msg.from_email, 'noreply@wared.sa')
        # Has both plain-text and HTML alternatives
        self.assertTrue(len(msg.alternatives) >= 1)
        html_body = msg.alternatives[0][0]
        self.assertIn('Aziz', html_body)

    def test_injects_frontend_url_and_current_year(self):
        from notifications.emails import send_templated_email
        send_templated_email(
            to_email='x@x.com',
            subject='s',
            template_name='welcome',
            context={'name': 'X'},
        )
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertIn('2026', html_body)

    def test_all_templates_render_without_error(self):
        from notifications.emails import send_templated_email
        templates_and_contexts = [
            ('welcome',               {'name': 'Aziz'}),
            ('password_reset',        {'name': 'Aziz', 'reset_url': 'http://example.com/reset/abc'}),
            ('listing_approved',      {'name': 'Aziz', 'make': 'Toyota', 'model': 'Camry', 'year': 2022, 'price': '85000', 'listing_id': 1}),
            ('listing_rejected',      {'name': 'Aziz', 'make': 'Toyota', 'model': 'Camry', 'year': 2022, 'listing_id': 1, 'rejection_reason': 'Bad photos'}),
            ('new_lead',              {'name': 'Dealer', 'make': 'Toyota', 'model': 'Camry', 'year': 2022, 'buyer_name': 'Buyer', 'message_preview': 'Hello', 'preferred_time': 'Morning'}),
            ('new_message',           {'name': 'User', 'sender_name': 'Sender', 'make': 'Toyota', 'model': 'Camry', 'year': 2022, 'message_preview': 'Hi', 'conversation_id': 1}),
            ('appointment_confirmed', {'name': 'Buyer', 'make': 'Toyota', 'model': 'Camry', 'year': 2022, 'listing_id': 1, 'appointment_date': 'Monday, 20 March 2026', 'appointment_time': '10:00 AM', 'location': 'Riyadh', 'seller_notes': ''}),
            ('appointment_rejected',  {'name': 'Buyer', 'make': 'Toyota', 'model': 'Camry', 'year': 2022, 'listing_id': 1, 'appointment_date': 'Monday, 20 March 2026', 'appointment_time': '10:00 AM'}),
        ]
        for template_name, ctx in templates_and_contexts:
            with self.subTest(template=template_name):
                mail.outbox.clear()
                send_templated_email(
                    to_email='test@test.com',
                    subject='Test',
                    template_name=template_name,
                    context=ctx,
                )
                self.assertEqual(len(mail.outbox), 1, f"No email for {template_name}")


# ---------------------------------------------------------------------------
# 2. accounts.tasks
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   CELERY_TASK_ALWAYS_EAGER=True)
class AccountsTaskTests(TestCase):

    def setUp(self):
        self.user = make_user('user@test.com', name='Aziz')

    def test_send_welcome_email_sends_to_user(self):
        from accounts.tasks import send_welcome_email
        send_welcome_email(self.user.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.email, mail.outbox[0].to)
        self.assertIn('Welcome', mail.outbox[0].subject)

    def test_send_welcome_email_unknown_user_returns_message(self):
        from accounts.tasks import send_welcome_email
        result = send_welcome_email(99999)
        self.assertIn('not found', result)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_password_reset_email_sends_reset_url(self):
        from accounts.tasks import send_password_reset_email
        reset_url = 'http://localhost:3000/auth/reset/abc123'
        send_password_reset_email(self.user.pk, reset_url)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertIn(self.user.email, msg.to)
        self.assertIn('Reset', msg.subject)
        html_body = msg.alternatives[0][0]
        self.assertIn(reset_url, html_body)

    def test_send_password_reset_email_unknown_user(self):
        from accounts.tasks import send_password_reset_email
        result = send_password_reset_email(99999, 'http://example.com')
        self.assertIn('not found', result)
        self.assertEqual(len(mail.outbox), 0)


# ---------------------------------------------------------------------------
# 3. notifications.tasks
# ---------------------------------------------------------------------------

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   CELERY_TASK_ALWAYS_EAGER=True)
class ListingEmailTaskTests(TestCase):

    def setUp(self):
        self.owner = make_user('owner@test.com', role='importer', name='Owner')
        self.listing = make_listing(self.owner, status='approved')

    def test_send_listing_approved_email(self):
        from notifications.tasks import send_listing_approved_email
        result = send_listing_approved_email(self.listing.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.owner.email, mail.outbox[0].to)
        self.assertIn('approved', mail.outbox[0].subject.lower())
        self.assertIn('sent to', result)

    def test_send_listing_approved_email_email_disabled(self):
        from notifications.models import NotificationPreference
        from notifications.tasks import send_listing_approved_email
        NotificationPreference.objects.create(user=self.owner, email_notifications=False)
        result = send_listing_approved_email(self.listing.pk)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('disabled', result)

    def test_send_listing_approved_email_not_found(self):
        from notifications.tasks import send_listing_approved_email
        result = send_listing_approved_email(99999)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('not found', result)

    def test_send_listing_rejected_email(self):
        from notifications.tasks import send_listing_rejected_email
        result = send_listing_rejected_email(self.listing.pk, 'Photos too dark')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.owner.email, mail.outbox[0].to)
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertIn('Photos too dark', html_body)
        self.assertIn('sent to', result)

    def test_send_listing_rejected_email_email_disabled(self):
        from notifications.models import NotificationPreference
        from notifications.tasks import send_listing_rejected_email
        NotificationPreference.objects.create(user=self.owner, email_notifications=False)
        result = send_listing_rejected_email(self.listing.pk, 'reason')
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('disabled', result)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   CELERY_TASK_ALWAYS_EAGER=True)
class LeadEmailTaskTests(TestCase):

    def setUp(self):
        self.dealer = make_user('dealer@test.com', role='importer', name='Dealer')
        self.buyer  = make_user('buyer@test.com',  role='buyer',  name='Buyer')
        self.listing = make_listing(self.dealer)
        from leads.models import Lead
        self.lead = Lead.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            dealer=self.dealer,
            message='I am interested',
            source='listing_page',
        )

    def test_send_new_lead_email(self):
        from notifications.tasks import send_new_lead_email
        result = send_new_lead_email(self.lead.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.dealer.email, mail.outbox[0].to)
        self.assertIn('sent to', result)

    def test_send_new_lead_email_email_disabled(self):
        from notifications.models import NotificationPreference
        from notifications.tasks import send_new_lead_email
        NotificationPreference.objects.create(user=self.dealer, email_notifications=False)
        result = send_new_lead_email(self.lead.pk)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('disabled', result)

    def test_send_new_lead_email_not_found(self):
        from notifications.tasks import send_new_lead_email
        result = send_new_lead_email(99999)
        self.assertIn('not found', result)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   CELERY_TASK_ALWAYS_EAGER=True)
class MessageEmailTaskTests(TestCase):

    def setUp(self):
        self.seller = make_user('seller@test.com', role='importer', name='Seller')
        self.buyer  = make_user('buyer@test.com',  role='buyer',  name='Buyer')
        self.listing = make_listing(self.seller)
        from messaging.models import Conversation, Message
        self.conv = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
        )
        self.msg = Message.objects.create(
            conversation=self.conv,
            sender=self.buyer,
            content='Hello, is this available?',
        )

    def test_send_new_message_email_to_seller(self):
        """Buyer sends message → seller gets email."""
        from notifications.tasks import send_new_message_email
        result = send_new_message_email(self.msg.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.seller.email, mail.outbox[0].to)
        self.assertIn('sent to', result)

    def test_send_new_message_email_recipient_is_non_sender(self):
        """Seller sends reply → buyer gets email."""
        from messaging.models import Message
        from notifications.tasks import send_new_message_email
        reply = Message.objects.create(
            conversation=self.conv,
            sender=self.seller,
            content='Yes it is available.',
        )
        result = send_new_message_email(reply.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.buyer.email, mail.outbox[0].to)

    def test_send_new_message_email_disabled(self):
        from notifications.models import NotificationPreference
        from notifications.tasks import send_new_message_email
        NotificationPreference.objects.create(user=self.seller, email_notifications=False)
        result = send_new_message_email(self.msg.pk)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('disabled', result)

    def test_send_new_message_email_not_found(self):
        from notifications.tasks import send_new_message_email
        result = send_new_message_email(99999)
        self.assertIn('not found', result)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   CELERY_TASK_ALWAYS_EAGER=True)
class AppointmentEmailTaskTests(TestCase):

    def setUp(self):
        self.seller = make_user('seller@test.com', role='importer', name='Seller')
        self.buyer  = make_user('buyer@test.com',  role='buyer',  name='Buyer')
        self.listing = make_listing(self.seller)
        from bookings.models import Appointment
        future_date = datetime.date.today() + datetime.timedelta(days=10)
        self.appt = Appointment.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            appointment_date=future_date,
            appointment_time=datetime.time(10, 0),
            location='Riyadh showroom',
        )

    def test_send_appointment_confirmed_email(self):
        from notifications.tasks import send_appointment_confirmed_email
        result = send_appointment_confirmed_email(self.appt.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.buyer.email, mail.outbox[0].to)
        self.assertIn('confirmed', mail.outbox[0].subject.lower())
        self.assertIn('sent to', result)

    def test_send_appointment_rejected_email(self):
        from notifications.tasks import send_appointment_rejected_email
        result = send_appointment_rejected_email(self.appt.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.buyer.email, mail.outbox[0].to)
        self.assertIn('sent to', result)

    def test_send_appointment_confirmed_email_disabled(self):
        from notifications.models import NotificationPreference
        from notifications.tasks import send_appointment_confirmed_email
        NotificationPreference.objects.create(user=self.buyer, email_notifications=False)
        result = send_appointment_confirmed_email(self.appt.pk)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('disabled', result)

    def test_send_appointment_not_found(self):
        from notifications.tasks import send_appointment_confirmed_email
        result = send_appointment_confirmed_email(99999)
        self.assertIn('not found', result)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   CELERY_TASK_ALWAYS_EAGER=True)
class WeeklyDigestTaskTests(TestCase):

    def setUp(self):
        self.user = make_user('subscriber@test.com', name='Sub')
        self.dealer = make_user('dealer@test.com', role='importer', name='Dealer')
        self.listing = make_listing(self.dealer, status='approved')

    def test_digest_sends_when_matches_exist(self):
        from cars.models import SavedSearch
        from notifications.tasks import send_weekly_digest
        SavedSearch.objects.create(
            user=self.user,
            name='Toyota Search',
            filters={'make': 'Toyota'},
            notify=True,
        )
        result = send_weekly_digest()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.email, mail.outbox[0].to)
        self.assertIn('1', result)

    def test_digest_skips_when_no_matches(self):
        from cars.models import SavedSearch
        from notifications.tasks import send_weekly_digest
        SavedSearch.objects.create(
            user=self.user,
            name='Bentley Search',
            filters={'make': 'Bentley'},
            notify=True,
        )
        result = send_weekly_digest()
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('0', result)

    def test_digest_skips_notify_false(self):
        from cars.models import SavedSearch
        from notifications.tasks import send_weekly_digest
        SavedSearch.objects.create(
            user=self.user,
            name='Toyota Search',
            filters={'make': 'Toyota'},
            notify=False,
        )
        result = send_weekly_digest()
        self.assertEqual(len(mail.outbox), 0)

    def test_digest_skips_email_disabled(self):
        from cars.models import SavedSearch
        from notifications.models import NotificationPreference
        from notifications.tasks import send_weekly_digest
        NotificationPreference.objects.create(user=self.user, email_notifications=False)
        SavedSearch.objects.create(
            user=self.user,
            name='Toyota Search',
            filters={'make': 'Toyota'},
            notify=True,
        )
        result = send_weekly_digest()
        self.assertEqual(len(mail.outbox), 0)

    def test_digest_sends_one_email_per_user_multiple_searches(self):
        from cars.models import SavedSearch
        from notifications.tasks import send_weekly_digest
        SavedSearch.objects.create(user=self.user, name='Toyota', filters={'make': 'Toyota'}, notify=True)
        SavedSearch.objects.create(user=self.user, name='2022 cars', filters={'year': 2022}, notify=True)
        result = send_weekly_digest()
        # Both searches match — but only one email per user
        self.assertEqual(len(mail.outbox), 1)


# ---------------------------------------------------------------------------
# 4. View integration tests — .delay() is called
# ---------------------------------------------------------------------------

class ListingViewEmailIntegrationTests(TestCase):

    def setUp(self):
        self.admin  = make_user('admin@test.com', role='admin', name='Admin')
        self.dealer = make_user('dealer@test.com', role='importer', name='Dealer')
        self.listing = make_listing(self.dealer, status='pending')
        self.client.force_login(self.admin)

    @patch('notifications.tasks.send_listing_approved_email.delay')
    def test_approve_listing_triggers_email_task(self, mock_delay):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.patch(f'/api/listings/{self.listing.pk}/approve/')
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(self.listing.pk)

    @patch('notifications.tasks.send_listing_rejected_email.delay')
    def test_reject_listing_triggers_email_task(self, mock_delay):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.admin)
        response = client.patch(f'/api/listings/{self.listing.pk}/reject/', {'rejection_reason': 'Bad photos'})
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(self.listing.pk, 'Bad photos')


class LeadViewEmailIntegrationTests(TestCase):

    def setUp(self):
        self.dealer = make_user('dealer@test.com', role='importer', name='Dealer')
        self.buyer  = make_user('buyer@test.com',  role='buyer',  name='Buyer')
        self.listing = make_listing(self.dealer, status='approved')

    @patch('notifications.tasks.send_new_lead_email.delay')
    def test_create_lead_triggers_email_task(self, mock_delay):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.buyer)
        response = client.post('/api/leads/', {
            'listing': self.listing.pk,
            'message': 'Interested in this car',
            'source': 'listing_page',
        })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(mock_delay.called)
        lead_id = mock_delay.call_args[0][0]
        self.assertIsNotNone(lead_id)


class MessageViewEmailIntegrationTests(TestCase):

    def setUp(self):
        self.seller = make_user('seller@test.com', role='importer', name='Seller')
        self.buyer  = make_user('buyer@test.com',  role='buyer',  name='Buyer')
        self.listing = make_listing(self.seller, status='approved')
        from messaging.models import Conversation
        self.conv = Conversation.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
        )

    @patch('notifications.tasks.send_new_message_email.delay')
    def test_send_message_triggers_email_task(self, mock_delay):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.buyer)
        response = client.post(
            f'/api/conversations/{self.conv.pk}/messages/',
            {'content': 'Hello there!'},
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(mock_delay.called)


class AppointmentViewEmailIntegrationTests(TestCase):

    def setUp(self):
        self.seller = make_user('seller@test.com', role='importer', name='Seller')
        self.buyer  = make_user('buyer@test.com',  role='buyer',  name='Buyer')
        self.listing = make_listing(self.seller, status='approved')
        from bookings.models import Appointment
        future_date = datetime.date.today() + datetime.timedelta(days=10)
        self.appt = Appointment.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            appointment_date=future_date,
            appointment_time=datetime.time(10, 0),
        )

    @patch('notifications.tasks.send_appointment_confirmed_email.delay')
    def test_confirm_appointment_triggers_email_task(self, mock_delay):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.seller)
        response = client.patch(f'/api/appointments/{self.appt.pk}/', {'status': 'confirmed'})
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(self.appt.pk)

    @patch('notifications.tasks.send_appointment_rejected_email.delay')
    def test_reject_appointment_triggers_email_task(self, mock_delay):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.seller)
        response = client.patch(f'/api/appointments/{self.appt.pk}/', {'status': 'rejected'})
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(self.appt.pk)

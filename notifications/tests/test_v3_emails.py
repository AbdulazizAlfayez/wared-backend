"""
v3 Import Order Email Tests (Phase 3.4).
Run: python manage.py test notifications.tests.test_v3_emails --verbosity=2
"""
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from cars.models import Listing
from notifications.models import NotificationPreference
from orders.models import ImportOrder

User = get_user_model()

EAGER_CELERY = {
    'CELERY_TASK_ALWAYS_EAGER': True,
    'CELERY_TASK_EAGER_PROPAGATES': True,
}


def make_user(email, role='user', name='Test'):
    return User.objects.create_user(email=email, password='pass123', name=name, role=role)


def make_order():
    """Create a minimal ImportOrder with related objects."""
    buyer = make_user('buyer@test.com', 'user', 'Buyer')
    importer = make_user('importer@test.com', 'importer', 'Importer Co')
    car = Listing.objects.create(
        owner=importer, make='Toyota', model='Camry', year=2024,
        price=150000, mileage=5000, status='approved', is_active=True,
    )
    order = ImportOrder.objects.create(
        buyer=buyer, importer=importer, car=car, status='pending',
        deposit_amount=30000, total_price=150000,
    )
    return order


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OrderCreatedEmailTest(TestCase):
    def test_order_created_email_sent_to_importer(self):
        from notifications.tasks import send_order_created_email
        order = make_order()
        mail.outbox.clear()
        send_order_created_email(order.id)
        importer_emails = [m for m in mail.outbox if order.importer.email in m.to]
        self.assertTrue(len(importer_emails) >= 1)
        self.assertIn('New order', importer_emails[0].subject)
        self.assertIn(order.order_number, importer_emails[0].subject)


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OrderConfirmedEmailTest(TestCase):
    def test_order_confirmed_email_sent_to_buyer(self):
        from notifications.tasks import send_order_confirmed_email
        order = make_order()
        mail.outbox.clear()
        send_order_confirmed_email(order.id)
        buyer_emails = [m for m in mail.outbox if order.buyer.email in m.to]
        self.assertTrue(len(buyer_emails) >= 1)
        self.assertIn('deposit', buyer_emails[0].subject.lower())


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DepositReceivedEmailTest(TestCase):
    def test_deposit_received_email_sent(self):
        from notifications.tasks import send_deposit_received_email
        order = make_order()
        mail.outbox.clear()
        send_deposit_received_email(order.id, 'TXN-12345', 'Mada · ending 4521')
        deposit_emails = [m for m in mail.outbox if 'Deposit received' in m.subject]
        self.assertTrue(len(deposit_emails) >= 1)
        self.assertIn('TXN-12345', deposit_emails[0].body)


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class StatusUpdateEmailTest(TestCase):
    def test_status_update_email_each_milestone(self):
        from notifications.tasks import send_order_status_update_email
        order = make_order()
        milestones = ['car_purchased', 'shipped', 'arrived_port', 'customs_cleared', 'inspection_passed']
        for key in milestones:
            mail.outbox.clear()
            send_order_status_update_email(order.id, key)
            buyer_emails = [m for m in mail.outbox if order.buyer.email in m.to]
            self.assertTrue(len(buyer_emails) >= 1, f"Failed for milestone: {key}")


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ReadyForDeliveryEmailTest(TestCase):
    def test_ready_for_delivery_email(self):
        from notifications.tasks import send_ready_for_delivery_email
        order = make_order()
        mail.outbox.clear()
        send_ready_for_delivery_email(order.id)
        ready_emails = [m for m in mail.outbox if 'ready' in m.subject.lower()]
        self.assertTrue(len(ready_emails) >= 1)


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OrderCancelledEmailTest(TestCase):
    def test_order_cancelled_email_sent_to_both_parties(self):
        from notifications.tasks import send_order_cancelled_email
        order = make_order()
        order.cancellation_reason = 'Changed my mind'
        order.cancelled_at = timezone.now()
        order.save(update_fields=['cancellation_reason', 'cancelled_at'])
        mail.outbox.clear()
        send_order_cancelled_email(order.id)
        recipients = set()
        for m in mail.outbox:
            recipients.update(m.to)
        self.assertIn(order.buyer.email, recipients)
        self.assertIn(order.importer.email, recipients)


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailPreferenceTest(TestCase):
    def test_email_skipped_when_preference_off(self):
        from notifications.tasks import send_order_created_email
        order = make_order()
        NotificationPreference.objects.create(
            user=order.importer, email_notifications=False,
        )
        mail.outbox.clear()
        send_order_created_email(order.id)
        # Our task should NOT have sent to importer (pref off)
        importer_emails = [m for m in mail.outbox if order.importer.email in m.to and 'New order' in m.subject]
        self.assertEqual(len(importer_emails), 0)


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MissingOrderTest(TestCase):
    def test_email_handles_missing_order_gracefully(self):
        from notifications.tasks import send_order_created_email
        # Should not raise
        send_order_created_email(999999)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TemplateRenderTest(TestCase):
    def test_all_templates_render_without_errors(self):
        from django.template.loader import render_to_string
        templates = [
            'emails/markabah_base.html',
            'emails/order_created.html',
            'emails/order_confirmed.html',
            'emails/deposit_received.html',
            'emails/order_status_update.html',
            'emails/ready_for_delivery.html',
            'emails/order_cancelled.html',
        ]
        minimal_ctx = {
            'frontend_url': 'http://localhost:3000',
            'order_number': 'MK-2026-001',
            'order_id': 1,
            'year': 2024, 'make': 'Toyota', 'model': 'Camry',
            'deposit_amount': '30000', 'remaining_balance': '120000', 'final_price': '150000',
            'placed_at': '15 May 2026',
            'buyer_initials': 'AB', 'buyer_name': 'Ahmad', 'buyer_email': 'a@b.com', 'buyer_phone': '0501234567',
            'source_country': 'USA', 'auction_source': 'Copart', 'auction_lot_number': '12345',
            'car_price_source_currency_display': '22,000 USD', 'car_price_sar': '82500',
            'shipping_cost': '5000', 'customs_duty': '4125', 'vat_amount': '13744', 'fees_total': '1500',
            'importer_business_name': 'Import Co', 'car_spec_line': 'USA · Clean title',
            'payment_url': '#', 'auction_source_display': 'Copart',
            'source_country_display': 'USA', 'port_of_entry_display': 'Jeddah',
            'estimated_delivery_date': '15 July 2026',
            'transaction_id': 'TXN-001', 'payment_method_display': 'Mada', 'payment_date': '15 May 2026',
            'subject_line': 'Test', 'milestone_number': 3, 'milestone_label': 'PURCHASED',
            'headline': 'Test headline', 'intro_paragraph': 'Test intro',
            'detail_rows': [{'label': 'Test', 'value': 'Val', 'mono': False}],
            'progress_steps': [{'label': 'Step 1', 'state': 'completed', 'date': 'May 1'}, {'label': 'Step 2', 'state': 'current', 'date': 'May 15'}, {'label': 'Step 3', 'state': 'upcoming', 'date': ''}],
            'cta_label': 'View →', 'cta_url': '#', 'caption': 'Test caption',
            'importer_address': '123 St', 'working_hours': 'Sun-Thu',
            'ready_date': '15 May', 'delivery_fee': '500', 'buyer_city': 'Riyadh',
            'delivery_window': '2-3 days', 'odometer': '5000', 'saso_cert_number': 'SASO-001',
            'recipient_role': 'buyer', 'cancelled_at': '15 May 2026', 'cancellation_reason': 'Test',
            'payment_method_display': 'Mada', 'refund_initiated_date': '15 May', 'refund_arrives_by_date': '22 May',
            'car_id': 1, 'current_year': 2026,
        }
        for tmpl in templates:
            try:
                html = render_to_string(tmpl, minimal_ctx)
                self.assertTrue(len(html) > 100, f"Template {tmpl} rendered too short")
            except Exception as e:
                self.fail(f"Template {tmpl} failed to render: {e}")


@override_settings(**EAGER_CELERY, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailFormatTest(TestCase):
    def test_email_renders_both_html_and_plaintext(self):
        from notifications.tasks import send_order_created_email
        order = make_order()
        mail.outbox.clear()
        send_order_created_email(order.id)
        self.assertTrue(len(mail.outbox) >= 1)
        msg = mail.outbox[0]
        # Has HTML alternative
        self.assertTrue(len(msg.alternatives) > 0)
        html = msg.alternatives[0][0]
        self.assertIn('<!DOCTYPE html>', html)
        # Plain text body is not empty
        self.assertTrue(len(msg.body) > 50)

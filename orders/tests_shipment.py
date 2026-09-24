"""
Shipment number and carrier on an import order.

A car cannot become 'shipped' without something the buyer can follow it with.
These cover both paths — refused without a number, accepted with one — plus
what the number does once it is there: the timeline entry, the notification,
and the payload.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from cars.models import Listing
from notifications.models import Notification
from orders.models import ImportOrder
from payments.models import PaymentTransaction

User = get_user_model()


class ShipmentTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.importer = User.objects.create_user(
            email='ship-importer@test.sa', password='Passw0rd!x', name='Shipper',
            role='importer',
        )
        cls.buyer = User.objects.create_user(
            email='ship-buyer@test.sa', password='Passw0rd!x', name='Waiting Buyer',
            role='user',
        )
        cls.staff = User.objects.create_user(
            email='ship-staff@test.sa', password='Passw0rd!x', name='Staffer',
            role='admin', is_staff=True,
        )
        cls.car = Listing.objects.create(
            owner=cls.importer, make='Nissan', model='Patrol', year=2024,
            price=280000, city='Riyadh', status='approved',
        )

    def setUp(self):
        self.client = APIClient()
        self.order = ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            status='preparing_shipment', total_price=280000,
        )
        # 'shipped' is payment-gated; without this the 409 fires first and
        # these tests would never reach the rule under test.
        PaymentTransaction.objects.create(
            order=self.order, user=self.buyer, amount=280000,
            payment_type='balance', method='bank_transfer', status='succeeded',
        )
        self.url = f'/api/orders/{self.order.pk}/update-status/'

    def ship(self, user=None, **body):
        self.client.force_authenticate(user=user or self.importer)
        payload = {'status': 'shipped'}
        payload.update(body)
        return self.client.patch(self.url, payload, format='json')


class ShipmentNumberRequiredTests(ShipmentTestBase):
    def test_shipping_without_a_number_is_refused(self):
        response = self.ship()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data['shipment_number'][0],
            'Shipment number is required when marking as shipped.',
        )

    def test_the_refusal_is_bilingual_and_carries_a_code(self):
        response = self.ship()
        self.assertEqual(response.data['code'], 'shipment_number_required')
        self.assertTrue(str(response.data['detail_ar']).strip())

    def test_a_blank_number_is_not_a_number(self):
        response = self.ship(shipment_number='   ')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_order_does_not_move_when_it_is_refused(self):
        self.ship()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'preparing_shipment')

    def test_no_timeline_entry_is_written_when_it_is_refused(self):
        self.ship()
        self.assertFalse(self.order.timeline_events.filter(event_type='shipped').exists())

    def test_shipping_with_a_number_succeeds(self):
        response = self.ship(shipment_number='MSK123456789', carrier='Maersk')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'shipped')
        self.assertEqual(self.order.shipment_number, 'MSK123456789')
        self.assertEqual(self.order.carrier, 'Maersk')

    def test_the_carrier_is_optional(self):
        response = self.ship(shipment_number='ABC123')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.carrier, '')

    def test_a_number_already_on_the_order_is_enough(self):
        """Correcting a carrier name should not mean retyping the number."""
        self.order.shipment_number = 'PRESET999'
        self.order.save(update_fields=['shipment_number'])
        response = self.ship(carrier='CMA CGM')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipment_number, 'PRESET999')

    def test_whitespace_is_trimmed(self):
        self.ship(shipment_number='  SPACE1  ', carrier='  Line  ')
        self.order.refresh_from_db()
        self.assertEqual(self.order.shipment_number, 'SPACE1')
        self.assertEqual(self.order.carrier, 'Line')

    def test_staff_forcing_past_the_gate_are_not_asked_for_one(self):
        """
        `force` exists to unstick an order whose paperwork is what is missing.
        """
        response = self.ship(user=self.staff, force=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'shipped')
        self.assertEqual(self.order.shipment_number, '')

    def test_an_importer_cannot_force_their_way_past_it(self):
        response = self.ship(force=True)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_statuses_do_not_ask_for_a_number(self):
        order = ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            status='confirmed', total_price=1,
        )
        self.client.force_authenticate(user=self.importer)
        response = self.client.patch(
            f'/api/orders/{order.pk}/update-status/', {'status': 'sourcing'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_too_long_a_number_is_refused(self):
        response = self.ship(shipment_number='X' * 61)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ShipmentVisibilityTests(ShipmentTestBase):
    def test_the_timeline_entry_names_the_carrier_and_number(self):
        self.ship(shipment_number='MSK123456789', carrier='Maersk')
        entry = self.order.timeline_events.filter(event_type='shipped').first()
        self.assertEqual(entry.title, 'Shipped · Maersk MSK123456789')
        self.assertIn('MSK123456789', entry.description)

    def test_the_timeline_entry_works_without_a_carrier(self):
        self.ship(shipment_number='ABC123')
        entry = self.order.timeline_events.filter(event_type='shipped').first()
        self.assertEqual(entry.title, 'Shipped · ABC123')

    def test_both_fields_are_on_the_order_payload(self):
        self.ship(shipment_number='MSK123456789', carrier='Maersk')
        self.client.force_authenticate(user=self.buyer)
        response = self.client.get(f'/api/orders/{self.order.pk}/')
        self.assertEqual(response.data['shipment_number'], 'MSK123456789')
        self.assertEqual(response.data['carrier'], 'Maersk')

    def test_both_fields_are_on_the_order_list_row(self):
        self.ship(shipment_number='MSK123456789', carrier='Maersk')
        self.client.force_authenticate(user=self.buyer)
        response = self.client.get('/api/orders/')
        rows = response.data['results'] if 'results' in response.data else response.data
        row = next(r for r in rows if r['id'] == self.order.pk)
        self.assertEqual(row['shipment_number'], 'MSK123456789')

    def test_an_unshipped_order_reports_empty_strings_not_null(self):
        self.client.force_authenticate(user=self.buyer)
        response = self.client.get(f'/api/orders/{self.order.pk}/')
        self.assertEqual(response.data['shipment_number'], '')
        self.assertEqual(response.data['carrier'], '')

    def test_the_buyers_notification_carries_the_number(self):
        self.ship(shipment_number='MSK123456789', carrier='Maersk')
        note = (
            Notification.objects
            .filter(recipient=self.buyer, title__contains=self.order.order_number)
            .order_by('-id').first()
        )
        self.assertIsNotNone(note)
        self.assertIn('MSK123456789', note.message)

    def test_the_notification_metadata_carries_it_too(self):
        """So a client can deep-link or offer a copy button without parsing."""
        self.ship(shipment_number='MSK123456789', carrier='Maersk')
        note = (
            Notification.objects
            .filter(recipient=self.buyer, title__contains=self.order.order_number)
            .order_by('-id').first()
        )
        self.assertEqual(note.metadata.get('shipment_number'), 'MSK123456789')
        self.assertEqual(note.metadata.get('carrier'), 'Maersk')

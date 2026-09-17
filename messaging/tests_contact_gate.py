"""
The contact gate on conversations (fix/messaging-mobile).

Rule: contact details unlock ONLY when a PaymentTransaction with
payment_type='balance' and status='succeeded' exists on the ImportOrder
between the conversation's buyer and importer for the conversation's listing.
A paid reservation fee, an accepted reservation, or a balance under review
must all stay masked — and another buyer's payment on the same car must
never leak into this conversation.
"""
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from orders.models import ImportOrder, Reservation
from orders.tests import make_buyer, make_importer, make_listing
from payments.models import PaymentTransaction

from .models import Conversation, Message

User = get_user_model()

PHONE = '0551234567'


def balance(order, status_):
    return PaymentTransaction.objects.create(
        order=order, user=order.buyer, amount='100000.00',
        payment_type='balance', method='bank_transfer', status=status_,
    )


class ConversationContactGateTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.conv = Conversation.objects.create(
            listing=self.car, buyer=self.buyer, seller=self.importer,
        )
        self.client.force_authenticate(user=self.buyer)

    def detail(self, user=None):
        if user is not None:
            self.client.force_authenticate(user=user)
        resp = self.client.get(f'/api/conversations/{self.conv.pk}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        return resp.data

    def accepted_order(self):
        Reservation.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            status='converted_to_order', platform_fee_sar='99.00', payment_status='succeeded',
        )
        return ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            total_price='100000.00', remaining_balance='100000.00', status='confirmed',
        )

    # (a) no order ----------------------------------------------------------
    def test_no_order_is_locked(self):
        data = self.detail()
        self.assertIs(data['can_exchange_contacts'], False)
        self.assertEqual(data['deal']['stage'], 'none')
        self.assertIsNone(data['deal']['order'])
        self.assertEqual(data['my_role'], 'buyer')

    def test_paid_reservation_awaiting_importer_is_locked(self):
        Reservation.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            status='pending_review', platform_fee_sar='99.00', payment_status='succeeded',
        )
        data = self.detail()
        self.assertIs(data['can_exchange_contacts'], False)
        self.assertEqual(data['deal']['stage'], 'reserved')
        self.assertEqual(data['deal']['reservation']['status'], 'pending_review')
        self.assertAlmostEqual(data['deal']['reservation']['hours_remaining'], 168.0, delta=1.0)

    # (b) accepted order, no transaction ------------------------------------
    def test_accepted_order_without_balance_is_locked(self):
        self.accepted_order()
        data = self.detail()
        self.assertIs(data['can_exchange_contacts'], False)
        self.assertEqual(data['deal']['stage'], 'awaiting_payment')
        self.assertEqual(data['deal']['order']['balance_status'], 'none')

    # (c) balance pending ---------------------------------------------------
    def test_balance_under_review_is_locked(self):
        balance(self.accepted_order(), 'pending')
        data = self.detail()
        self.assertIs(data['can_exchange_contacts'], False)
        self.assertEqual(data['deal']['stage'], 'payment_under_review')

    def test_failed_balance_is_locked(self):
        balance(self.accepted_order(), 'failed')
        data = self.detail()
        self.assertIs(data['can_exchange_contacts'], False)
        self.assertEqual(data['deal']['stage'], 'awaiting_payment')

    # (d) balance succeeded -------------------------------------------------
    def test_only_a_succeeded_balance_unlocks(self):
        balance(self.accepted_order(), 'succeeded')
        data = self.detail()
        self.assertIs(data['can_exchange_contacts'], True)
        self.assertEqual(data['deal']['stage'], 'paid')

    def test_list_endpoint_carries_the_same_gate(self):
        order = self.accepted_order()
        resp = self.client.get('/api/conversations/')
        row = resp.data['results'][0]
        self.assertIs(row['can_exchange_contacts'], False)
        self.assertEqual(row['deal']['stage'], 'awaiting_payment')

        balance(order, 'succeeded')
        row = self.client.get('/api/conversations/').data['results'][0]
        self.assertIs(row['can_exchange_contacts'], True)
        self.assertEqual(row['deal']['stage'], 'paid')

    def test_confirm_payment_endpoint_is_what_flips_it(self):
        order = self.accepted_order()
        balance(order, 'pending')
        self.assertIs(self.detail()['can_exchange_contacts'], False)

        admin = User.objects.create_user(
            email='admin@test.com', name='Admin', role='admin', password='x', is_staff=True,
        )
        self.client.force_authenticate(user=admin)
        resp = self.client.post(f'/api/orders/{order.pk}/confirm-payment/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        self.assertIs(self.detail(user=self.buyer)['can_exchange_contacts'], True)

    # the regression --------------------------------------------------------
    def test_another_buyers_paid_order_on_the_same_car_does_not_leak(self):
        """The mobile bug: an admin or importer order list contains other
        buyers' orders; the conversation must answer for its own parties."""
        other_buyer = make_buyer(email='other@test.com', name='Other Buyer')
        other_order = ImportOrder.objects.create(
            car=self.car, buyer=other_buyer, importer=self.importer,
            total_price='100000.00', remaining_balance='0', status='shipped',
        )
        balance(other_order, 'succeeded')

        for viewer in (self.buyer, self.importer):
            data = self.detail(user=viewer)
            self.assertIs(data['can_exchange_contacts'], False, viewer.email)
            self.assertEqual(data['deal']['stage'], 'none', viewer.email)
        self.assertEqual(self.detail(user=self.importer)['my_role'], 'seller')

    def test_gate_is_scoped_to_the_listing(self):
        other_car = make_listing(self.importer, title='Other Car')
        order = ImportOrder.objects.create(
            car=other_car, buyer=self.buyer, importer=self.importer,
            total_price='100000.00', remaining_balance='0', status='shipped',
        )
        balance(order, 'succeeded')
        self.assertIs(self.detail()['can_exchange_contacts'], False)


class ContactMaskingFollowsTheGateTests(APITestCase):
    """Storage, serializer and list endpoint all agree with the gate."""

    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.conv = Conversation.objects.create(
            listing=self.car, buyer=self.buyer, seller=self.importer,
        )
        self.order = ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            total_price='100000.00', remaining_balance='100000.00', status='confirmed',
        )
        self.client.force_authenticate(user=self.importer)

    def send(self, text):
        resp = self.client.post(
            f'/api/conversations/{self.conv.pk}/messages/',
            {'content': text, 'message_type': 'text'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        return resp

    def test_accepted_order_stores_and_returns_masked(self):
        self.send(f'Call me on {PHONE}')
        stored = Message.objects.get(conversation=self.conv).content
        self.assertNotIn(PHONE, stored)

        self.client.force_authenticate(user=self.buyer)
        listed = self.client.get(f'/api/conversations/{self.conv.pk}/messages/').data
        self.assertNotIn(PHONE, listed[0]['content'])

    def test_balance_under_review_still_masks(self):
        balance(self.order, 'pending')
        self.send(f'Call me on {PHONE}')
        self.assertNotIn(PHONE, Message.objects.get(conversation=self.conv).content)

    def test_succeeded_balance_stores_and_returns_raw(self):
        balance(self.order, 'succeeded')
        self.send(f'Call me on {PHONE}')
        self.assertIn(PHONE, Message.objects.get(conversation=self.conv).content)

        self.client.force_authenticate(user=self.buyer)
        detail = self.client.get(f'/api/conversations/{self.conv.pk}/').data
        self.assertIn(PHONE, detail['recent_messages'][-1]['content'])

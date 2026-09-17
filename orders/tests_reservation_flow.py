"""
Reservation & order flow (fix/reservation-flow).

Business rules under test:
  * The SAR 99 reservation fee is non-refundable, is NOT credited toward the
    car price, and is WARED's revenue.
  * A reserved car is hidden from every other user.
  * Buyer↔importer messaging is always allowed; contact details are not
    exchanged until the balance is confirmed paid.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.test import APITestCase

from cars.models import Listing
from payments.models import PaymentTransaction

from .models import ImportOrder, Reservation
from .tasks import expire_due_reservations
from .tests import make_buyer, make_importer, make_listing

User = get_user_model()


def make_reservation(car, buyer, importer, status_='pending_payment', **kwargs):
    defaults = {
        'car': car,
        'buyer': buyer,
        'importer': importer,
        'status': status_,
        'platform_fee_sar': '99.00',
    }
    defaults.update(kwargs)
    return Reservation.objects.create(**defaults)


def confirm_balance(order, amount='100000.00'):
    """Mark the order's full balance as received — the gate for everything."""
    return PaymentTransaction.objects.create(
        order=order,
        user=order.buyer,
        amount=amount,
        payment_type='balance',
        method='mada',
        status='succeeded',
    )


# ---------------------------------------------------------------------------
# 1. Expiry releases the car
# ---------------------------------------------------------------------------

class ReservationExpiryTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        # Reserved out of 'ready_for_delivery', not 'available', to prove the
        # car goes back to what it actually was.
        self.car = make_listing(self.importer, import_status='ready_for_delivery')
        self.res = make_reservation(self.car, self.buyer, self.importer)
        self.res.activate()
        self.car.refresh_from_db()

    def test_activate_locks_the_car(self):
        self.assertTrue(self.car.is_reserved)
        self.assertEqual(self.car.current_reservation_id, self.res.pk)
        self.assertEqual(self.car.import_status, 'reserved')

    def test_expiry_releases_the_car_on_a_frozen_clock(self):
        created = self.res.created_at
        # One second past the 7-day deadline.
        frozen = created + timedelta(days=Reservation.RESERVATION_EXPIRY_DAYS, seconds=1)

        with mock.patch('django.utils.timezone.now', return_value=frozen):
            expired = expire_due_reservations()

        self.assertEqual(expired, [self.res.reservation_number])

        self.res.refresh_from_db()
        self.car.refresh_from_db()
        self.assertEqual(self.res.status, 'expired')
        self.assertFalse(self.car.is_reserved)
        self.assertIsNone(self.car.current_reservation_id)
        # Restored to its pre-reservation value, not blindly 'available'.
        self.assertEqual(self.car.import_status, 'ready_for_delivery')

    def test_not_expired_one_second_before_the_deadline(self):
        frozen = self.res.created_at + timedelta(
            days=Reservation.RESERVATION_EXPIRY_DAYS, seconds=-1
        )
        with mock.patch('django.utils.timezone.now', return_value=frozen):
            expired = expire_due_reservations()

        self.assertEqual(expired, [])
        self.res.refresh_from_db()
        self.car.refresh_from_db()
        self.assertEqual(self.res.status, 'pending_review')
        self.assertTrue(self.car.is_reserved)

    def test_only_pending_review_expires(self):
        self.res.status = 'converted_to_order'
        self.res.save(update_fields=['status'])
        frozen = self.res.created_at + timedelta(days=30)
        with mock.patch('django.utils.timezone.now', return_value=frozen):
            self.assertEqual(expire_due_reservations(), [])

    def test_management_command_and_task_share_one_implementation(self):
        # Patch where the command *looked it up*, not where it is defined —
        # the command binds the name at import time.
        target = 'orders.management.commands.expire_reservations.expire_due_reservations'
        with mock.patch(target, return_value=[]) as shared:
            from django.core.management import call_command
            call_command('expire_reservations')
        self.assertTrue(shared.called)


# ---------------------------------------------------------------------------
# 2. pay/ goes through activate()
# ---------------------------------------------------------------------------

class ReservationPayTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.res = make_reservation(self.car, self.buyer, self.importer)

    def _stub_provider(self, success=True):
        provider = mock.Mock()
        provider.charge.return_value = mock.Mock(
            success=success,
            provider='stub',
            transaction_id='STUB-123',
            error_message='' if success else 'Card declined',
        )
        return mock.patch('payments.views.get_payment_provider', return_value=provider)

    def test_successful_payment_populates_current_reservation(self):
        self.client.force_authenticate(user=self.buyer)
        with self._stub_provider():
            resp = self.client.post(
                f'/api/reservations/{self.res.pk}/pay/', {'method': 'mada'}, format='json'
            )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Response shape preserved.
        self.assertTrue(resp.data['success'])
        self.assertEqual(resp.data['transaction_id'], 'STUB-123')
        self.assertEqual(resp.data['reservation_status'], 'pending_review')

        self.res.refresh_from_db()
        self.car.refresh_from_db()
        self.assertEqual(self.res.status, 'pending_review')
        self.assertEqual(self.res.payment_status, 'succeeded')
        # The bug this fixes: current_reservation used to stay NULL.
        self.assertEqual(self.car.current_reservation_id, self.res.pk)
        self.assertTrue(self.car.is_reserved)
        self.assertEqual(self.car.import_status, 'reserved')
        self.assertEqual(self.res.car_import_status_before, 'available')

    def test_failed_payment_leaves_the_car_unlocked(self):
        self.client.force_authenticate(user=self.buyer)
        with self._stub_provider(success=False):
            resp = self.client.post(
                f'/api/reservations/{self.res.pk}/pay/', {'method': 'mada'}, format='json'
            )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.car.refresh_from_db()
        self.assertFalse(self.car.is_reserved)
        self.assertIsNone(self.car.current_reservation_id)


# ---------------------------------------------------------------------------
# 3. Duplicate guard
# ---------------------------------------------------------------------------

class ReservationDuplicateGuardTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.other_buyer = make_buyer(email='other@test.com', name='Other Buyer')
        self.car = make_listing(self.importer)
        self.url = '/api/reservations/'

    def _create(self, user):
        self.client.force_authenticate(user=user)
        return self.client.post(self.url, {'car_id': self.car.id}, format='json')

    def test_blocks_second_reservation_while_one_is_pending_payment(self):
        make_reservation(self.car, self.buyer, self.importer, 'pending_payment')
        resp = self._create(self.other_buyer)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data['detail'], 'This car is currently reserved.')

    def test_blocks_second_reservation_while_one_is_pending_review(self):
        make_reservation(self.car, self.buyer, self.importer, 'pending_review')
        resp = self._create(self.other_buyer)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data['detail'], 'This car is currently reserved.')

    def test_blocks_the_same_buyer_reserving_twice(self):
        make_reservation(self.car, self.buyer, self.importer, 'pending_review')
        resp = self._create(self.buyer)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_allows_a_new_reservation_after_the_previous_one_ended(self):
        make_reservation(self.car, self.buyer, self.importer, 'cancelled_by_buyer')
        resp = self._create(self.other_buyer)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_cannot_reserve_your_own_listing(self):
        resp = self._create(self.importer)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 4. A reserved car is hidden from everyone else
# ---------------------------------------------------------------------------

class ReservedCarVisibilityTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.stranger = make_buyer(email='stranger@test.com', name='Stranger')
        self.staff = make_buyer(email='staff@test.com', name='Staff', is_staff=True)
        self.car = make_listing(self.importer)
        self.res = make_reservation(self.car, self.buyer, self.importer)
        self.res.activate()
        self.detail_url = f'/api/imported-cars/{self.car.pk}/'

    def _get(self, user=None):
        if user:
            self.client.force_authenticate(user=user)
        else:
            self.client.force_authenticate(user=None)
        return self.client.get(self.detail_url)

    def test_anonymous_gets_404(self):
        self.assertEqual(self._get().status_code, status.HTTP_404_NOT_FOUND)

    def test_unrelated_user_gets_404(self):
        self.assertEqual(self._get(self.stranger).status_code, status.HTTP_404_NOT_FOUND)

    def test_reserving_buyer_can_still_open_it(self):
        self.assertEqual(self._get(self.buyer).status_code, status.HTTP_200_OK)

    def test_importer_can_still_open_it(self):
        self.assertEqual(self._get(self.importer).status_code, status.HTTP_200_OK)

    def test_staff_can_still_open_it(self):
        self.assertEqual(self._get(self.staff).status_code, status.HTTP_200_OK)

    def test_excluded_from_the_public_browse_list(self):
        self.client.force_authenticate(user=self.stranger)
        resp = self.client.get('/api/imported-cars/')
        ids = [row['id'] for row in resp.data.get('results', resp.data)]
        self.assertNotIn(self.car.pk, ids)

    def test_excluded_from_compare_for_a_stranger(self):
        other = make_listing(self.importer, title='Other Car')
        self.client.force_authenticate(user=self.stranger)
        resp = self.client.get(f'/api/listings/compare/?ids={self.car.pk},{other.pk}')
        returned = [row['id'] for row in resp.data]
        self.assertNotIn(self.car.pk, returned)
        self.assertIn(other.pk, returned)


# ---------------------------------------------------------------------------
# 5. Conversation on reserve + messaging is never gated
# ---------------------------------------------------------------------------

class ReservationConversationTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)

    def test_activate_creates_one_conversation(self):
        from messaging.models import Conversation
        res = make_reservation(self.car, self.buyer, self.importer)
        res.activate()
        self.assertEqual(
            Conversation.objects.filter(listing=self.car, buyer=self.buyer).count(), 1
        )

    def test_activate_reuses_a_conversation_started_before_reserving(self):
        from messaging.models import Conversation
        existing = Conversation.objects.create(
            listing=self.car, buyer=self.buyer, seller=self.importer
        )
        res = make_reservation(self.car, self.buyer, self.importer)
        res.activate()

        convs = Conversation.objects.filter(listing=self.car, buyer=self.buyer)
        self.assertEqual(convs.count(), 1)
        self.assertEqual(convs.first().pk, existing.pk)
        existing.refresh_from_db()
        self.assertEqual(existing.reservation_id, res.pk)

    def test_messaging_does_not_require_a_reservation(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            '/api/conversations/', {'car_id': self.car.pk}, format='json'
        )
        self.assertIn(resp.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED))

    def test_contacts_stay_masked_until_the_balance_is_paid(self):
        from messaging.models import Conversation
        from messaging.utils import contact_exchange_allowed

        conv = Conversation.objects.create(
            listing=self.car, buyer=self.buyer, seller=self.importer
        )
        order = ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            total_price='100000.00', remaining_balance='100000.00',
            status='confirmed',
        )
        # 'confirmed' alone must NOT unmask — that is what accept/ produces.
        self.assertFalse(contact_exchange_allowed(conv))

        confirm_balance(order)
        self.assertTrue(contact_exchange_allowed(conv))


# ---------------------------------------------------------------------------
# 6. Order cancellation reason is optional
# ---------------------------------------------------------------------------

class OrderCancelReasonTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.order = ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            total_price='100000.00', remaining_balance='100000.00',
            status='confirmed',
        )
        self.url = f'/api/orders/{self.order.pk}/cancel/'

    def test_cancel_without_a_body_uses_a_default_reason(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'cancelled')
        self.assertEqual(self.order.cancellation_reason, 'Cancelled by buyer')

    def test_supplied_reason_is_stored_verbatim(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            self.url, {'cancellation_reason': 'Found a better car'}, format='json'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_reason, 'Found a better car')

    def test_importer_cancelling_gets_the_importer_default(self):
        self.client.force_authenticate(user=self.importer)
        self.client.post(self.url, {}, format='json')
        self.order.refresh_from_db()
        self.assertEqual(self.order.cancellation_reason, 'Cancelled by importer')


# ---------------------------------------------------------------------------
# 7. Status transitions + the money gate
# ---------------------------------------------------------------------------

class OrderStatusTransitionTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.staff = make_buyer(email='staff2@test.com', name='Staff', is_staff=True)
        self.car = make_listing(self.importer)
        self.order = ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            total_price='100000.00', remaining_balance='100000.00',
            status='confirmed',
        )
        self.url = f'/api/orders/{self.order.pk}/update-status/'

    def _patch(self, user, **body):
        self.client.force_authenticate(user=user)
        return self.client.patch(self.url, body, format='json')

    def test_confirmed_to_delivered_is_blocked(self):
        resp = self._patch(self.importer, status='delivered')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        # The error names the legal next steps.
        self.assertIn('sourcing', str(resp.data))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'confirmed')

    def test_confirmed_to_sourcing_is_allowed(self):
        resp = self._patch(self.importer, status='sourcing')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'sourcing')

    def test_purchased_is_blocked_until_the_balance_is_confirmed(self):
        self.order.status = 'sourcing'
        self.order.save(update_fields=['status'])

        resp = self._patch(self.importer, status='purchased')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data['detail'], 'Balance payment not confirmed.')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'sourcing')

    def test_purchased_is_allowed_once_the_balance_is_confirmed(self):
        self.order.status = 'sourcing'
        self.order.save(update_fields=['status'])
        confirm_balance(self.order)

        resp = self._patch(self.importer, status='purchased')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'purchased')

    def test_staff_can_force_past_both_gates(self):
        resp = self._patch(self.staff, status='delivered', force=True)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'delivered')

    def test_importer_cannot_force(self):
        resp = self._patch(self.importer, status='delivered', force=True)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 8. Removed endpoints
# ---------------------------------------------------------------------------

class RemovedEndpointTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.res = make_reservation(self.car, self.buyer, self.importer, 'pending_review')

    def test_convert_to_order_is_gone(self):
        self.client.force_authenticate(user=self.importer)
        resp = self.client.post(f'/api/reservations/{self.res.pk}/convert-to-order/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_accept_still_works(self):
        self.client.force_authenticate(user=self.importer)
        resp = self.client.post(f'/api/reservations/{self.res.pk}/accept/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.res.refresh_from_db()
        self.assertEqual(self.res.status, 'converted_to_order')

    def test_cancel_rejects_the_legacy_active_status(self):
        self.res.status = 'active'
        self.res.save(update_fields=['status'])
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(f'/api/reservations/{self.res.pk}/cancel/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 9 + 10. Serializer payloads
# ---------------------------------------------------------------------------

class SerializerFieldTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.res = make_reservation(self.car, self.buyer, self.importer, 'pending_review')
        self.order = ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=self.importer,
            total_price='100000.00', remaining_balance='100000.00',
            status='confirmed',
        )

    def test_reservation_exposes_expiry_and_permissions(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.get(f'/api/reservations/{self.res.pk}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # DRF renders DateTimeField as an ISO string, so compare like for like.
        expected = self.res.created_at + timedelta(days=Reservation.RESERVATION_EXPIRY_DAYS)
        self.assertEqual(
            serializers.DateTimeField().to_representation(resp.data['expires_at']),
            serializers.DateTimeField().to_representation(expected),
        )
        self.assertAlmostEqual(resp.data['hours_remaining'], 168.0, delta=1.0)
        self.assertTrue(resp.data['can_cancel'])
        # pending_review means payment already happened.
        self.assertFalse(resp.data['can_pay'])
        self.assertFalse(resp.data['fee_refundable'])

    def test_can_pay_is_true_only_for_the_buyer_awaiting_payment(self):
        self.res.status = 'pending_payment'
        self.res.save(update_fields=['status'])

        self.client.force_authenticate(user=self.buyer)
        resp = self.client.get(f'/api/reservations/{self.res.pk}/')
        self.assertTrue(resp.data['can_pay'])

        self.client.force_authenticate(user=self.importer)
        resp = self.client.get(f'/api/reservations/{self.res.pk}/')
        self.assertFalse(resp.data['can_pay'])

    def test_order_balance_is_the_full_price_with_no_fee_deduction(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.get(f'/api/orders/{self.order.pk}/')

        # SAR 99 is WARED's revenue, never credited against the car.
        self.assertEqual(resp.data['balance_due_sar'], 100000.0)
        self.assertEqual(resp.data['payment_status'], 'none')
        self.assertFalse(resp.data['can_exchange_contacts'])

    def test_order_payment_status_lifecycle(self):
        self.client.force_authenticate(user=self.buyer)

        txn = PaymentTransaction.objects.create(
            order=self.order, user=self.buyer, amount='100000.00',
            payment_type='balance', method='mada', status='pending',
        )
        resp = self.client.get(f'/api/orders/{self.order.pk}/')
        self.assertEqual(resp.data['payment_status'], 'under_review')
        self.assertFalse(resp.data['can_exchange_contacts'])

        txn.status = 'failed'
        txn.save(update_fields=['status'])
        resp = self.client.get(f'/api/orders/{self.order.pk}/')
        self.assertEqual(resp.data['payment_status'], 'rejected')

        txn.status = 'succeeded'
        txn.save(update_fields=['status'])
        resp = self.client.get(f'/api/orders/{self.order.pk}/')
        self.assertEqual(resp.data['payment_status'], 'paid')
        self.assertTrue(resp.data['can_exchange_contacts'])

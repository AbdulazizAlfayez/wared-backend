"""
Tests for the Import Order & Tracking system (Phase C).
"""
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from cars.models import Listing
from .models import ImportOrder, ImportTimeline

User = get_user_model()


def make_importer(**kwargs):
    defaults = {
        'email': 'importer@test.com',
        'name': 'Test Importer',
        'role': 'importer',
        'password': 'testpass123',
    }
    defaults.update(kwargs)
    return User.objects.create_user(**defaults)


def make_buyer(**kwargs):
    defaults = {
        'email': 'buyer@test.com',
        'name': 'Test Buyer',
        'role': 'user',
        'password': 'testpass123',
    }
    defaults.update(kwargs)
    return User.objects.create_user(**defaults)


def make_listing(owner, import_status='available', **kwargs):
    defaults = {
        'title': 'Test Car',
        'make': 'Toyota',
        'model': 'Camry',
        'year': 2022,
        'price': '100000.00',
        'mileage': 5000,
        'city': 'Riyadh',
        'status': 'approved',
        'import_status': import_status,
        'is_active': True,
        'owner': owner,
    }
    defaults.update(kwargs)
    return Listing.objects.create(**defaults)


class OrderCreateTests(APITestCase):
    """Tests for POST /api/orders/"""

    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.url = '/api/orders/'

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    # Test 1
    def test_buyer_can_create_order(self):
        """Buyer can create an order; car.import_status becomes 'reserved'."""
        self._auth(self.buyer)
        data = {'car_id': self.car.id, 'buyer_notes': 'Please hurry!'}
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'pending')
        self.car.refresh_from_db()
        self.assertEqual(self.car.import_status, 'reserved')
        self.assertTrue(
            ImportOrder.objects.filter(buyer=self.buyer, car=self.car).exists()
        )

    # Test 2
    def test_order_unavailable_car_rejected(self):
        """Creating order on unavailable car (import_status != 'available') → 400."""
        self._auth(self.buyer)
        reserved_car = make_listing(
            self.importer,
            import_status='reserved',
            title='Reserved Car',
        )
        data = {'car_id': reserved_car.id}
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # Test 3
    def test_buyer_cannot_order_own_listing(self):
        """Buyer cannot order their own listing → 400."""
        own_car = make_listing(self.buyer, title='My own car')
        self._auth(self.buyer)
        data = {'car_id': own_car.id}
        response = self.client.post(self.url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # Test 4
    def test_duplicate_order_blocked(self):
        """Duplicate active order blocked → 400."""
        self._auth(self.buyer)
        data = {'car_id': self.car.id}
        r1 = self.client.post(self.url, data, format='json')
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        # Car is now reserved; create another available car for the same test purpose
        # But actually duplicate check is on same buyer/car combo regardless of import_status
        r2 = self.client.post(self.url, data, format='json')
        self.assertEqual(r2.status_code, status.HTTP_400_BAD_REQUEST)


class OrderStatusTransitionTests(APITestCase):
    """Tests for PATCH /api/orders/{id}/update-status/"""

    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        # Create an order as buyer first
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/orders/', {'car_id': self.car.id}, format='json')
        self.order_id = resp.data['id']
        self.order_number = resp.data['order_number']
        self.update_url = f'/api/orders/{self.order_id}/update-status/'

    # Test 5
    def test_valid_status_transition_by_importer(self):
        """Valid transition: pending → confirmed by importer → 200."""
        self.client.force_authenticate(user=self.importer)
        response = self.client.patch(
            self.update_url,
            {'status': 'confirmed', 'notes': 'All good'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'confirmed')

    # Test 6a — flexible transitions: skipping ahead is now allowed
    def test_skip_ahead_transition_allowed(self):
        """Flexible mode: pending → shipped is allowed (importer can jump stages)."""
        self.client.force_authenticate(user=self.importer)
        response = self.client.patch(
            self.update_url,
            {'status': 'shipped'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'shipped')

    # Test 6b — unknown status is still rejected
    def test_unknown_status_rejected(self):
        self.client.force_authenticate(user=self.importer)
        response = self.client.patch(
            self.update_url,
            {'status': 'teleported'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # Test 6c — terminal states stay locked
    def test_terminal_status_locked(self):
        order = ImportOrder.objects.get(pk=self.order_id)
        order.status = 'cancelled'
        order.save(update_fields=['status'])
        self.client.force_authenticate(user=self.importer)
        response = self.client.patch(
            self.update_url,
            {'status': 'confirmed'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # Test 7
    def test_status_change_creates_timeline_event(self):
        """Status change auto-creates a timeline event."""
        self.client.force_authenticate(user=self.importer)
        self.client.patch(
            self.update_url,
            {'status': 'confirmed'},
            format='json',
        )
        order = ImportOrder.objects.get(pk=self.order_id)
        # Should have at least 2 events: order_placed + order_confirmed
        self.assertGreaterEqual(order.timeline_events.count(), 2)
        event_types = list(order.timeline_events.values_list('event_type', flat=True))
        self.assertIn('order_confirmed', event_types)

    # Test 8
    def test_buyer_cannot_call_update_status(self):
        """Buyer cannot call update-status → 403."""
        self.client.force_authenticate(user=self.buyer)
        response = self.client.patch(
            self.update_url,
            {'status': 'confirmed'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class OrderCancelTests(APITestCase):
    """Tests for POST /api/orders/{id}/cancel/"""

    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/orders/', {'car_id': self.car.id}, format='json')
        self.order_id = resp.data['id']
        self.cancel_url = f'/api/orders/{self.order_id}/cancel/'

    # Test 9
    def test_cancel_order_sets_status_and_cancelled_by(self):
        """Cancel order sets status to 'cancelled' and sets cancelled_by."""
        self.client.force_authenticate(user=self.buyer)
        response = self.client.post(
            self.cancel_url,
            {'cancellation_reason': 'Changed my mind'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'cancelled')
        order = ImportOrder.objects.get(pk=self.order_id)
        self.assertEqual(order.status, 'cancelled')
        self.assertEqual(order.cancelled_by_id, self.buyer.id)
        self.assertEqual(order.cancellation_reason, 'Changed my mind')


class OrderTimelineVisibilityTests(APITestCase):
    """Tests for GET /api/orders/{id}/timeline/"""

    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.car = make_listing(self.importer)
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/orders/', {'car_id': self.car.id}, format='json')
        self.order_id = resp.data['id']
        # Add a private (importer-only) timeline event
        order = ImportOrder.objects.get(pk=self.order_id)
        ImportTimeline.objects.create(
            order=order,
            event_type='note',
            title='Internal note — do not show buyer',
            date=timezone.now(),
            created_by=self.importer,
            is_public=False,
        )
        self.timeline_url = f'/api/orders/{self.order_id}/timeline/'

    # Test 10
    def test_buyer_only_sees_public_timeline_events(self):
        """Timeline GET: buyer only sees is_public=True events."""
        self.client.force_authenticate(user=self.buyer)
        response = self.client.get(self.timeline_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for event in response.data:
            self.assertTrue(event['is_public'],
                            f"Buyer should not see private event: {event['title']}")

    def test_importer_sees_all_timeline_events(self):
        """Importer can see all timeline events including private ones."""
        self.client.force_authenticate(user=self.importer)
        response = self.client.get(self.timeline_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        public_flags = [e['is_public'] for e in response.data]
        self.assertIn(False, public_flags,
                      "Importer should see private events too")


# ---------------------------------------------------------------------------
# Reservation email tests
# ---------------------------------------------------------------------------

from unittest.mock import patch, MagicMock
from .models import Reservation


class ReservationEmailTests(APITestCase):
    """Test that reservation lifecycle emails fire correctly."""

    @classmethod
    def setUpTestData(cls):
        cls.importer = make_importer(email='imp_email@test.com', is_email_verified=True)
        cls.buyer = make_buyer(email='buyer_email@test.com', is_email_verified=True)
        cls.listing = make_listing(owner=cls.importer, status='approved')

    @patch('orders.reservation_views._safe_send')
    def test_reservation_created_sends_buyer_email(self, mock_send):
        """Creating a reservation sends a confirmation email to the buyer."""
        self.client.force_authenticate(user=self.buyer)
        response = self.client.post('/api/reservations/', {'car_id': self.listing.pk})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_send.assert_called()
        call_args = mock_send.call_args
        self.assertEqual(call_args[0][0], 'reservation_buyer_confirmation')
        self.assertEqual(call_args[0][1], 'buyer_email@test.com')

    @patch('orders.reservation_views._safe_send')
    def test_reservation_accepted_sends_buyer_email(self, mock_send):
        """Accepting a reservation sends an order-created email to the buyer."""
        res = Reservation.objects.create(
            car=self.listing, buyer=self.buyer, importer=self.importer,
            status='pending_review', platform_fee_sar=99,
        )
        self.client.force_authenticate(user=self.importer)
        response = self.client.post(f'/api/reservations/{res.pk}/accept/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check that _safe_send was called with 'reservation_accepted'
        template_names = [c[0][0] for c in mock_send.call_args_list]
        self.assertIn('reservation_accepted', template_names)

    @patch('orders.reservation_views._safe_send')
    def test_reservation_rejected_sends_buyer_email(self, mock_send):
        """Rejecting a reservation sends a rejection email to the buyer."""
        res = Reservation.objects.create(
            car=self.listing, buyer=self.buyer, importer=self.importer,
            status='pending_review', platform_fee_sar=99,
        )
        self.client.force_authenticate(user=self.importer)
        response = self.client.post(f'/api/reservations/{res.pk}/reject/',
                                    {'reason': 'Car sold'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        template_names = [c[0][0] for c in mock_send.call_args_list]
        self.assertIn('reservation_rejected', template_names)

    @patch('orders.reservation_views._safe_send', side_effect=Exception("SMTP down"))
    def test_email_failure_does_not_break_reservation(self, mock_send):
        """Email send failure must not prevent the reservation from being created."""
        self.client.force_authenticate(user=self.buyer)
        response = self.client.post('/api/reservations/', {'car_id': self.listing.pk})
        # Should still succeed even though email "failed"
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class OrderCarSyncTests(APITestCase):
    """Test that order status updates sync to the car's import_status."""

    @classmethod
    def setUpTestData(cls):
        cls.importer = make_importer(email='sync_imp@test.com', is_email_verified=True)
        cls.buyer = make_buyer(email='sync_buyer@test.com', is_email_verified=True)
        cls.listing = make_listing(cls.importer, import_status='available')

    def _make_order(self, order_status='confirmed'):
        return ImportOrder.objects.create(
            car=self.listing, buyer=self.buyer, importer=self.importer,
            total_price=100000, remaining_balance=100000, status=order_status,
        )

    def test_order_shipped_syncs_car_to_shipping(self):
        # 'preparing_shipment' → 'shipped' is the valid transition path
        order = self._make_order('preparing_shipment')
        self.client.force_authenticate(user=self.importer)
        resp = self.client.patch(
            f'/api/orders/{order.pk}/update-status/',
            {'status': 'shipped'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.import_status, 'shipping')

    def test_order_delivered_syncs_car(self):
        order = self._make_order('ready')
        self.client.force_authenticate(user=self.importer)
        resp = self.client.patch(
            f'/api/orders/{order.pk}/update-status/',
            {'status': 'delivered'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.import_status, 'delivered')

    def test_order_at_port_syncs_car(self):
        order = self._make_order('shipped')
        self.client.force_authenticate(user=self.importer)
        resp = self.client.patch(
            f'/api/orders/{order.pk}/update-status/',
            {'status': 'arrived_port'}, format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.import_status, 'at_port')

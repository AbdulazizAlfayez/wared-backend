"""
Reserved-car visibility (fix/reserved-visibility).

Rule under test: from the moment a reservation is PAID (pending_review) until
it is cancelled, rejected or expired — or its order is cancelled/refunded —
the car is gone from every browse surface for everyone but staff (even its
buyer and importer), and opening it directly works only for the buyer, the
importer and staff. Every public listing surface goes through
cars.visibility.public_market_q(user, browse=...); each gets a test here.
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from cars.models import RecentlyViewed, SavedSearch, Showroom
from cars.visibility import public_market_q, reservation_state
from favorites.models import Favorite
from importers.models import ImporterProfile
from source_countries.models import SourceCountry

from .models import CarAlreadyReserved, ImportOrder, Reservation
from .tasks import expire_due_reservations
from .tests import make_buyer, make_importer, make_listing
from .tests_reservation_flow import make_reservation


def _ids(resp):
    data = resp.data
    if isinstance(data, dict):
        data = data.get('results', data)
    return [row['id'] for row in data]


class ReservedFixture:
    """
    Car X: reserved and paid by buyer A. Car Y: an ordinary public car.
    Stranger B, the importer and staff complete the cast.
    """

    def setUp(self):
        cache.clear()
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.stranger = make_buyer(email='stranger@test.com', name='Stranger')
        self.staff = make_buyer(email='staff@test.com', name='Staff', is_staff=True)

        self.showroom = Showroom.objects.create(owner=self.importer, name='Showroom')
        common = dict(
            showroom=self.showroom, latitude=24.71, longitude=46.67,
            is_featured=True, source_country='tstvis',
        )
        self.car = make_listing(self.importer, title='Reserved Car', make='Lamborghini', **common)
        self.other = make_listing(self.importer, title='Public Car', make='Toyota', **common)

        self.res = make_reservation(self.car, self.buyer, self.importer)
        self.res.activate()
        self.car.refresh_from_db()

    def as_(self, user):
        self.client.force_authenticate(user=user)
        return self.client

    def assert_browse_hidden(self, url, *, ids=_ids):
        """Browse: hidden from everyone — buyer and importer included — but staff."""
        for user in (None, self.stranger, self.buyer, self.importer):
            resp = self.as_(user).get(url)
            self.assertEqual(resp.status_code, 200, (url, user, resp.data))
            self.assertNotIn(self.car.pk, ids(resp), (url, user))
            self.assertIn(self.other.pk, ids(resp), (url, user))
        resp = self.as_(self.staff).get(url)
        self.assertIn(self.car.pk, ids(resp), (url, 'staff'))


# ---------------------------------------------------------------------------
# 1. Every public endpoint
# ---------------------------------------------------------------------------

class PublicEndpointTests(ReservedFixture, APITestCase):

    def test_listings_list(self):
        self.assert_browse_hidden('/api/listings/')

    def test_listings_list_cursor_mode(self):
        url = '/api/listings/?pagination=cursor'
        self.assert_browse_hidden(url)

    def test_listings_count(self):
        anon = self.as_(None).get('/api/listings/').data['count']
        self.assertEqual(anon, 1)
        self.assertEqual(self.as_(self.stranger).get('/api/listings/').data['count'], 1)
        self.assertEqual(self.as_(self.buyer).get('/api/listings/').data['count'], 1)
        self.assertEqual(self.as_(self.importer).get('/api/listings/').data['count'], 1)
        self.assertEqual(self.as_(self.staff).get('/api/listings/').data['count'], 2)

    def test_listings_detail_404_for_non_party(self):
        url = f'/api/listings/{self.car.pk}/'
        self.assertEqual(self.as_(None).get(url).status_code, 404)
        self.assertEqual(self.as_(self.stranger).get(url).status_code, 404)
        for user in (self.buyer, self.importer, self.staff):
            self.assertEqual(self.as_(user).get(url).status_code, 200, user)

    def test_imported_cars_list(self):
        self.assert_browse_hidden('/api/imported-cars/')

    def test_imported_cars_detail_404_for_non_party(self):
        url = f'/api/imported-cars/{self.car.pk}/'
        self.assertEqual(self.as_(None).get(url).status_code, 404)
        self.assertEqual(self.as_(self.stranger).get(url).status_code, 404)
        for user in (self.buyer, self.importer, self.staff):
            self.assertEqual(self.as_(user).get(url).status_code, 200, user)

    def test_imported_cars_arriving(self):
        # A car still marked 'shipping' with a paid reservation row but no lock
        # fields set (the pre-fix data shape) must be hidden by the row alone.
        shipping_x = make_listing(self.importer, title='Ship X', import_status='shipping')
        shipping_y = make_listing(self.importer, title='Ship Y', import_status='shipping')
        make_reservation(shipping_x, self.buyer, self.importer, 'pending_review')
        resp = self.as_(self.stranger).get('/api/imported-cars/arriving/')
        self.assertNotIn(shipping_x.pk, _ids(resp))
        self.assertIn(shipping_y.pk, _ids(resp))
        resp = self.as_(self.buyer).get('/api/imported-cars/arriving/')
        self.assertNotIn(shipping_x.pk, _ids(resp))

    def test_compare(self):
        url = f'/api/listings/compare/?ids={self.car.pk},{self.other.pk}'
        self.assert_browse_hidden(url)

    def test_search(self):
        url = '/api/listings/?search=Lamborghini'
        for user in (None, self.stranger, self.buyer, self.importer):
            self.assertEqual(_ids(self.as_(user).get(url)), [], user)
        self.assertEqual(_ids(self.as_(self.staff).get(url)), [self.car.pk])

    def test_autocomplete(self):
        url = '/api/listings/autocomplete/?q=Lambo'
        self.assertEqual(self.as_(self.stranger).get(url).data['suggestions'], [])
        self.assertEqual(self.as_(None).get(url).data['suggestions'], [])
        self.assertEqual(self.as_(self.buyer).get(url).data['suggestions'], [])
        self.assertEqual(
            self.as_(self.staff).get(url).data['suggestions'],
            [{'type': 'make', 'value': 'Lamborghini'}],
        )

    def test_popular(self):
        self.assert_browse_hidden('/api/listings/popular/')

    def test_featured(self):
        self.assert_browse_hidden('/api/listings/featured/')

    def test_nearby(self):
        url = '/api/listings/nearby/?lat=24.71&lng=46.67&radius=10'
        self.assert_browse_hidden(url)

    def test_map_pins(self):
        self.assert_browse_hidden('/api/listings/map-pins/')

    def test_filter_options(self):
        makes = lambda resp: [m['value'] for m in resp.data['makes']]
        for user in (None, self.stranger, self.buyer):
            cache.clear()
            resp = self.as_(user).get('/api/listings/filter-options/')
            # Facets are shared and cached, so they count public cars only —
            # for the parties too.
            self.assertEqual(makes(resp), ['Toyota'], user)

    def test_imported_cars_filter_options(self):
        resp = self.as_(self.stranger).get('/api/imported-cars/filter-options/')
        self.assertEqual(resp.data['makes'], ['Toyota'])

    def test_by_country(self):
        SourceCountry.objects.create(
            code='tstvis', name_en='Vis', name_ar='Vis', iso_code='VZ',
            flag_emoji='x', latitude=0, longitude=0,
            avg_shipping_cost_sar=1, avg_shipping_days=1, is_active=True,
        )
        cache.clear()
        resp = self.as_(self.stranger).get('/api/imported-cars/by-country/')
        row = next(c for c in resp.data['countries'] if c['code'] == 'tstvis')
        self.assertEqual(row['total_cars'], 1)
        self.assertEqual(row['reserved_cars'], 0)

    def test_showroom_listings(self):
        self.assert_browse_hidden(f'/api/showrooms/{self.showroom.pk}/listings/')

    def test_importer_inventory(self):
        profile, _ = ImporterProfile.objects.get_or_create(
            user=self.importer, defaults={'business_name': 'Imp'},
        )
        url = f'/api/importers/{profile.pk}/inventory/'
        self.assert_browse_hidden(url)

    def test_saved_search_results(self):
        search = SavedSearch.objects.create(user=self.stranger, name='all', filters={})
        resp = self.as_(self.stranger).get(f'/api/saved-searches/{search.pk}/results/')
        self.assertNotIn(self.car.pk, _ids(resp))
        self.assertIn(self.other.pk, _ids(resp))
        listed = self.as_(self.stranger).get('/api/saved-searches/')
        self.assertEqual(_ids_count(listed, search.pk), 1)

    def test_favorites(self):
        Favorite.objects.create(user=self.stranger, listing=self.car)
        Favorite.objects.create(user=self.stranger, listing=self.other)
        resp = self.as_(self.stranger).get('/api/favorites/')
        self.assertEqual(_ids(resp), [self.other.pk])
        self.assertEqual(self.as_(self.stranger).get('/api/favorites/count/').data['count'], 1)
        toggle = self.as_(self.stranger).post(f'/api/imported-cars/{self.car.pk}/favorite/')
        self.assertEqual(toggle.status_code, 404)

    def test_recently_viewed(self):
        RecentlyViewed.objects.create(user=self.stranger, listing=self.car)
        RecentlyViewed.objects.create(user=self.stranger, listing=self.other)
        resp = self.as_(self.stranger).get('/api/recently-viewed/')
        ids = [row['listing']['id'] for row in resp.data['results']]
        self.assertEqual(ids, [self.other.pk])

    def test_listing_images(self):
        url = f'/api/listings/{self.car.pk}/images/'
        self.assertEqual(self.as_(self.stranger).get(url).status_code, 404)
        self.assertEqual(self.as_(self.buyer).get(url).status_code, 200)

    def test_social_comments_and_like(self):
        url = f'/api/listings/{self.car.pk}/comments/'
        self.assertEqual(self.as_(None).get(url).status_code, 404)
        self.assertEqual(self.as_(self.stranger).get(url).status_code, 404)
        self.assertEqual(self.as_(self.buyer).get(url).status_code, 200)
        like = self.as_(self.stranger).post(f'/api/listings/{self.car.pk}/like/')
        self.assertEqual(like.status_code, 404)

    def test_assistant_search_tool(self):
        from assistant.services import _execute_search_cars
        hits = lambda user: [c['id'] for c in _execute_search_cars({}, user)['cars']]
        self.assertNotIn(self.car.pk, hits(self.stranger))
        self.assertNotIn(self.car.pk, hits(self.buyer))


def _ids_count(resp, search_pk):
    data = resp.data.get('results', resp.data)
    return next(row['results_count'] for row in data if row['id'] == search_pk)


# ---------------------------------------------------------------------------
# public_market_q itself
# ---------------------------------------------------------------------------

class PublicMarketQTests(ReservedFixture, TestCase):
    def _visible(self, user=None):
        from cars.models import Listing
        return set(Listing.objects.filter(public_market_q(user)).values_list('pk', flat=True))

    def test_no_duplicate_rows_for_a_party_with_many_matches(self):
        from cars.models import Listing
        # A second, dead reservation + a cancelled order must not multiply rows.
        make_reservation(self.car, self.buyer, self.importer, 'cancelled_by_buyer')
        ImportOrder.objects.create(car=self.car, buyer=self.buyer, importer=self.importer,
                                   total_price=1, status='cancelled')
        qs = Listing.objects.filter(public_market_q(self.buyer))
        self.assertEqual(qs.count(), len(set(qs.values_list('pk', flat=True))))

    def test_is_reserved_alone_hides(self):
        self.other.is_reserved = True
        self.other.save(update_fields=['is_reserved'])
        self.assertNotIn(self.other.pk, self._visible())

    def test_active_order_hides_and_its_buyer_sees(self):
        ImportOrder.objects.create(car=self.other, buyer=self.stranger, importer=self.importer,
                                   total_price=1, status='confirmed')
        self.assertNotIn(self.other.pk, self._visible())
        self.assertIn(self.other.pk, self._visible(self.stranger))

    def test_buyer_of_a_cancelled_order_does_not_see_someone_elses_reservation(self):
        ImportOrder.objects.create(car=self.car, buyer=self.stranger, importer=self.importer,
                                   total_price=1, status='cancelled')
        self.assertNotIn(self.car.pk, self._visible(self.stranger))

    def test_pending_payment_does_not_hide(self):
        make_reservation(self.other, self.stranger, self.importer, 'pending_payment')
        self.assertIn(self.other.pk, self._visible())


# ---------------------------------------------------------------------------
# 2. Release paths
# ---------------------------------------------------------------------------

class ReleaseTests(ReservedFixture, APITestCase):

    def assert_released(self, import_status='available'):
        self.car.refresh_from_db()
        self.assertFalse(self.car.is_reserved)
        self.assertIsNone(self.car.current_reservation_id)
        self.assertEqual(self.car.import_status, import_status)
        self.assertIn(self.car.pk, _ids(self.as_(self.stranger).get('/api/listings/')))
        self.assertIn(self.car.pk, _ids(self.as_(None).get('/api/imported-cars/')))
        self.assertEqual(
            self.as_(self.stranger).get(f'/api/listings/{self.car.pk}/').status_code, 200,
        )

    def test_buyer_cancel(self):
        resp = self.as_(self.buyer).post(f'/api/reservations/{self.res.pk}/cancel/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assert_released()

    def test_importer_cancel(self):
        resp = self.as_(self.importer).post(f'/api/reservations/{self.res.pk}/cancel/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.res.refresh_from_db()
        self.assertEqual(self.res.status, 'cancelled_by_importer')
        self.assert_released()

    def test_reject(self):
        resp = self.as_(self.importer).post(f'/api/reservations/{self.res.pk}/reject/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assert_released()

    def test_expire(self):
        frozen = self.res.created_at + timedelta(days=Reservation.RESERVATION_EXPIRY_DAYS, seconds=1)
        with mock.patch('django.utils.timezone.now', return_value=frozen):
            self.assertEqual(expire_due_reservations(), [self.res.reservation_number])
        self.assert_released()

    def test_expire_restores_the_pre_reservation_status(self):
        car = make_listing(self.importer, title='RFD', import_status='ready_for_delivery')
        res = make_reservation(car, self.buyer, self.importer)
        res.activate()
        res.expire()
        self.car = car
        self.assert_released('ready_for_delivery')

    def _accept(self):
        resp = self.as_(self.importer).post(f'/api/reservations/{self.res.pk}/accept/')
        self.assertEqual(resp.status_code, 200, resp.data)
        order_id = resp.data['order']['id']
        # Still hidden while the order is live.
        self.assertNotIn(self.car.pk, _ids(self.as_(self.stranger).get('/api/listings/')))
        return order_id

    def test_order_cancel(self):
        order_id = self._accept()
        resp = self.as_(self.buyer).post(f'/api/orders/{order_id}/cancel/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assert_released()

    def test_order_cancel_via_update_status(self):
        order_id = self._accept()
        resp = self.as_(self.importer).patch(
            f'/api/orders/{order_id}/update-status/', {'status': 'cancelled'}, format='json',
        )
        self.assertIn(resp.status_code, (200, 201), resp.data)
        self.assert_released()

    def test_order_refund(self):
        order_id = self._accept()
        resp = self.as_(self.staff).patch(
            f'/api/orders/{order_id}/update-status/',
            {'status': 'refunded', 'force': True}, format='json',
        )
        self.assertIn(resp.status_code, (200, 201), resp.data)
        self.assertEqual(ImportOrder.objects.get(pk=order_id).status, 'refunded')
        self.assert_released()

    def test_order_cancel_leaves_a_newer_reservation_alone(self):
        order_id = self._accept()
        order = ImportOrder.objects.get(pk=order_id)
        order.status = 'cancelled'
        order.save(update_fields=['status'])
        # The car goes to someone else before the release runs.
        self.car.is_reserved = False
        self.car.current_reservation = None
        self.car.import_status = 'available'
        self.car.save()
        newer = make_reservation(self.car, self.stranger, self.importer)
        newer.activate()
        order.release_car()
        self.car.refresh_from_db()
        self.assertTrue(self.car.is_reserved)
        self.assertEqual(self.car.current_reservation_id, newer.pk)


# ---------------------------------------------------------------------------
# 3. pay/ locks atomically; a second buyer gets 409
# ---------------------------------------------------------------------------

class PayLockTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.second = make_buyer(email='second@test.com', name='Second')
        self.car = make_listing(self.importer)
        self.res = make_reservation(self.car, self.buyer, self.importer)

    def _pay(self, res, user):
        provider = mock.Mock()
        provider.charge.return_value = mock.Mock(
            success=True, provider='stub', transaction_id='STUB-1', error_message='',
        )
        self.client.force_authenticate(user=user)
        with mock.patch('payments.views.get_payment_provider', return_value=provider):
            return self.client.post(f'/api/reservations/{res.pk}/pay/', {'method': 'mada'}, format='json')

    def test_pay_locks_the_car(self):
        self.assertEqual(self._pay(self.res, self.buyer).status_code, 200)
        self.car.refresh_from_db()
        self.assertTrue(self.car.is_reserved)
        self.assertEqual(self.car.current_reservation_id, self.res.pk)
        self.assertEqual(self.car.import_status, 'reserved')

    def test_second_buyer_gets_409(self):
        self._pay(self.res, self.buyer)
        self.client.force_authenticate(user=self.second)
        resp = self.client.post('/api/reservations/', {'car_id': self.car.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data['detail'], 'This car is currently reserved.')

    def test_lock_failure_rolls_back_the_status_change(self):
        with mock.patch.object(Reservation, '_lock_car', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.res.activate()
        self.res.refresh_from_db()
        self.car.refresh_from_db()
        self.assertEqual(self.res.status, 'pending_payment')
        self.assertFalse(self.car.is_reserved)

    def test_activate_refuses_a_car_held_by_another_reservation(self):
        self.res.activate()
        # Simulates two unpaid reservations racing to pay (the create guard
        # normally prevents the second from existing).
        rival = make_reservation(self.car, self.second, self.importer)
        with self.assertRaises(CarAlreadyReserved):
            rival.activate()
        rival.refresh_from_db()
        self.assertEqual(rival.status, 'pending_payment')

    def test_pay_returns_409_when_the_car_is_already_held(self):
        rival = make_reservation(self.car, self.second, self.importer)
        self.res.activate()
        resp = self._pay(rival, self.second)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data['detail'], 'This car is currently reserved.')


# ---------------------------------------------------------------------------
# 4. reservation_state   5. converted_order
# ---------------------------------------------------------------------------

class SerializerFieldTests(ReservedFixture, APITestCase):

    def _state(self, user, url):
        data = self.as_(user).get(url).data
        rows = data.get('results', data) if isinstance(data, dict) and 'results' in data else [data]
        return {row['id']: row['reservation_state'] for row in rows}

    def test_list_state(self):
        # Only staff see a reserved car in the list at all.
        self.assertNotIn(self.car.pk, self._state(self.buyer, '/api/listings/'))
        self.assertNotIn(self.car.pk, self._state(self.importer, '/api/listings/'))
        self.assertEqual(self._state(self.staff, '/api/listings/')[self.car.pk], 'reserved')
        self.assertIsNone(self._state(self.stranger, '/api/listings/')[self.other.pk])
        self.assertIsNone(self._state(None, '/api/listings/')[self.other.pk])

    def test_detail_state(self):
        url = f'/api/listings/{self.car.pk}/'
        self.assertEqual(self._state(self.buyer, url)[self.car.pk], 'reserved_by_you')
        self.assertEqual(self._state(self.importer, url)[self.car.pk], 'reserved')
        self.assertEqual(self._state(self.staff, url)[self.car.pk], 'reserved')

    def test_detail_state_when_the_lock_has_no_current_reservation(self):
        # Pre-activate() data: car locked, pointer missing.
        self.car.current_reservation = None
        self.car.save(update_fields=['current_reservation'])
        url = f'/api/listings/{self.car.pk}/'
        self.assertEqual(self._state(self.buyer, url)[self.car.pk], 'reserved_by_you')
        self.assertEqual(self._state(self.importer, url)[self.car.pk], 'reserved')

    def test_state_clears_on_release(self):
        self.res.cancel(by='buyer')
        self.car.refresh_from_db()
        self.assertIsNone(reservation_state(self.car, self.buyer))

    def test_converted_order(self):
        url = f'/api/reservations/{self.res.pk}/'
        self.assertIsNone(self.as_(self.buyer).get(url).data['converted_order'])
        resp = self.as_(self.importer).post(f'/api/reservations/{self.res.pk}/accept/')
        order_id = resp.data['order']['id']
        self.assertEqual(resp.data['reservation']['converted_order'], order_id)
        self.assertEqual(self.as_(self.buyer).get(url).data['converted_order'], order_id)
        # Still reserved_by_you while the order is live.
        detail = self.as_(self.buyer).get(f'/api/listings/{self.car.pk}/').data
        self.assertEqual(detail['reservation_state'], 'reserved_by_you')


# ---------------------------------------------------------------------------
# Regression: the Home grid showed a reserved car (fix/reserved-browse)
# ---------------------------------------------------------------------------

class HomeGridRegressionTests(APITestCase):
    """reserve → pay → the list excludes the car for everyone but staff → cancel → back."""

    def setUp(self):
        cache.clear()
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.other_buyer = make_buyer(email='b@test.com', name='B')
        self.car = make_listing(self.importer)

    def _list_ids(self, user, query='page_size=100'):
        self.client.force_authenticate(user=user)
        return _ids(self.client.get(f'/api/listings/?{query}'))

    def _reserve_and_pay(self):
        self.client.force_authenticate(user=self.buyer)
        rid = self.client.post('/api/reservations/', {'car_id': self.car.pk}, format='json').data['id']
        provider = mock.Mock()
        provider.charge.return_value = mock.Mock(
            success=True, provider='stub', transaction_id='STUB', error_message='',
        )
        with mock.patch('payments.views.get_payment_provider', return_value=provider):
            resp = self.client.post(f'/api/reservations/{rid}/pay/', {'method': 'mada'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        return rid

    def test_reserve_pay_cancel_round_trip(self):
        for user in (self.other_buyer, self.buyer, None):
            self.assertIn(self.car.pk, self._list_ids(user))

        rid = self._reserve_and_pay()

        for query in ('page_size=100', 'pagination=cursor&page_size=20', 'search=Camry'):
            for user in (self.other_buyer, self.buyer, self.importer, None):
                self.assertNotIn(self.car.pk, self._list_ids(user, query), (query, user))
        self.client.force_authenticate(user=self.other_buyer)
        self.assertEqual(self.client.get('/api/listings/').data['count'], 0)
        # The buyer still reaches it directly, with the right state.
        self.client.force_authenticate(user=self.buyer)
        detail = self.client.get(f'/api/listings/{self.car.pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['reservation_state'], 'reserved_by_you')
        self.client.force_authenticate(user=self.other_buyer)
        self.assertEqual(self.client.get(f'/api/listings/{self.car.pk}/').status_code, 404)

        self.client.force_authenticate(user=self.buyer)
        self.assertEqual(self.client.post(f'/api/reservations/{rid}/cancel/').status_code, 200)

        for user in (self.other_buyer, self.buyer, None):
            self.assertIn(self.car.pk, self._list_ids(user))
        self.client.force_authenticate(user=self.other_buyer)
        self.assertEqual(self.client.get('/api/listings/').data['count'], 1)

    def test_owner_still_sees_their_own_unlocked_draft_in_the_list(self):
        draft = make_listing(self.importer, title='Draft', status='pending')
        self.assertIn(draft.pk, self._list_ids(self.importer))
        self.assertNotIn(draft.pk, self._list_ids(self.other_buyer))


class MarketCacheInvalidationTests(APITestCase):
    def test_order_and_reservation_changes_bust_the_facet_caches(self):
        from cars.filter_options import CACHE_KEY
        from source_countries.signals import CACHE_KEY_BY_COUNTRY
        importer = make_importer()
        buyer = make_buyer()
        car = make_listing(importer)
        for make_change in (
            lambda: ImportOrder.objects.create(car=car, buyer=buyer, importer=importer,
                                               total_price=1, status='confirmed'),
            lambda: make_reservation(car, buyer, importer, 'pending_review'),
        ):
            cache.set(CACHE_KEY, 'stale')
            cache.set(CACHE_KEY_BY_COUNTRY, 'stale')
            make_change()
            self.assertIsNone(cache.get(CACHE_KEY))
            self.assertIsNone(cache.get(CACHE_KEY_BY_COUNTRY))


class BackfillMigrationTests(TestCase):
    def _run(self):
        import importlib
        from django.apps import apps
        mod = importlib.import_module('orders.migrations.0006_backfill_reservation_locks')
        mod.backfill(apps, None)

    def test_pending_review_without_lock_is_locked(self):
        importer = make_importer()
        buyer = make_buyer()
        car = make_listing(importer, import_status='ready_for_delivery')
        res = make_reservation(car, buyer, importer, 'pending_review')
        self._run()
        car.refresh_from_db()
        res.refresh_from_db()
        self.assertTrue(car.is_reserved)
        self.assertEqual(car.current_reservation_id, res.pk)
        self.assertEqual(car.import_status, 'reserved')
        self.assertEqual(res.car_import_status_before, 'ready_for_delivery')
        # ...and release now restores it.
        res.cancel(by='buyer')
        car.refresh_from_db()
        self.assertEqual((car.is_reserved, car.import_status), (False, 'ready_for_delivery'))

    def test_converted_with_live_order_gets_the_pointer_but_keeps_import_status(self):
        importer = make_importer()
        buyer = make_buyer()
        car = make_listing(importer, import_status='shipping', is_reserved=True)
        order = ImportOrder.objects.create(car=car, buyer=buyer, importer=importer,
                                           total_price=1, status='shipped')
        res = make_reservation(car, buyer, importer, 'converted_to_order', converted_order=order)
        self._run()
        car.refresh_from_db()
        self.assertEqual(car.current_reservation_id, res.pk)
        self.assertEqual(car.import_status, 'shipping')

    def test_ended_reservations_are_left_alone(self):
        importer = make_importer()
        buyer = make_buyer()
        car = make_listing(importer)
        make_reservation(car, buyer, importer, 'cancelled_by_buyer')
        self._run()
        car.refresh_from_db()
        self.assertFalse(car.is_reserved)
        self.assertIsNone(car.current_reservation_id)


# ---------------------------------------------------------------------------
# repair_reservation_flags + one writer for the lock
# ---------------------------------------------------------------------------

class RepairCommandTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()

    def _repair(self, **kwargs):
        from orders.management.commands.repair_reservation_flags import repair
        return repair(**kwargs)

    def test_locks_a_paid_reservation_whose_car_was_left_open(self):
        car = make_listing(self.importer, import_status='ready_for_delivery')
        res = make_reservation(car, self.buyer, self.importer, 'pending_review')
        locked, released = self._repair()
        self.assertEqual((locked, released), ([car.pk], []))
        car.refresh_from_db()
        res.refresh_from_db()
        self.assertTrue(car.is_reserved)
        self.assertEqual(car.current_reservation_id, res.pk)
        self.assertEqual(car.import_status, 'reserved')
        self.assertEqual(res.car_import_status_before, 'ready_for_delivery')

    def test_releases_a_car_whose_reservation_ended(self):
        car = make_listing(self.importer)
        res = make_reservation(car, self.buyer, self.importer)
        res.activate()
        Reservation.objects.filter(pk=res.pk).update(status='cancelled_by_buyer')
        locked, released = self._repair()
        self.assertEqual((locked, released), ([], [car.pk]))
        car.refresh_from_db()
        self.assertFalse(car.is_reserved)
        self.assertIsNone(car.current_reservation_id)
        self.assertEqual(car.import_status, 'available')
        self.assertIn(car.pk, _ids(self.as_client_get()))

    def as_client_get(self):
        self.client.force_authenticate(user=make_buyer(email='onlooker@test.com', name='O'))
        return self.client.get('/api/listings/')

    def test_converted_reservation_with_a_live_order_keeps_its_import_status(self):
        car = make_listing(self.importer, import_status='shipping')
        order = ImportOrder.objects.create(car=car, buyer=self.buyer, importer=self.importer,
                                           total_price=1, status='shipped')
        res = make_reservation(car, self.buyer, self.importer, 'converted_to_order',
                               converted_order=order)
        self._repair()
        car.refresh_from_db()
        self.assertTrue(car.is_reserved)
        self.assertEqual(car.current_reservation_id, res.pk)
        self.assertEqual(car.import_status, 'shipping')

    def test_is_a_no_op_on_consistent_data(self):
        car = make_listing(self.importer)
        res = make_reservation(car, self.buyer, self.importer)
        res.activate()
        make_listing(self.importer, title='Untouched')
        self.assertEqual(self._repair(), ([], []))

    def test_dry_run_writes_nothing(self):
        car = make_listing(self.importer)
        make_reservation(car, self.buyer, self.importer, 'pending_review')
        locked, _ = self._repair(dry_run=True)
        self.assertEqual(locked, [car.pk])
        car.refresh_from_db()
        self.assertFalse(car.is_reserved)

    def test_release_refuses_while_another_live_deal_holds_the_car(self):
        from orders.locks import release_listing
        car = make_listing(self.importer)
        res = make_reservation(car, self.buyer, self.importer)
        res.activate()
        ImportOrder.objects.create(car=car, buyer=self.buyer, importer=self.importer,
                                   total_price=1, status='confirmed')
        release_listing(car)
        car.refresh_from_db()
        self.assertTrue(car.is_reserved)

    def test_order_placed_without_a_reservation_locks_and_releases(self):
        car = make_listing(self.importer)
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/orders/', {'car_id': car.pk}, format='json')
        self.assertIn(resp.status_code, (200, 201), resp.data)
        car.refresh_from_db()
        self.assertEqual(car.import_status, 'reserved')
        stranger = make_buyer(email='nosy@test.com', name='Nosy')
        self.client.force_authenticate(user=stranger)
        self.assertNotIn(car.pk, _ids(self.client.get('/api/listings/')))
        order_id = resp.data['id'] if 'id' in resp.data else resp.data['order']['id']
        self.client.force_authenticate(user=self.buyer)
        self.assertEqual(self.client.post(f'/api/orders/{order_id}/cancel/').status_code, 200)
        car.refresh_from_db()
        self.assertEqual(car.import_status, 'available')
        self.client.force_authenticate(user=stranger)
        self.assertIn(car.pk, _ids(self.client.get('/api/listings/')))

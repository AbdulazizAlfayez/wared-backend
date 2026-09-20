"""
The moderation gate: an unapproved listing is not on the market.

`status` (draft / pending / approved / rejected / changes_requested) decides
whether the public may see a car at all. It is a different thing from
`import_status` (available / reserved / shipping …), which only decides the
badge and whether an on-market car can be reserved — a pending listing is
`import_status='available'` by default, and that must not be mistaken for
being available to buy.

The bug these cover: a pending car appeared in the public browse grid for its
importer, and — worse, for anyone — could be reserved, ordered, messaged
about, led on and reported by id, because those endpoints looked the listing
up with `Listing.objects` and checked only `is_active`.
"""
from django.contrib.auth import get_user_model
from rest_framework import status as http
from rest_framework.test import APITestCase

from cars.models import Listing
from orders.tests import make_buyer, make_importer, make_listing

User = get_user_model()


def make_admin(**kwargs):
    defaults = {
        'email': 'gate-admin@test.com',
        'name': 'Gate Admin',
        'role': 'admin',
        'password': 'testpass123',
        'is_staff': True,
    }
    defaults.update(kwargs)
    return User.objects.create_user(**defaults)


def ids_of(response):
    data = response.data
    rows = data.get('results', data) if isinstance(data, dict) else data
    return {row['id'] for row in rows if isinstance(row, dict) and 'id' in row}


class PendingListingIsNotPublicTests(APITestCase):
    """No public read path may return a listing the admin has not approved."""

    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.admin = make_admin()
        self.approved = make_listing(self.importer, title='Approved Car')
        self.pending = make_listing(
            self.importer, title='Pending Car', status='pending', make='Audi', model='RS6',
        )
        self.draft = make_listing(
            self.importer, title='Draft Car', status='draft', make='Audi', model='Q8',
        )

    def as_(self, user):
        if user is None:
            self.client.force_authenticate(user=None)
        else:
            self.client.force_authenticate(user=user)

    def test_no_public_list_endpoint_returns_a_pending_or_draft_listing(self):
        endpoints = [
            '/api/listings/?page_size=100',
            '/api/listings/?search=Audi',
            '/api/listings/autocomplete/?q=Audi',
            '/api/listings/popular/',
            '/api/listings/featured/',
            '/api/listings/map-pins/',
            '/api/imported-cars/?page_size=100',
            f'/api/importers/{self.importer.pk}/inventory/',
            f'/api/listings/compare/?ids={self.pending.pk},{self.approved.pk}',
        ]
        # The owner is included deliberately: browse is the public market, and
        # the importer's own unapproved car does not belong in it either.
        for viewer in (None, self.buyer, self.importer):
            for url in endpoints:
                self.as_(viewer)
                response = self.client.get(url)
                self.assertEqual(response.status_code, http.HTTP_200_OK, url)
                returned = ids_of(response)
                label = f'{url} as {viewer}'
                self.assertNotIn(self.pending.pk, returned, label)
                self.assertNotIn(self.draft.pk, returned, label)

    def test_the_approved_car_is_still_listed(self):
        """The gate must not take working listings down with it."""
        for viewer in (None, self.buyer, self.importer):
            self.as_(viewer)
            self.assertIn(self.approved.pk, ids_of(self.client.get('/api/listings/?page_size=100')))

    def test_filter_option_counts_exclude_unapproved_cars(self):
        self.as_(None)
        makes = self.client.get('/api/listings/filter-options/').data.get('makes', [])
        self.assertNotIn('Audi', [m['value'] if isinstance(m, dict) else m for m in makes])

    def test_saved_search_results_exclude_unapproved_cars(self):
        self.as_(self.buyer)
        saved = self.client.post(
            '/api/saved-searches/', {'name': 'Audis', 'filters': {'make': 'Audi'}}, format='json',
        )
        self.assertIn(saved.status_code, (http.HTTP_200_OK, http.HTTP_201_CREATED), saved.data)
        results = self.client.get(f'/api/saved-searches/{saved.data["id"]}/results/')
        self.assertNotIn(self.pending.pk, ids_of(results))
        self.assertNotIn(self.draft.pk, ids_of(results))


class PendingListingDetailTests(APITestCase):
    """The detail page 404s for the public, and stays open to its parties."""

    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.admin = make_admin()
        self.pending = make_listing(self.importer, title='Pending Car', status='pending')

    def get_detail(self, user):
        self.client.force_authenticate(user=user)
        return self.client.get(f'/api/listings/{self.pending.pk}/')

    def test_anonymous_gets_404(self):
        self.assertEqual(self.get_detail(None).status_code, http.HTTP_404_NOT_FOUND)

    def test_another_buyer_gets_404(self):
        self.assertEqual(self.get_detail(self.buyer).status_code, http.HTTP_404_NOT_FOUND)

    def test_the_imported_cars_detail_route_404s_too(self):
        self.client.force_authenticate(user=self.buyer)
        response = self.client.get(f'/api/imported-cars/{self.pending.pk}/')
        self.assertEqual(response.status_code, http.HTTP_404_NOT_FOUND)

    def test_the_owner_can_still_open_it(self):
        response = self.get_detail(self.importer)
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'pending')

    def test_an_admin_can_still_open_it(self):
        self.assertEqual(self.get_detail(self.admin).status_code, http.HTTP_200_OK)

    def test_the_owner_still_finds_it_on_their_own_surfaces(self):
        """Hiding it from browse must not hide it from the importer's tools."""
        self.client.force_authenticate(user=self.importer)
        self.assertIn(self.pending.pk, ids_of(self.client.get('/api/listings/?mine=1')))
        self.assertIn(self.pending.pk, ids_of(self.client.get('/api/listings/my/')))


class PendingListingCannotBeActedOnTests(APITestCase):
    """Endpoints that take a listing id must apply the gate as well."""

    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.pending = make_listing(self.importer, title='Pending Car', status='pending')
        self.approved = make_listing(self.importer, title='Approved Car')
        self.client.force_authenticate(user=self.buyer)

    def test_a_pending_car_cannot_be_reserved(self):
        response = self.client.post('/api/reservations/', {'car_id': self.pending.pk}, format='json')
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn('car_id', response.data)

    def test_an_approved_car_can_still_be_reserved(self):
        response = self.client.post('/api/reservations/', {'car_id': self.approved.pk}, format='json')
        self.assertEqual(response.status_code, http.HTTP_201_CREATED, response.data)

    def test_a_pending_car_cannot_be_ordered(self):
        response = self.client.post(
            '/api/orders/', {'car_id': self.pending.pk, 'delivery_method': 'pickup'}, format='json',
        )
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST, response.data)

    def test_a_pending_car_cannot_start_a_conversation(self):
        response = self.client.post('/api/conversations/', {'car_id': self.pending.pk}, format='json')
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn('car_id', response.data)

    def test_a_pending_car_cannot_take_a_lead(self):
        response = self.client.post(
            '/api/leads/', {'listing': self.pending.pk, 'message': 'Interested'}, format='json',
        )
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST, response.data)

    def test_a_pending_car_cannot_be_reported(self):
        response = self.client.post(
            '/api/reports/',
            {'report_type': 'listing', 'reason': 'scam', 'description': 'x', 'listing_id': self.pending.pk},
            format='json',
        )
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST, response.data)
        self.assertIn('listing_id', response.data)

    def test_a_pending_car_cannot_be_booked_for_a_test_drive(self):
        response = self.client.post(
            '/api/appointments/',
            {'listing': self.pending.pk, 'appointment_date': '2030-01-01', 'appointment_time': '10:00'},
            format='json',
        )
        self.assertEqual(response.status_code, http.HTTP_400_BAD_REQUEST, response.data)

    def test_a_pending_car_cannot_be_favourited(self):
        response = self.client.post('/api/favorites/', {'listing': self.pending.pk}, format='json')
        self.assertEqual(response.status_code, http.HTTP_404_NOT_FOUND, response.data)

    def test_the_importer_can_still_message_about_their_own_pending_car(self):
        """The owner is exempt: they may need to talk to a buyer about it."""
        self.client.force_authenticate(user=self.importer)
        response = self.client.post(
            '/api/conversations/',
            {'car_id': self.pending.pk, 'buyer_id': self.buyer.pk},
            format='json',
        )
        self.assertIn(response.status_code, (http.HTTP_200_OK, http.HTTP_201_CREATED), response.data)


class ImporterCannotSelfApproveTests(APITestCase):
    """Only an admin moves a listing through the moderation states."""

    def setUp(self):
        self.importer = make_importer()
        self.admin = make_admin()
        self.client.force_authenticate(user=self.importer)

    def test_create_cannot_ask_for_approved(self):
        response = self.client.post(
            '/api/listings/',
            {
                'title': 'Self approved', 'make': 'Toyota', 'model': 'Camry', 'year': 2022,
                'price': '100000.00', 'mileage': 5000, 'city': 'Riyadh', 'status': 'approved',
            },
            format='json',
        )
        self.assertEqual(response.status_code, http.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['status'], 'pending')
        self.assertEqual(Listing.objects.get(pk=response.data['id']).status, 'pending')

    def test_patch_cannot_set_approved(self):
        listing = make_listing(self.importer, status='pending')
        response = self.client.patch(
            f'/api/listings/{listing.pk}/', {'status': 'approved'}, format='json',
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK, response.data)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'pending')

    def test_bulk_status_change_cannot_set_approved(self):
        listing = make_listing(self.importer, status='pending')
        response = self.client.post(
            '/api/listings/bulk/status/',
            {'listing_ids': [listing.pk], 'status': 'approved'},
            format='json',
        )
        self.assertEqual(response.status_code, http.HTTP_403_FORBIDDEN, response.data)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'pending')

    def test_an_admin_can_approve(self):
        listing = make_listing(self.importer, status='pending')
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(f'/api/listings/{listing.pk}/approve/', {}, format='json')
        self.assertEqual(response.status_code, http.HTTP_200_OK, response.data)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'approved')


class EditingAnApprovedListingReturnsItToPendingTests(APITestCase):
    """An approved listing is a promise; changing the deal re-opens review."""

    def setUp(self):
        self.importer = make_importer()
        self.listing = make_listing(self.importer)
        self.client.force_authenticate(user=self.importer)

    def test_changing_the_price_sends_it_back_to_pending(self):
        response = self.client.patch(
            f'/api/listings/{self.listing.pk}/', {'price': '123456.00'}, format='json',
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK, response.data)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, 'pending')

    def test_it_leaves_the_public_market_immediately(self):
        self.client.patch(f'/api/listings/{self.listing.pk}/', {'price': '123456.00'}, format='json')
        self.client.force_authenticate(user=None)
        self.assertNotIn(
            self.listing.pk, ids_of(self.client.get('/api/listings/?page_size=100')),
        )

    def test_a_cosmetic_edit_does_not(self):
        response = self.client.patch(
            f'/api/listings/{self.listing.pk}/', {'description': 'Now with photos'}, format='json',
        )
        self.assertEqual(response.status_code, http.HTTP_200_OK, response.data)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, 'approved')


class PublicProfileCountsTests(APITestCase):
    """A stranger must not learn what is sitting in the moderation queue."""

    def test_profile_stats_count_only_published_listings(self):
        importer = make_importer()
        make_listing(importer, title='Approved')
        make_listing(importer, title='Pending', status='pending')
        make_listing(importer, title='Draft', status='draft')

        self.client.force_authenticate(user=None)
        response = self.client.get(f'/api/users/{importer.pk}/profile/')
        self.assertEqual(response.status_code, http.HTTP_200_OK)
        self.assertEqual(response.data['stats']['total_listings'], 1)
        self.assertEqual(response.data['stats']['active_listings'], 1)

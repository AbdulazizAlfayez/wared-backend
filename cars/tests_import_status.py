"""
Who may move a car's availability, and when.

The rule is that availability only moves on an approved listing. It is about
the value changing, not about the field appearing in a payload — a form posts
everything it holds, and rejecting an unchanged `available` failed every other
edit in the same request.
"""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}

REJECTION = 'Import status can only be updated on approved listings.'


def _user(email, role='importer'):
    return User.objects.create_user(email=email, password='pw12345!', name='N', role=role)


@override_settings(CACHES=DUMMY_CACHE)
class ImportStatusOnCreateTests(TestCase):
    def setUp(self):
        self.owner = _user('creator@importstatus.test')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_a_new_listing_is_available_without_being_asked(self):
        """A car nobody has reserved is available; the server says so itself."""
        response = self.client.post(
            '/api/listings/',
            {'status': 'draft', 'make': 'Audi', 'model': 'RS7', 'year': 2024},
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['import_status'], 'available')

    def test_create_accepts_an_explicit_available(self):
        """The guard is on update; creating never had one, and still does not."""
        response = self.client.post(
            '/api/listings/',
            {
                'status': 'draft', 'make': 'Audi', 'model': 'RS7', 'year': 2024,
                'import_status': 'available',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)


@override_settings(CACHES=DUMMY_CACHE)
class ImportStatusOnUpdateTests(TestCase):
    def setUp(self):
        self.owner = _user('owner@importstatus.test')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.listing = Listing.objects.create(
            owner=self.owner, title='2024 Audi RS7', make='Audi', model='RS7',
            year=2024, status='draft', import_status='available',
        )

    def _patch(self, payload):
        return self.client.patch(
            f'/api/listings/{self.listing.pk}/', payload, format='json',
        )

    def test_an_unchanged_import_status_does_not_fail_the_request(self):
        """
        This is the whole bug: the form sent `available` back unchanged and
        the 403 took the mileage, the city and the price down with it.
        """
        response = self._patch({
            'mileage': 5000, 'city': 'Riyadh', 'final_price_sar': '300000',
            'import_status': 'available',
        })

        self.assertEqual(response.status_code, 200, response.data)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.mileage, 5000)
        self.assertEqual(self.listing.city, 'Riyadh')

    def test_an_actual_change_on_a_draft_is_still_refused(self):
        response = self._patch({'import_status': 'reserved'})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['error'], REJECTION)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.import_status, 'available')

    def test_the_refusal_does_not_leak_the_rest_of_the_payload_through(self):
        """A refused request changes nothing at all, not just the one field."""
        response = self._patch({'import_status': 'sold', 'city': 'Jeddah'})

        self.assertEqual(response.status_code, 403)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.city, '')

    def test_an_approved_listing_may_have_its_availability_moved(self):
        Listing.objects.filter(pk=self.listing.pk).update(
            status='approved', mileage=1, city='Riyadh', price='300000',
            final_price_sar='300000',
        )

        # `sourcing` and `available` are the only values the serializer
        # accepts before a purchase; `reserved` and `sold` are set by the
        # order flow, never by hand.
        response = self._patch({'import_status': 'sourcing'})

        self.assertEqual(response.status_code, 200, response.data)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.import_status, 'sourcing')

    def test_an_admin_is_not_bound_by_this(self):
        self.client.force_authenticate(_user('admin@importstatus.test', role='admin'))

        response = self._patch({'import_status': 'sourcing'})

        self.assertEqual(response.status_code, 200, response.data)

    def test_a_payload_with_no_import_status_is_untouched_by_the_guard(self):
        response = self._patch({'city': 'Riyadh'})

        self.assertEqual(response.status_code, 200, response.data)

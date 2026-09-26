"""
Draft → submit → pending → approved, and what may not happen along the way.

The rule these pin down: a draft is private and silent, submitting needs no
prior approval, and approval gates public visibility and nothing else.
"""
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing
from cars.serializers import ListingSerializer
from notifications.models import Notification

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}

MINIMAL_DRAFT = {'status': 'draft', 'make': 'BMW', 'model': 'M5', 'year': 2024}


def _user(email, role='importer', **extra):
    return User.objects.create_user(
        email=email, password='pw12345!', name=email.split('@')[0], role=role, **extra
    )


@override_settings(CACHES=DUMMY_CACHE)
class DraftSaveTests(TestCase):
    """A draft is a half-filled form. That is the entire point of it."""

    def setUp(self):
        self.owner = _user('importer@lifecycle.test')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_minimal_draft_is_created(self):
        response = self.client.post('/api/listings/', MINIMAL_DRAFT, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'draft')

    def test_title_is_composed_rather_than_demanded(self):
        """The rule is "title OR make+model" — so compose it."""
        response = self.client.post('/api/listings/', MINIMAL_DRAFT, format='json')

        self.assertEqual(response.data['title'], '2024 BMW M5')

    def test_blank_strings_from_a_form_are_accepted_on_a_draft(self):
        """
        A form posts every field it renders, empty ones included. Rejecting
        those with "This field may not be blank." is what made saving a
        half-filled draft impossible.
        """
        payload = {
            **MINIMAL_DRAFT,
            'title': '',
            'city': '',
            'description': '',
            'vin': '',
            'mileage': None,
        }
        response = self.client.post('/api/listings/', payload, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['city'], '')
        self.assertIsNone(response.data['mileage'])

    def test_a_draft_tells_the_owner_what_is_left(self):
        response = self.client.post('/api/listings/', MINIMAL_DRAFT, format='json')

        self.assertEqual(
            response.data['missing_for_submit'], ['mileage', 'city', 'final_price_sar'],
        )

    def test_saving_a_draft_notifies_nobody(self):
        _user('admin@lifecycle.test', role='admin')

        response = self.client.post('/api/listings/', MINIMAL_DRAFT, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Notification.objects.filter(listing_id=response.data['id']).count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_non_draft_still_has_to_be_complete(self):
        """Relaxing the draft must not relax everything else."""
        response = self.client.post(
            '/api/listings/', {**MINIMAL_DRAFT, 'status': 'pending'}, format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('mileage', response.data)
        self.assertIn('city', response.data)


@override_settings(CACHES=DUMMY_CACHE)
class SubmitTests(TestCase):
    def setUp(self):
        self.owner = _user('owner@lifecycle.test')
        self.admin = _user('admin2@lifecycle.test', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.listing = Listing.objects.create(
            owner=self.owner, title='2024 BMW M5', make='BMW', model='M5',
            year=2024, status='draft',
        )

    def _submit(self):
        return self.client.post(f'/api/listings/{self.listing.pk}/submit/')

    def _complete(self):
        Listing.objects.filter(pk=self.listing.pk).update(
            mileage=9000, city='Riyadh', final_price_sar='320000', price='320000',
        )

    def test_incomplete_submit_names_every_missing_field_at_once(self):
        """
        Sending someone back five times for one field each is how a form gets
        abandoned.
        """
        response = self._submit()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data['missing_for_submit'], ['mileage', 'city', 'final_price_sar'],
        )
        self.assertEqual(response.data['mileage'], 'This field is required.')

    def test_submit_moves_it_into_the_queue_and_tells_the_admins_once(self):
        self._complete()

        response = self._submit()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'pending')
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, 'pending')
        self.assertIsNotNone(self.listing.submitted_at)
        self.assertEqual(
            Notification.objects.filter(
                listing=self.listing, recipient=self.admin,
                notification_type='listing_submitted',
            ).count(),
            1,
        )

    def test_submitting_needs_no_prior_approval(self):
        """
        Neither the listing nor an unverified owner has ever been approved,
        and that must not stand in the way: approval is what this asks for.
        """
        self.owner.is_business_verified = False
        self.owner.save(update_fields=['is_business_verified'])
        self._complete()

        self.assertEqual(self._submit().status_code, 200)

    def test_changes_requested_may_be_resubmitted(self):
        self._complete()
        Listing.objects.filter(pk=self.listing.pk).update(status='changes_requested')

        self.assertEqual(self._submit().status_code, 200)

    def test_rejected_may_be_resubmitted(self):
        self._complete()
        Listing.objects.filter(pk=self.listing.pk).update(status='rejected')

        self.assertEqual(self._submit().status_code, 200)

    def test_an_approved_listing_is_not_submittable(self):
        self._complete()
        Listing.objects.filter(pk=self.listing.pk).update(status='approved')

        self.assertEqual(self._submit().status_code, 409)

    def test_a_stranger_cannot_submit_someone_else_s_listing(self):
        """
        404 rather than 403: a draft is private, so the honest answer to a
        stranger is that there is nothing here — not "there is, but it is not
        yours", which confirms the listing exists.
        """
        self._complete()
        self.client.force_authenticate(_user('stranger@lifecycle.test'))

        self.assertEqual(self._submit().status_code, 404)


@override_settings(CACHES=DUMMY_CACHE)
class VisibilityTests(TestCase):
    """Approval gates public visibility — and that is all it gates."""

    def setUp(self):
        self.owner = _user('owner3@lifecycle.test')
        self.buyer = _user('buyer@lifecycle.test', role='buyer')
        self.client = APIClient()

    def _listing(self, status):
        return Listing.objects.create(
            owner=self.owner, title='2024 BMW M5', make='BMW', model='M5', year=2024,
            mileage=9000, city='Riyadh', price='320000', final_price_sar='320000',
            status=status, is_active=True,
        )

    def _browse_ids(self):
        response = self.client.get('/api/listings/', {'page_size': 100})
        rows = response.data['results'] if isinstance(response.data, dict) else response.data
        return [row['id'] for row in rows]

    def test_a_draft_is_invisible_to_buyers(self):
        listing = self._listing('draft')
        self.client.force_authenticate(self.buyer)

        self.assertNotIn(listing.pk, self._browse_ids())
        self.assertEqual(self.client.get(f'/api/listings/{listing.pk}/').status_code, 404)

    def test_pending_is_invisible_to_buyers(self):
        listing = self._listing('pending')
        self.client.force_authenticate(self.buyer)

        self.assertNotIn(listing.pk, self._browse_ids())

    def test_approval_makes_it_public(self):
        listing = self._listing('pending')
        self.client.force_authenticate(self.buyer)
        self.assertNotIn(listing.pk, self._browse_ids())

        Listing.objects.filter(pk=listing.pk).update(status='approved')

        self.assertIn(listing.pk, self._browse_ids())

    def test_the_owner_sees_their_own_draft(self):
        listing = self._listing('draft')
        self.client.force_authenticate(self.owner)

        response = self.client.get('/api/listings/my/')
        # `/my/` paginates (20 a page); the fallback is for an older server.
        rows = response.data if isinstance(response.data, list) else response.data['results']
        self.assertIn(listing.pk, [row['id'] for row in rows])

    def test_missing_for_submit_is_not_shown_to_a_stranger(self):
        listing = self._listing('approved')
        self.client.force_authenticate(self.buyer)

        response = self.client.get(f'/api/listings/{listing.pk}/')
        self.assertNotIn('missing_for_submit', response.data)


class MissingForSubmitTests(TestCase):
    """The computation itself, without a request in the way."""

    def test_an_empty_draft_is_missing_everything_but_its_identity(self):
        listing = Listing(title='', make='', model='', year=None, mileage=None, city='')

        self.assertEqual(
            ListingSerializer.compute_missing_for_submit(listing),
            ['title', 'make', 'model', 'year', 'mileage', 'city', 'final_price_sar'],
        )

    def test_a_complete_listing_is_missing_nothing(self):
        listing = Listing(
            title='2024 BMW M5', make='BMW', model='M5', year=2024,
            mileage=9000, city='Riyadh', final_price_sar='320000',
        )

        self.assertEqual(ListingSerializer.compute_missing_for_submit(listing), [])

    def test_either_price_column_satisfies_the_price(self):
        listing = Listing(
            title='t', make='BMW', model='M5', year=2024, mileage=1, city='Riyadh',
            price='320000',
        )

        self.assertEqual(ListingSerializer.compute_missing_for_submit(listing), [])

    def test_zero_mileage_is_a_real_answer(self):
        """A new car has 0 km — `not value` would have called that missing."""
        listing = Listing(
            title='t', make='BMW', model='M5', year=2024, mileage=0, city='Riyadh',
            final_price_sar='320000',
        )

        self.assertEqual(ListingSerializer.compute_missing_for_submit(listing), [])

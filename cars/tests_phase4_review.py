"""
Re-review, drafts, and the reviewer's note reaching the person who must act.
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from calculator.models import ExchangeRate
from cars.models import Listing
from cars.review_rules import changed_review_fields, requires_rereview

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}


def _user(email, role='importer'):
    return User.objects.create_user(email=email, password='pw12345!', name='N', role=role)


@override_settings(CACHES=DUMMY_CACHE)
class ReviewRuleTests(TestCase):
    """The rule itself, without a request in the way."""

    def test_identity_and_money_changes_trigger_review(self):
        for field, before, after in (
            ('make', 'Toyota', 'Lexus'),
            ('model', 'Camry', 'ES'),
            ('year', 2023, 2024),
            ('vin', 'A' * 17, 'B' * 17),
            ('price', '100000', '90000'),
            ('margin_sar', '5000', '9000'),
            ('source_price', '10000', '12000'),
        ):
            with self.subTest(field=field):
                self.assertTrue(
                    requires_rereview('approved', {field: before}, {field: after}),
                )

    def test_corrections_do_not(self):
        for field, before, after in (
            ('description', 'old', 'new'),
            ('mileage', 10000, 12000),
            ('city', 'Riyadh', 'Jeddah'),
            ('color', 'white', 'black'),
        ):
            with self.subTest(field=field):
                self.assertFalse(
                    requires_rereview('approved', {field: before}, {field: after}),
                )

    def test_new_photos_trigger_review_on_their_own(self):
        self.assertTrue(requires_rereview('approved', {}, {}, photos_changed=True))

    def test_only_approved_listings_are_affected(self):
        """A pending listing is already in the queue; rejected has its own rule."""
        for status in ('pending', 'rejected', 'changes_requested', 'draft'):
            with self.subTest(status=status):
                self.assertFalse(
                    requires_rereview(status, {'price': '1'}, {'price': '2'}),
                )

    def test_an_unchanged_value_is_not_a_change(self):
        """3 and Decimal('3.00') are the same price."""
        self.assertEqual(
            changed_review_fields({'price': '100000'}, {'price': '100000.00'}), set(),
        )
        self.assertEqual(changed_review_fields({'vin': None}, {'vin': ''}), set())


@override_settings(CACHES=DUMMY_CACHE)
class ApprovedEditTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.update_or_create(
            currency='usd', defaults={'rate_to_sar': Decimal('3.750000')},
        )
        self.owner = _user('owner@test.com')
        self.listing = Listing.objects.create(
            title='2023 Toyota Camry', make='Toyota', model='Camry', year=2023,
            mileage=14500, city='Riyadh', price=Decimal('100000'),
            owner=self.owner, status='approved',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_a_price_edit_pulls_the_listing_off_the_market(self):
        response = self.client.patch(
            f'/api/listings/{self.listing.pk}/', {'price': '120000'}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['requires_review'])
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, 'pending')

    def test_a_description_edit_does_not(self):
        response = self.client.patch(
            f'/api/listings/{self.listing.pk}/',
            {'description': 'Now with a full service history.'},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['requires_review'])
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, 'approved')

    def test_a_listing_back_in_review_leaves_the_public_market(self):
        from cars.visibility import public_market_q

        self.assertTrue(Listing.objects.filter(public_market_q(None), pk=self.listing.pk).exists())

        self.client.patch(f'/api/listings/{self.listing.pk}/', {'year': 2024}, format='json')

        self.assertFalse(
            Listing.objects.filter(public_market_q(None), pk=self.listing.pk).exists(),
            'An edited listing must not stay on the market while it waits for review.',
        )


@override_settings(CACHES=DUMMY_CACHE)
class DraftTests(TestCase):
    def setUp(self):
        self.owner = _user('drafter@test.com')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _create_draft(self):
        return self.client.post('/api/listings/', {
            'title': 'Draft car', 'make': 'Toyota', 'model': 'Camry', 'year': 2023,
            'mileage': 100, 'city': 'Riyadh', 'price': '50000', 'status': 'draft',
        }, format='json')

    def test_a_listing_can_be_created_as_a_draft(self):
        response = self._create_draft()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Listing.objects.get(pk=response.data['id']).status, 'draft')

    def test_without_asking_a_listing_is_still_born_pending(self):
        response = self.client.post('/api/listings/', {
            'title': 'Normal', 'make': 'Toyota', 'model': 'Camry', 'year': 2023,
            'mileage': 100, 'city': 'Riyadh', 'price': '50000',
        }, format='json')

        self.assertEqual(Listing.objects.get(pk=response.data['id']).status, 'pending')

    def test_a_draft_is_invisible_publicly(self):
        from cars.visibility import public_market_q

        pk = self._create_draft().data['id']
        self.assertFalse(Listing.objects.filter(public_market_q(None), pk=pk).exists())

    def test_a_draft_is_freely_editable_without_review(self):
        pk = self._create_draft().data['id']

        response = self.client.patch(
            f'/api/listings/{pk}/', {'price': '75000'}, format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['requires_review'])
        self.assertEqual(Listing.objects.get(pk=pk).status, 'draft')

    def test_submitting_a_draft_puts_it_in_the_queue(self):
        pk = self._create_draft().data['id']

        response = self.client.post(f'/api/listings/{pk}/submit/')

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Listing.objects.get(pk=pk).status, 'pending')

    def test_only_a_draft_can_be_submitted(self):
        pk = self._create_draft().data['id']
        self.client.post(f'/api/listings/{pk}/submit/')

        again = self.client.post(f'/api/listings/{pk}/submit/')

        self.assertEqual(again.status_code, 409)

    def test_someone_elses_draft_cannot_be_submitted(self):
        pk = self._create_draft().data['id']
        other = APIClient()
        other.force_authenticate(_user('stranger@test.com'))

        # 404 rather than 403: the queryset never shows a stranger someone
        # else's draft, so the refusal does not confirm it exists.
        self.assertEqual(other.post(f'/api/listings/{pk}/submit/').status_code, 404)
        self.assertEqual(Listing.objects.get(pk=pk).status, 'draft')


@override_settings(CACHES=DUMMY_CACHE)
class OwnerFeedbackTests(TestCase):
    def setUp(self):
        self.owner = _user('feedback@test.com')
        self.listing = Listing.objects.create(
            title='x', make='Toyota', model='Camry', year=2023, mileage=1,
            city='Riyadh', price=Decimal('50000'), owner=self.owner,
        )

    def _get(self, as_user):
        client = APIClient()
        client.force_authenticate(as_user)
        return client.get(f'/api/listings/{self.listing.pk}/')

    def test_changes_requested_reaches_the_owner(self):
        """
        `request-changes` writes to admin_notes, which is stripped for
        non-admins — so without owner_feedback the owner saw that changes were
        wanted but never what to change.
        """
        self.listing.status = 'changes_requested'
        self.listing.admin_notes = 'The second photo is of a different car.'
        self.listing.save()

        response = self._get(self.owner)

        self.assertEqual(
            response.data['owner_feedback'], 'The second photo is of a different car.',
        )
        self.assertNotIn('admin_notes', response.data)

    def test_a_rejection_reason_reaches_the_owner(self):
        self.listing.status = 'rejected'
        self.listing.rejection_reason = 'Mileage does not match the VIN report.'
        self.listing.save()

        self.assertEqual(
            self._get(self.owner).data['owner_feedback'],
            'Mileage does not match the VIN report.',
        )

    def test_an_approved_listing_has_nothing_outstanding(self):
        self.listing.status = 'approved'
        self.listing.admin_notes = 'internal chatter'
        self.listing.save()

        self.assertIsNone(self._get(self.owner).data['owner_feedback'])

    def test_a_stranger_sees_neither_the_note_nor_the_feedback(self):
        self.listing.status = 'rejected'
        self.listing.rejection_reason = 'private'
        self.listing.save()

        response = self._get(_user('nosy@test.com', role='user'))

        self.assertNotIn('owner_feedback', response.data)
        self.assertNotIn('rejection_reason', response.data)

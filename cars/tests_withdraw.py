"""
Taking a car off the market, and putting it back.

The three things that make withdrawal different from everything next to it:

  * it is not deletion — the row stays, and `withdrawn_at` is what tells the
    two apart, because both set `is_active=False`;
  * it is not moderation — `status` stays `approved`, so relisting needs no
    second review;
  * it is not unconditional — a buyer who has paid to hold the car outranks
    the seller's change of mind, and the refusal says so in both languages.
"""
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing
from cars.withdraw import is_withdrawn
from notifications.models import Notification
from orders.models import ImportOrder, Reservation

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}


def _user(email, role='importer'):
    return User.objects.create_user(email=email, password='pw12345!', name='N', role=role)


@override_settings(CACHES=DUMMY_CACHE)
class WithdrawTests(TestCase):
    def setUp(self):
        self.owner = _user('owner@withdraw.test')
        self.buyer = _user('buyer@withdraw.test', role='buyer')
        self.client = APIClient()
        self.listing = Listing.objects.create(
            owner=self.owner, title='2024 Audi RS6', make='Audi', model='RS6', year=2024,
            mileage=9000, city='Riyadh', price='320000', final_price_sar='320000',
            status='approved', is_active=True,
        )

    def _withdraw(self, user=None):
        self.client.force_authenticate(user or self.owner)
        return self.client.post(f'/api/listings/{self.listing.pk}/withdraw/')

    def _relist(self, user=None):
        self.client.force_authenticate(user or self.owner)
        return self.client.post(f'/api/listings/{self.listing.pk}/relist/')

    # ── the happy path ───────────────────────────────────────────────────

    def test_an_owner_can_take_their_car_off_the_market(self):
        response = self._withdraw()

        self.listing.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.listing.is_active)
        self.assertIsNotNone(self.listing.withdrawn_at)

    def test_the_approval_survives_it(self):
        """Relisting must not cost the importer another trip through review."""
        self._withdraw()

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, 'approved')

    def test_the_owner_is_told_it_is_withdrawn(self):
        response = self._withdraw()

        # The moderation column still says `approved`; the owner's vocabulary
        # has a word for what they actually did.
        self.assertEqual(response.data['status'], 'withdrawn')

    def test_relisting_puts_it_back(self):
        self._withdraw()

        response = self._relist()

        self.listing.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.listing.is_active)
        self.assertIsNone(self.listing.withdrawn_at)
        self.assertEqual(response.data['status'], 'approved')

    # ── who can see it ───────────────────────────────────────────────────

    def test_buyers_stop_seeing_it_everywhere(self):
        self._withdraw()

        self.client.force_authenticate(self.buyer)
        detail = self.client.get(f'/api/listings/{self.listing.pk}/')
        browse = self.client.get('/api/listings/')
        rows = browse.data.get('results', browse.data)

        self.assertEqual(detail.status_code, 404)
        self.assertNotIn(self.listing.pk, [row['id'] for row in rows])

    def test_an_anonymous_visitor_stops_seeing_it(self):
        self._withdraw()
        self.client.force_authenticate(None)

        self.assertEqual(
            self.client.get(f'/api/listings/{self.listing.pk}/').status_code, 404,
        )

    def test_the_owner_can_still_open_it(self):
        """
        Without this there is no way back: `relist` goes through the same
        `get_object`, so an owner locked out of their own withdrawn car could
        never put it on the market again.
        """
        self._withdraw()
        self.client.force_authenticate(self.owner)

        response = self.client.get(f'/api/listings/{self.listing.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'withdrawn')

    def test_it_stays_in_my_listings(self):
        self._withdraw()
        self.client.force_authenticate(self.owner)

        response = self.client.get('/api/listings/my/')
        rows = response.data['results']

        row = next(r for r in rows if r['id'] == self.listing.pk)
        self.assertEqual(row['status'], 'withdrawn')

    # ── what it is not ───────────────────────────────────────────────────

    def test_a_deleted_listing_is_not_a_withdrawn_one(self):
        """
        Both set `is_active=False`. Only one of them may be relisted, and only
        one belongs under the owner's "Withdrawn" filter.
        """
        self.client.force_authenticate(self.owner)
        self.client.delete(f'/api/listings/{self.listing.pk}/')

        self.listing.refresh_from_db()
        self.assertFalse(self.listing.is_active)
        self.assertIsNone(self.listing.withdrawn_at)
        self.assertFalse(is_withdrawn(self.listing))

        # And it cannot be relisted back into existence: a deleted listing is
        # gone for its owner too, so `relist` never reaches the rule — it 404s
        # on the row, which is the honest answer rather than a conflict.
        self.assertEqual(self._relist().status_code, 404)

    def test_a_deleted_listing_stays_out_of_my_listings(self):
        self.client.force_authenticate(self.owner)
        self.client.delete(f'/api/listings/{self.listing.pk}/')

        response = self.client.get('/api/listings/my/')

        self.assertNotIn(
            self.listing.pk, [row['id'] for row in response.data['results']],
        )

    # ── the refusals ─────────────────────────────────────────────────────

    def _assert_bilingual_conflict(self, response, code):
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], code)
        self.assertTrue(response.data['detail'])
        # The Arabic is the point: an importer reading the app in Arabic gets
        # the reason, not an English sentence in the middle of their screen.
        self.assertTrue(response.data['detail_ar'])
        self.assertNotEqual(response.data['detail'], response.data['detail_ar'])

    def test_a_paid_reservation_blocks_it(self):
        Reservation.objects.create(
            car=self.listing, buyer=self.buyer, status='pending_review',
        )

        self._assert_bilingual_conflict(self._withdraw(), 'listing_reserved')

    def test_an_active_order_blocks_it(self):
        ImportOrder.objects.create(
            car=self.listing, buyer=self.buyer, status='confirmed',
        )

        self._assert_bilingual_conflict(self._withdraw(), 'listing_in_deal')

    def test_a_finished_reservation_does_not_block_it(self):
        """A cancelled hold is not a claim; the car is the seller's again."""
        Reservation.objects.create(
            car=self.listing, buyer=self.buyer, status='cancelled_by_buyer',
        )

        self.assertEqual(self._withdraw().status_code, 200)

    def test_an_unapproved_listing_cannot_be_withdrawn(self):
        Listing.objects.filter(pk=self.listing.pk).update(status='pending')

        self._assert_bilingual_conflict(self._withdraw(), 'listing_not_approved')

    def test_withdrawing_twice_is_a_conflict_not_a_second_withdrawal(self):
        self._withdraw()

        self._assert_bilingual_conflict(self._withdraw(), 'already_withdrawn')

    def test_relisting_what_was_never_withdrawn_is_a_conflict(self):
        self._assert_bilingual_conflict(self._relist(), 'not_withdrawn')

    def test_a_car_that_acquired_a_deal_while_away_stays_away(self):
        self._withdraw()
        ImportOrder.objects.create(
            car=self.listing, buyer=self.buyer, status='confirmed',
        )

        self._assert_bilingual_conflict(self._relist(), 'listing_in_deal')

    # ── who may do it ────────────────────────────────────────────────────

    def test_a_stranger_cannot(self):
        response = self._withdraw(user=self.buyer)

        self.assertEqual(response.status_code, 403)
        self.listing.refresh_from_db()
        self.assertTrue(self.listing.is_active)

    def test_an_admin_cannot_either(self):
        """
        Withdrawal is a seller's commercial decision, not a moderation tool —
        an admin taking a car off the market silently, with no reason recorded
        anywhere, is what `reject` and `request-changes` exist to prevent.
        """
        admin = _user('admin@withdraw.test', role='admin')

        response = self._withdraw(user=admin)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'owner_only')

    def test_it_needs_a_login(self):
        self.client.force_authenticate(None)

        self.assertIn(
            self.client.post(f'/api/listings/{self.listing.pk}/withdraw/').status_code,
            (401, 403),
        )

    # ── the receipt ──────────────────────────────────────────────────────

    def test_the_importer_is_notified_both_ways(self):
        self._withdraw()
        self._relist()

        types = list(
            Notification.objects.filter(recipient=self.owner)
            .order_by('created_at').values_list('notification_type', flat=True)
        )

        self.assertIn('listing_withdrawn', types)
        self.assertIn('listing_relisted', types)

    def test_the_notification_names_the_car_and_is_bilingual(self):
        self._withdraw()

        note = Notification.objects.get(
            recipient=self.owner, notification_type='listing_withdrawn',
        )

        self.assertIn('2024 Audi RS6', note.message)
        self.assertIn('/', note.title)  # "عربي / English", as every listing note is
        self.assertEqual(note.listing_id, self.listing.pk)


@override_settings(CACHES=DUMMY_CACHE)
class WithdrawRuleTests(TestCase):
    """The rule itself, without a request in the way."""

    def setUp(self):
        self.owner = _user('rule@withdraw.test')
        self.listing = Listing.objects.create(
            owner=self.owner, title='2023 Kia', make='Kia', model='Telluride', year=2023,
            mileage=100, city='Riyadh', price='1', final_price_sar='1',
            status='approved', is_active=True,
        )

    def test_is_withdrawn_needs_both_halves(self):
        self.assertFalse(is_withdrawn(self.listing))

        # Soft-deleted: inactive, never withdrawn.
        self.listing.is_active = False
        self.assertFalse(is_withdrawn(self.listing))

        self.listing.withdrawn_at = timezone.now()
        self.assertTrue(is_withdrawn(self.listing))

        # Relisted rows keep neither half.
        self.listing.is_active = True
        self.listing.withdrawn_at = None
        self.assertFalse(is_withdrawn(self.listing))

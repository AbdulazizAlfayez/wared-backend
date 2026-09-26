"""
`/api/listings/my/` — the importer's own cars, at the cost of one page.

Three things are pinned here, because all three have regressed before:

1. The query count. Twenty cards used to cost 148 queries: five per row for
   the owner stats, three per row for the view windows, one per row for the
   promotions, the owner's profile, the social counts and the reservation.
   The page is worth six queries and no more, whatever the sort.
2. The order. `?sort=attention` is the screen's default because the cars an
   importer has to do something about are the reason they opened it.
3. Pagination. It arrived late — the endpoint used to answer with a bare list
   — so the shape is asserted rather than assumed.
"""
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing, ViewLog
from favorites.models import Favorite
from messaging.models import Conversation
from social.models import ListingLike

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}

#: The page the endpoint promises, and the number of rows these tests build.
PAGE_SIZE = 20


def _user(email, role='importer'):
    return User.objects.create_user(email=email, password='pw12345!', name='N', role=role)


@override_settings(CACHES=DUMMY_CACHE)
class MyListingsQueryCountTests(TestCase):
    """A full page of twenty, with stats on every row, in six queries."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = _user('owner@my-listings.test')
        cls.buyer = _user('buyer@my-listings.test', role='buyer')
        cls.listings = [
            Listing.objects.create(
                owner=cls.owner, title=f'2024 Car {i}', make='Audi', model='RS6',
                year=2024, mileage=9000 + i, city='Riyadh',
                price='320000', final_price_sar='320000',
                status='approved', is_active=True, view_count=i,
            )
            for i in range(PAGE_SIZE + 3)
        ]
        # Every kind of related row the stats count, so no query can be saved
        # by a listing simply having nothing attached to it.
        for listing in cls.listings:
            Favorite.objects.create(user=cls.buyer, listing=listing)
            ListingLike.objects.create(user=cls.buyer, listing=listing)
            Conversation.objects.create(
                listing=listing, buyer=cls.buyer, seller=cls.owner,
            )
            ViewLog.objects.create(listing=listing, user=cls.buyer, ip_address='127.0.0.1')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _page(self, query=''):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f'/api/listings/my/{query}')
        self.assertEqual(response.status_code, 200)
        return response, queries

    def test_twenty_rows_cost_six_queries(self):
        response, queries = self._page(f'?page_size={PAGE_SIZE}')

        self.assertEqual(len(response.data['results']), PAGE_SIZE)
        self.assertLessEqual(
            len(queries), 6,
            'A page of twenty grew a per-row query:\n'
            + '\n'.join(q['sql'][:160] for q in queries.captured_queries),
        )

    def test_every_sort_costs_the_same(self):
        """
        The ordering is the database's, not Python's — a sort that had to load
        the rows to order them would also have to load their stats one by one.
        """
        for sort in ('attention', 'newest', 'most_viewed'):
            with self.subTest(sort=sort):
                _, queries = self._page(f'?sort={sort}&page_size={PAGE_SIZE}')
                self.assertLessEqual(len(queries), 6)

    def test_the_stats_are_on_every_row_and_are_right(self):
        response, _ = self._page(f'?page_size={PAGE_SIZE}')

        for row in response.data['results']:
            stats = row['owner_stats']
            self.assertEqual(stats['saves'], 1)
            self.assertEqual(stats['likes'], 1)
            self.assertEqual(stats['messages'], 1)
            self.assertEqual(stats['reservations'], 0)
            self.assertEqual(stats['views'], row['view_count'])

    def test_it_paginates(self):
        response, _ = self._page(f'?page_size={PAGE_SIZE}')

        self.assertEqual(response.data['count'], PAGE_SIZE + 3)
        self.assertIsNotNone(response.data['next'])
        self.assertEqual(len(response.data['results']), PAGE_SIZE)

    def test_the_second_page_is_the_remainder(self):
        response, _ = self._page(f'?page_size={PAGE_SIZE}&page=2')

        self.assertEqual(len(response.data['results']), 3)
        self.assertIsNone(response.data['next'])

    def test_the_default_page_is_twenty(self):
        response, _ = self._page()

        self.assertEqual(len(response.data['results']), PAGE_SIZE)


@override_settings(CACHES=DUMMY_CACHE)
class MyListingsSortTests(TestCase):
    """What leads, per sort."""

    def setUp(self):
        self.owner = _user('owner@sort.test')
        self.buyer = _user('buyer@sort.test', role='buyer')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def _listing(self, status, title, views=0, conversations=0):
        listing = Listing.objects.create(
            owner=self.owner, title=title, make='Audi', model='RS6', year=2024,
            mileage=9000, city='Riyadh', price='320000', final_price_sar='320000',
            status=status, is_active=True, view_count=views,
        )
        # A conversation is unique per (listing, buyer, seller), so "three
        # people are asking about this car" needs three people.
        for i in range(conversations):
            Conversation.objects.create(
                listing=listing, seller=self.owner,
                buyer=_user(f'asker{i}@{listing.pk}.test', role='buyer'),
            )
        return listing

    def _ids(self, sort=None):
        query = f'?sort={sort}' if sort else ''
        response = self.client.get(f'/api/listings/my/{query}')
        self.assertEqual(response.status_code, 200)
        return [row['id'] for row in response.data['results']]

    def test_attention_puts_the_sent_back_cars_first(self):
        approved = self._listing('approved', 'Approved')
        pending = self._listing('pending', 'Pending')
        draft = self._listing('draft', 'Draft')
        rejected = self._listing('rejected', 'Rejected')
        changes = self._listing('changes_requested', 'Changes')

        order = self._ids('attention')

        # Sent back, then the owner's own unfinished work, then somebody
        # else's turn, then the cars that are simply selling.
        self.assertEqual(
            order,
            [changes.pk, rejected.pk, draft.pk, pending.pk, approved.pk],
        )

    def test_within_a_band_the_one_people_are_asking_about_leads(self):
        quiet = self._listing('changes_requested', 'Quiet', conversations=0)
        busy = self._listing('changes_requested', 'Busy', conversations=3)
        some = self._listing('changes_requested', 'Some', conversations=1)

        self.assertEqual(self._ids('attention'), [busy.pk, some.pk, quiet.pk])

    def test_most_viewed_is_by_the_counter_the_importer_watches(self):
        few = self._listing('approved', 'Few', views=3)
        many = self._listing('approved', 'Many', views=90)
        some = self._listing('approved', 'Some', views=40)

        self.assertEqual(self._ids('most_viewed'), [many.pk, some.pk, few.pk])

    def test_newest_is_the_default(self):
        first = self._listing('approved', 'First')
        second = self._listing('approved', 'Second')

        self.assertEqual(self._ids(), [second.pk, first.pk])
        self.assertEqual(self._ids('newest'), [second.pk, first.pk])

    def test_an_unknown_sort_is_a_400_rather_than_a_silent_newest(self):
        """
        Ignoring it would have the client show "Most viewed" over a list that
        is not sorted by views — a lie the user cannot see through.
        """
        response = self.client.get('/api/listings/my/?sort=price')

        self.assertEqual(response.status_code, 400)
        self.assertIn('attention', response.data['error'])

    def test_only_the_owner_s_own_cars(self):
        mine = self._listing('approved', 'Mine')
        stranger = _user('stranger@sort.test')
        Listing.objects.create(
            owner=stranger, title='Theirs', make='Audi', model='RS6', year=2024,
            mileage=9000, city='Riyadh', price='320000', final_price_sar='320000',
            status='approved', is_active=True,
        )

        self.assertEqual(self._ids('attention'), [mine.pk])

    def test_it_needs_a_login(self):
        self.client.force_authenticate(None)

        self.assertIn(self.client.get('/api/listings/my/').status_code, (401, 403))

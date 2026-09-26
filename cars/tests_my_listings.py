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
from datetime import timedelta

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing, ViewLog
from favorites.models import Favorite
from messaging.models import Conversation, Message
from orders.models import Reservation
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

    def _listing(self, status, title, views=0, conversations=0,
                 unanswered=0, pending_reservations=0):
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
        for i in range(unanswered):
            self._unanswered_thread(listing, f'waiting{i}@{listing.pk}.test')
        for i in range(pending_reservations):
            Reservation.objects.create(
                car=listing, buyer=_user(f'res{i}@{listing.pk}.test', role='buyer'),
                importer=self.owner, status='pending_review',
                platform_fee_sar='99.00',
            )
        return listing

    def _unanswered_thread(self, listing, email, answered=False):
        """A buyer asked; the owner has answered only if `answered`."""
        buyer = _user(email, role='buyer')
        conversation = Conversation.objects.create(
            listing=listing, seller=self.owner, buyer=buyer,
        )
        Message.objects.create(
            conversation=conversation, sender=buyer, content='Is it still here?',
        )
        if answered:
            Message.objects.create(
                conversation=conversation, sender=self.owner, content='It is.',
            )
        return conversation

    def _touch(self, listing, when):
        """
        When this car was last touched. `Listing` has no `updated_at`, so the
        sort reads `status_changed_at` and falls back to creation — see the
        `_touched` annotation in `my_listings`.
        """
        Listing.objects.filter(pk=listing.pk).update(status_changed_at=when)
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

    def test_a_selling_car_with_somebody_waiting_beats_a_quiet_one(self):
        quiet = self._listing('approved', 'Quiet')
        unanswered = self._listing('approved', 'Unanswered', unanswered=1)
        reserved = self._listing('approved', 'Reserved', pending_reservations=1)

        order = self._ids('attention')

        self.assertEqual(set(order[:2]), {unanswered.pk, reserved.pk})
        self.assertEqual(order[2], quiet.pk)

    def test_an_answered_thread_is_not_somebody_waiting(self):
        answered = self._listing('approved', 'Answered')
        self._unanswered_thread(answered, 'asked@answered.test', answered=True)
        waiting = self._listing('approved', 'Waiting', unanswered=1)

        self.assertEqual(self._ids('attention'), [waiting.pk, answered.pk])

    def test_a_busy_but_fully_answered_car_does_not_jump_the_queue(self):
        """Total conversations is the wrong measure; unanswered is the right one."""
        busy = self._listing('approved', 'Busy', conversations=3)
        one_waiting = self._listing('approved', 'One waiting', unanswered=1)

        self.assertEqual(self._ids('attention'), [one_waiting.pk, busy.pk])

    def test_the_rest_fall_back_to_most_recently_touched(self):
        from django.utils import timezone

        now = timezone.now()
        older = self._touch(self._listing('approved', 'Older'), now - timedelta(days=3))
        newer = self._touch(self._listing('approved', 'Newer'), now - timedelta(hours=1))
        oldest = self._touch(self._listing('approved', 'Oldest'), now - timedelta(days=9))

        self.assertEqual(self._ids('attention'), [newer.pk, older.pk, oldest.pk])

    def test_most_viewed_is_by_the_counter_the_importer_watches(self):
        few = self._listing('approved', 'Few', views=3)
        many = self._listing('approved', 'Many', views=90)
        some = self._listing('approved', 'Some', views=40)

        self.assertEqual(self._ids('most_viewed'), [many.pk, some.pk, few.pk])

    def test_newest_is_by_when_the_car_was_listed(self):
        first = self._listing('approved', 'First')
        second = self._listing('approved', 'Second')

        self.assertEqual(self._ids('newest'), [second.pk, first.pk])

    def test_attention_is_the_default(self):
        """No `sort` is the screen's first paint, and it leads with the work."""
        self._listing('approved', 'Selling')
        sent_back = self._listing('changes_requested', 'Sent back')

        self.assertEqual(self._ids()[0], sent_back.pk)
        self.assertEqual(self._ids(), self._ids('attention'))

    def test_a_withdrawn_car_sorts_last_whatever_else_is_true_of_it(self):
        """
        It is the one row waiting on nobody. Given a buyer's unanswered
        message as well — which would otherwise rank it as a sale in progress
        — it still goes to the bottom.
        """
        from cars.withdraw import withdraw

        busy = self._listing('approved', 'Withdrawn but asked about', conversations=2)
        quiet = self._listing('approved', 'Quiet')
        sent_back = self._listing('changes_requested', 'Sent back')
        withdraw(busy)

        order = self._ids('attention')

        self.assertEqual(order[-1], busy.pk)
        self.assertEqual(order[0], sent_back.pk)
        self.assertIn(quiet.pk, order)

    def test_withdrawn_is_still_returned_by_my(self):
        from cars.withdraw import withdraw

        listing = self._listing('approved', 'Gone quiet')
        withdraw(listing)

        rows = self.client.get('/api/listings/my/').data['results']

        row = next(r for r in rows if r['id'] == listing.pk)
        self.assertEqual(row['status'], 'withdrawn')

    def test_the_status_filter_narrows_to_one_chip(self):
        from cars.withdraw import withdraw

        withdrawn = self._listing('approved', 'Withdrawn')
        self._listing('approved', 'Live')
        self._listing('draft', 'Draft')
        withdraw(withdrawn)

        rows = self.client.get('/api/listings/my/?status=withdrawn').data['results']

        self.assertEqual([r['id'] for r in rows], [withdrawn.pk])
        self.assertEqual(rows[0]['status'], 'withdrawn')

    def test_approved_does_not_answer_for_a_withdrawn_car(self):
        """
        `withdrawn` is not a column value — the row stays `approved` — so the
        Approved chip has to exclude it by hand, or it would file a car buyers
        cannot see under the label for the ones they can.
        """
        from cars.withdraw import withdraw

        withdrawn = self._listing('approved', 'Withdrawn')
        live = self._listing('approved', 'Live')
        withdraw(withdrawn)

        rows = self.client.get('/api/listings/my/?status=approved').data['results']

        self.assertEqual([r['id'] for r in rows], [live.pk])

    def test_every_chip_value_is_accepted(self):
        for wanted in ('draft', 'pending', 'approved', 'rejected',
                       'changes_requested', 'sold', 'withdrawn'):
            with self.subTest(status=wanted):
                response = self.client.get(f'/api/listings/my/?status={wanted}')
                self.assertEqual(response.status_code, 200)

    def test_an_unknown_status_is_a_400_rather_than_a_silent_everything(self):
        """A chip that quietly filtered nothing would show the whole list."""
        response = self.client.get('/api/listings/my/?status=archived')

        self.assertEqual(response.status_code, 400)
        self.assertIn('withdrawn', response.data['error'])

    def test_the_filter_combines_with_the_sort(self):
        from cars.withdraw import withdraw

        first = self._listing('approved', 'First', views=10)
        second = self._listing('approved', 'Second', views=90)
        withdraw(first)
        withdraw(second)

        rows = self.client.get('/api/listings/my/?status=withdrawn&sort=most_viewed').data['results']

        self.assertEqual([r['id'] for r in rows], [second.pk, first.pk])

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


@override_settings(CACHES=DUMMY_CACHE)
class OwnerStatsBulkTests(TestCase):
    """
    The counts, in bulk. Four aggregates and no more — and the same numbers
    `owner_stats` gives one at a time, because a stats line that disagrees
    with the card it sits under is worse than no stats line.
    """

    def setUp(self):
        self.owner = _user('owner@bulk.test')
        self.other = _user('other@bulk.test')
        self.buyer = _user('buyer@bulk.test', role='buyer')
        self.listing = self._listing(self.owner, 'Mine', views=7)
        self.theirs = self._listing(self.other, 'Theirs')

    def _listing(self, owner, title, views=0):
        return Listing.objects.create(
            owner=owner, title=title, make='Audi', model='RS6', year=2024,
            mileage=9000, city='Riyadh', price='320000', final_price_sar='320000',
            status='approved', is_active=True, view_count=views,
        )

    def test_it_matches_the_one_at_a_time_version(self):
        from cars.stats import owner_stats, owner_stats_bulk

        Favorite.objects.create(user=self.buyer, listing=self.listing)
        ListingLike.objects.create(user=self.buyer, listing=self.listing)
        Conversation.objects.create(
            listing=self.listing, buyer=self.buyer, seller=self.owner,
        )
        Reservation.objects.create(
            car=self.listing, buyer=self.buyer, importer=self.owner,
            status='pending_review', platform_fee_sar='99.00',
        )

        bulk = owner_stats_bulk([self.listing])
        self.assertEqual(bulk[self.listing.pk], owner_stats(self.listing))
        self.assertEqual(bulk[self.listing.pk], {
            'views': 7, 'saves': 1, 'likes': 1, 'messages': 1, 'reservations': 1,
        })

    def test_instances_cost_four_queries(self):
        from cars.stats import owner_stats_bulk

        rows = [self._listing(self.owner, f'Car {i}') for i in range(PAGE_SIZE)]
        with CaptureQueriesContext(connection) as captured:
            owner_stats_bulk(rows)
        self.assertLessEqual(len(captured), 4, [q['sql'][:90] for q in captured])

    def test_bare_ids_work_and_cost_one_more(self):
        from cars.stats import owner_stats_bulk

        Favorite.objects.create(user=self.buyer, listing=self.listing)
        with CaptureQueriesContext(connection) as captured:
            bulk = owner_stats_bulk([self.listing.pk])
        self.assertEqual(bulk[self.listing.pk]['saves'], 1)
        self.assertEqual(bulk[self.listing.pk]['views'], 7)
        # The counters have to be read back; with instances they are free.
        self.assertLessEqual(len(captured), 5, [q['sql'][:90] for q in captured])

    def test_a_user_restricts_the_answer_to_their_own_cars(self):
        from cars.stats import owner_stats_bulk

        both = [self.listing.pk, self.theirs.pk]
        self.assertEqual(
            set(owner_stats_bulk(both, user=self.owner)), {self.listing.pk},
        )
        self.assertEqual(
            set(owner_stats_bulk(both, user=self.other)), {self.theirs.pk},
        )
        # Instances are filtered the same way.
        self.assertEqual(
            set(owner_stats_bulk([self.listing, self.theirs], user=self.owner)),
            {self.listing.pk},
        )

    def test_nothing_in_nothing_out(self):
        from cars.stats import owner_stats_bulk

        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(owner_stats_bulk([]), {})
        self.assertEqual(len(captured), 0)

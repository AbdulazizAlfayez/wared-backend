"""
The importer's point of view (feat/importer-pov).

1. Owner stats on a listing, and `is_owner` everywhere.
2. /api/importers/me/desk/ — the Home screen in one call.
3. An importer cannot reserve a car; saving and liking stay open to them.
4. The public seller card: response time, rating, live cars, member since.
5. Conversations: `unanswered_by_me`, and grouping by listing.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from cars.models import Listing, ViewLog
from favorites.models import Favorite
from importers.desk import ATTENTION_LIMIT, STALE_ORDER_DAYS, UNANSWERED_HOURS
from importers.models import ImporterProfile
from messaging.models import Conversation, Message
from orders.models import ImportOrder, Reservation
from orders.tests import make_buyer, make_importer, make_listing
from orders.tests_reservation_flow import confirm_balance, make_reservation
from social.models import ListingLike

User = get_user_model()
DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}


def conversation(listing, buyer, seller, **kwargs):
    return Conversation.objects.create(
        listing=listing, buyer=buyer, seller=seller, **kwargs,
    )


def message(conv, sender, content='hello', **kwargs):
    """Post a message the way the API does, counters and all."""
    msg = Message.objects.create(
        conversation=conv, sender=sender, content=content, **kwargs,
    )
    conv.update_last_message(msg)
    conv.refresh_from_db()
    return msg


def age(obj, field, when):
    """Force an auto_now / auto_now_add timestamp, which save() would reset."""
    type(obj).objects.filter(pk=obj.pk).update(**{field: when})
    obj.refresh_from_db()
    return obj


# ---------------------------------------------------------------------------
# 1. Owner stats and is_owner
# ---------------------------------------------------------------------------

@override_settings(CACHES=DUMMY_CACHE)
class OwnerStatsTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.stranger = make_buyer(email='stranger@test.com', name='Stranger')
        self.listing = make_listing(self.importer, final_price_sar=Decimal('165000'))

    def _detail(self, user=None):
        self.client.force_authenticate(user=user)
        return self.client.get(f'/api/listings/{self.listing.pk}/')

    def test_owner_sees_the_five_counts(self):
        Favorite.objects.create(user=self.buyer, listing=self.listing)
        ListingLike.objects.create(user=self.buyer, listing=self.listing)
        conversation(self.listing, self.buyer, self.importer)
        make_reservation(self.listing, self.buyer, self.importer)
        Listing.objects.filter(pk=self.listing.pk).update(view_count=42)

        stats = self._detail(self.importer).data['owner_stats']
        self.assertEqual(stats, {
            'views': 42, 'saves': 1, 'likes': 1, 'messages': 1, 'reservations': 1,
        })

    def test_zero_is_reported_not_omitted(self):
        stats = self._detail(self.importer).data['owner_stats']
        self.assertEqual(
            stats, {'views': 0, 'saves': 0, 'likes': 0, 'messages': 0, 'reservations': 0},
        )

    def test_a_buyer_never_sees_them(self):
        self.assertNotIn('owner_stats', self._detail(self.buyer).data)
        self.assertNotIn('owner_stats', self._detail(None).data)

    def test_staff_do(self):
        admin = make_buyer(email='admin@test.com', name='Admin', role='admin')
        self.assertIn('owner_stats', self._detail(admin).data)

    def test_my_listings_carries_them(self):
        self.client.force_authenticate(user=self.importer)
        rows = self.client.get('/api/listings/my/').data
        self.assertEqual(rows[0]['owner_stats']['views'], 0)

    def test_counts_are_per_listing(self):
        other = make_listing(self.importer, title='Other')
        Favorite.objects.create(user=self.buyer, listing=other)
        self.assertEqual(self._detail(self.importer).data['owner_stats']['saves'], 0)

    def test_bulk_matches_one_at_a_time(self):
        from cars.stats import owner_stats, owner_stats_bulk

        other = make_listing(self.importer, title='Other')
        Favorite.objects.create(user=self.buyer, listing=self.listing)
        ListingLike.objects.create(user=self.stranger, listing=other)
        bulk = owner_stats_bulk([self.listing, other])
        self.assertEqual(bulk[self.listing.pk], owner_stats(self.listing))
        self.assertEqual(bulk[other.pk], owner_stats(other))

    def test_is_owner_on_detail(self):
        self.assertTrue(self._detail(self.importer).data['is_owner'])
        self.assertFalse(self._detail(self.buyer).data['is_owner'])
        self.assertFalse(self._detail(None).data['is_owner'])

    def test_is_owner_on_the_list_serializer(self):
        self.client.force_authenticate(user=self.importer)
        row = next(
            r for r in self.client.get('/api/listings/').data['results']
            if r['id'] == self.listing.pk
        )
        self.assertTrue(row['is_owner'])
        self.client.force_authenticate(user=self.buyer)
        row = next(
            r for r in self.client.get('/api/listings/').data['results']
            if r['id'] == self.listing.pk
        )
        self.assertFalse(row['is_owner'])


@override_settings(CACHES=DUMMY_CACHE)
class ViewDebounceTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer()
        self.listing = make_listing(self.importer)

    def _open(self, user=None):
        self.client.force_authenticate(user=user)
        return self.client.get(f'/api/listings/{self.listing.pk}/')

    def _views(self):
        self.listing.refresh_from_db()
        return self.listing.view_count

    def test_a_non_owner_view_counts(self):
        self._open(self.buyer)
        self.assertEqual(self._views(), 1)

    def test_the_owner_looking_at_their_own_car_does_not(self):
        self._open(self.importer)
        self.assertEqual(self._views(), 0)

    def test_a_second_view_within_the_hour_does_not(self):
        self._open(self.buyer)
        self._open(self.buyer)
        self.assertEqual(self._views(), 1)

    def test_a_view_after_the_hour_does(self):
        self._open(self.buyer)
        log = ViewLog.objects.get(listing=self.listing)
        age(log, 'viewed_at', timezone.now() - timedelta(hours=1, minutes=1))
        self._open(self.buyer)
        self.assertEqual(self._views(), 2)

    def test_unique_views_count_the_person_once(self):
        self._open(self.buyer)
        log = ViewLog.objects.get(listing=self.listing)
        age(log, 'viewed_at', timezone.now() - timedelta(hours=2))
        self._open(self.buyer)
        self.listing.refresh_from_db()
        self.assertEqual((self.listing.view_count, self.listing.unique_view_count), (2, 1))

    def test_anonymous_views_are_debounced_by_ip(self):
        self._open(None)
        self._open(None)
        self.assertEqual(self._views(), 1)


# ---------------------------------------------------------------------------
# 2. The desk
# ---------------------------------------------------------------------------

@override_settings(CACHES=DUMMY_CACHE)
class DeskTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer(name='Fahad Al Otaibi')
        self.listing = make_listing(self.importer, title='2022 Toyota Camry TRD')

    def _desk(self, user=None):
        self.client.force_authenticate(user=user or self.importer)
        resp = self.client.get('/api/importers/me/desk/')
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    # -- counts ------------------------------------------------------------
    def test_counts_split_live_review_and_drafts(self):
        make_listing(self.importer, title='Pending', status='pending')
        make_listing(self.importer, title='Sent back', status='changes_requested')
        make_listing(self.importer, title='Draft', status='draft')
        counts = self._desk()['counts']
        self.assertEqual(counts['live'], 1)
        self.assertEqual(counts['in_review'], 2)
        self.assertEqual(counts['drafts'], 1)

    def test_counts_cover_the_deal_pipeline(self):
        make_reservation(self.listing, self.buyer, self.importer, 'pending_review')
        order = ImportOrder.objects.create(
            car=make_listing(self.importer, title='Ordered'), buyer=self.buyer,
            importer=self.importer, total_price=Decimal('1'), status='confirmed',
        )
        confirm_balance(order, '1.00')
        conv = conversation(self.listing, self.buyer, self.importer)
        message(conv, self.buyer)

        counts = self._desk()['counts']
        self.assertEqual(counts['reservations_pending'], 1)
        self.assertEqual(counts['orders_active'], 1)
        self.assertEqual(counts['unread_messages'], 1)
        self.assertEqual(counts['payouts_pending'], 1)

    def test_another_importers_work_is_not_counted(self):
        other = make_importer(email='other@test.com', name='Other')
        make_listing(other, title='Theirs')
        make_reservation(make_listing(other, title='T2'), self.buyer, other, 'pending_review')
        counts = self._desk()['counts']
        self.assertEqual(counts['live'], 1)
        self.assertEqual(counts['reservations_pending'], 0)

    def test_my_own_unread_replies_do_not_count(self):
        conv = conversation(self.listing, self.buyer, self.importer)
        message(conv, self.importer)
        self.assertEqual(self._desk()['counts']['unread_messages'], 0)

    # -- attention ---------------------------------------------------------
    def test_a_pending_reservation_is_the_first_thing_shown(self):
        res = make_reservation(self.listing, self.buyer, self.importer, 'pending_review')
        item = self._desk()['attention'][0]
        self.assertEqual(item['type'], 'reservation_expiring')
        self.assertEqual(item['reservation_id'], res.pk)
        self.assertEqual(item['car_title'], '2022 Toyota Camry TRD')
        self.assertEqual(item['buyer_first_name'], 'Fahad')
        self.assertGreater(item['hours_remaining'], 0)

    def test_a_confirmed_payment_needs_acknowledging(self):
        order = ImportOrder.objects.create(
            car=self.listing, buyer=self.buyer, importer=self.importer,
            total_price=Decimal('1'), status='confirmed',
        )
        confirm_balance(order, '1.00')
        item = next(
            i for i in self._desk()['attention'] if i['type'] == 'payment_to_acknowledge'
        )
        self.assertEqual(item['order_id'], order.pk)
        self.assertEqual(item['car_title'], '2022 Toyota Camry TRD')

    def test_a_listing_sent_back_carries_the_reviewers_note(self):
        sent_back = make_listing(
            self.importer, title='Sent back', status='changes_requested',
            admin_notes='  Photos are too dark.  ',
        )
        item = next(
            i for i in self._desk()['attention'] if i['type'] == 'changes_requested'
        )
        self.assertEqual(item['listing_id'], sent_back.pk)
        self.assertEqual(item['note'], 'Photos are too dark.')

    def test_an_unanswered_message_appears_only_after_the_grace_period(self):
        conv = conversation(self.listing, self.buyer, self.importer)
        msg = message(conv, self.buyer)
        self.assertEqual(
            [i for i in self._desk()['attention'] if i['type'] == 'unanswered_message'], [],
        )

        age(msg, 'created_at', timezone.now() - timedelta(hours=UNANSWERED_HOURS + 2))
        item = next(
            i for i in self._desk()['attention'] if i['type'] == 'unanswered_message'
        )
        self.assertEqual(item['conversation_id'], conv.pk)
        self.assertEqual(item['buyer_first_name'], 'Fahad')
        self.assertEqual(item['car_title'], '2022 Toyota Camry TRD')
        self.assertGreater(item['hours_since'], UNANSWERED_HOURS)

    def test_a_thread_i_answered_is_not_owed_a_reply(self):
        conv = conversation(self.listing, self.buyer, self.importer)
        age(message(conv, self.buyer), 'created_at', timezone.now() - timedelta(days=3))
        age(message(conv, self.importer), 'created_at', timezone.now() - timedelta(days=2))
        self.assertEqual(
            [i for i in self._desk()['attention'] if i['type'] == 'unanswered_message'], [],
        )

    def test_a_system_note_is_not_a_question(self):
        conv = conversation(self.listing, self.buyer, self.importer)
        msg = message(conv, self.buyer, content='reserved', is_system=True)
        age(msg, 'created_at', timezone.now() - timedelta(days=3))
        self.assertEqual(
            [i for i in self._desk()['attention'] if i['type'] == 'unanswered_message'], [],
        )

    def test_a_stale_order_is_flagged_with_its_status(self):
        order = ImportOrder.objects.create(
            car=self.listing, buyer=self.buyer, importer=self.importer,
            total_price=Decimal('1'), status='sourcing',
        )
        age(order, 'updated_at', timezone.now() - timedelta(days=STALE_ORDER_DAYS + 3))
        item = next(
            i for i in self._desk()['attention'] if i['type'] == 'order_status_due'
        )
        self.assertEqual(item['order_id'], order.pk)
        self.assertEqual(item['current_status'], 'sourcing')
        self.assertGreaterEqual(item['days_in_status'], STALE_ORDER_DAYS)

    def test_a_fresh_order_is_not(self):
        ImportOrder.objects.create(
            car=self.listing, buyer=self.buyer, importer=self.importer,
            total_price=Decimal('1'), status='sourcing',
        )
        self.assertEqual(
            [i for i in self._desk()['attention'] if i['type'] == 'order_status_due'], [],
        )

    def test_the_most_urgent_type_comes_first(self):
        sent_back = make_listing(
            self.importer, title='Sent back', status='changes_requested', admin_notes='fix',
        )
        order = ImportOrder.objects.create(
            car=make_listing(self.importer, title='Stale'), buyer=self.buyer,
            importer=self.importer, total_price=Decimal('1'), status='sourcing',
        )
        age(order, 'updated_at', timezone.now() - timedelta(days=STALE_ORDER_DAYS + 1))
        make_reservation(self.listing, self.buyer, self.importer, 'pending_review')

        types = [item['type'] for item in self._desk()['attention']]
        self.assertEqual(types[0], 'reservation_expiring')
        self.assertLess(types.index('changes_requested'), types.index('order_status_due'))

    def test_the_list_is_capped(self):
        for i in range(ATTENTION_LIMIT + 4):
            make_listing(
                self.importer, title=f'Sent back {i}',
                status='changes_requested', admin_notes='fix',
            )
        self.assertEqual(len(self._desk()['attention']), ATTENTION_LIMIT)

    def test_a_quiet_desk_has_nothing_to_say(self):
        desk = self._desk()
        self.assertEqual(desk['attention'], [])
        self.assertIsNone(desk['strip'])

    # -- strip -------------------------------------------------------------
    def test_the_strip_is_the_first_item_with_a_text_key(self):
        res = make_reservation(self.listing, self.buyer, self.importer, 'pending_review')
        strip = self._desk()['strip']
        self.assertEqual(strip['type'], 'reservation_expiring')
        self.assertEqual(strip['text_key'], 'desk.strip.reservationExpiring')
        self.assertEqual(strip['params']['reservation_id'], res.pk)
        self.assertEqual(strip['params']['buyer_first_name'], 'Fahad')
        self.assertNotIn('type', strip['params'])

    # -- recent listings ---------------------------------------------------
    def test_recent_listings_are_newest_first_and_capped_at_six(self):
        for i in range(8):
            make_listing(self.importer, title=f'Car {i}')
        rows = self._desk()['recent_listings']
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]['title'], 'Car 7')

    def test_recent_listings_carry_owner_stats_and_status(self):
        Favorite.objects.create(user=self.buyer, listing=self.listing)
        row = next(r for r in self._desk()['recent_listings'] if r['id'] == self.listing.pk)
        self.assertEqual(row['owner_stats']['saves'], 1)
        self.assertEqual(row['status'], 'approved')
        self.assertEqual(row['import_status'], 'available')
        self.assertTrue(row['is_owner'])

    def test_drafts_are_included_so_the_importer_can_finish_them(self):
        draft = make_listing(self.importer, title='Half done', status='draft')
        ids = [row['id'] for row in self._desk()['recent_listings']]
        self.assertIn(draft.pk, ids)

    # -- access ------------------------------------------------------------
    def test_buyers_are_refused(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.get('/api/importers/me/desk/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get('/api/importers/me/desk/')
        self.assertIn(resp.status_code, (401, 403))


# ---------------------------------------------------------------------------
# 3. Importers do not reserve
# ---------------------------------------------------------------------------

@override_settings(CACHES=DUMMY_CACHE)
class ImporterCannotReserveTests(APITestCase):
    def setUp(self):
        self.seller = make_importer()
        self.other_importer = make_importer(email='other@test.com', name='Other')
        self.buyer = make_buyer()
        self.listing = make_listing(self.seller)

    def test_an_importer_gets_403_with_a_bilingual_code(self):
        self.client.force_authenticate(user=self.other_importer)
        resp = self.client.post('/api/reservations/', {'car_id': self.listing.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(resp.data['code'], 'importer_cannot_reserve')
        self.assertTrue(resp.data['detail'])
        self.assertTrue(resp.data['detail_ar'])
        self.assertFalse(Reservation.objects.exists())

    def test_a_buyer_still_can(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/reservations/', {'car_id': self.listing.pk}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_an_importer_may_still_save_a_car(self):
        self.client.force_authenticate(user=self.other_importer)
        resp = self.client.post(f'/api/imported-cars/{self.listing.pk}/favorite/')
        self.assertIn(resp.status_code, (200, 201), resp.data)
        self.assertTrue(Favorite.objects.filter(user=self.other_importer).exists())

    def test_an_importer_may_still_like_a_car(self):
        self.client.force_authenticate(user=self.other_importer)
        resp = self.client.post(f'/api/listings/{self.listing.pk}/like/')
        self.assertIn(resp.status_code, (200, 201), resp.data)
        self.assertTrue(ListingLike.objects.filter(user=self.other_importer).exists())


# ---------------------------------------------------------------------------
# 4. The public seller card
# ---------------------------------------------------------------------------

@override_settings(CACHES=DUMMY_CACHE)
class SellerCardTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.importer.is_business_verified = True
        self.importer.save(update_fields=['is_business_verified'])
        self.buyer = make_buyer()
        self.profile, _ = ImporterProfile.objects.get_or_create(
            user=self.importer,
            defaults={'business_name': 'Gulf Imports'},
        )

    def _card(self):
        resp = self.client.get(f'/api/importers/{self.profile.pk}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def _thread(self, asked_hours_ago, replied_hours_ago, listing=None):
        listing = listing or make_listing(self.importer)
        conv = conversation(listing, self.buyer, self.importer)
        now = timezone.now()
        age(message(conv, self.buyer), 'created_at', now - timedelta(hours=asked_hours_ago))
        if replied_hours_ago is not None:
            age(
                message(conv, self.importer),
                'created_at', now - timedelta(hours=replied_hours_ago),
            )
        return conv

    def test_no_replies_yet_reads_null_not_zero(self):
        self._thread(asked_hours_ago=5, replied_hours_ago=None)
        self.assertIsNone(self._card()['response_time_hours'])

    def test_one_reply_is_its_own_median(self):
        self._thread(asked_hours_ago=8, replied_hours_ago=6)
        self.assertEqual(self._card()['response_time_hours'], 2.0)

    def test_the_median_ignores_one_forgotten_thread(self):
        self._thread(asked_hours_ago=10, replied_hours_ago=9)    # 1h
        self._thread(asked_hours_ago=10, replied_hours_ago=8)    # 2h
        self._thread(asked_hours_ago=500, replied_hours_ago=100)  # 400h
        # A mean would read ~134 hours; the median says what to expect.
        self.assertEqual(self._card()['response_time_hours'], 2.0)

    def test_only_the_first_reply_in_a_thread_is_measured(self):
        conv = self._thread(asked_hours_ago=10, replied_hours_ago=9)
        now = timezone.now()
        age(message(conv, self.buyer), 'created_at', now - timedelta(hours=8))
        age(message(conv, self.importer), 'created_at', now - timedelta(hours=1))
        self.assertEqual(self._card()['response_time_hours'], 1.0)

    def test_older_than_the_window_does_not_count(self):
        conv = self._thread(asked_hours_ago=10, replied_hours_ago=9)
        age(conv, 'created_at', timezone.now() - timedelta(days=120))
        self.assertIsNone(self._card()['response_time_hours'])

    def test_rating_reviews_cars_live_and_member_since(self):
        ImporterProfile.objects.filter(pk=self.profile.pk).update(
            average_rating=Decimal('4.75'), total_reviews=12,
        )
        make_listing(self.importer, title='Live one')
        make_listing(self.importer, title='A draft', status='draft')

        card = self._card()
        self.assertEqual(card['rating_avg'], 4.75)
        self.assertEqual(card['reviews_count'], 12)
        self.assertEqual(card['cars_live'], 1)
        self.assertTrue(card['member_since'])

    def test_an_unrated_importer_reads_null_rather_than_zero_stars(self):
        card = self._card()
        self.assertIsNone(card['rating_avg'])
        self.assertEqual(card['reviews_count'], 0)

    def test_a_reserved_car_is_not_live(self):
        listing = make_listing(self.importer, title='Reserved')
        res = make_reservation(listing, self.buyer, self.importer)
        res.activate()
        self.assertEqual(self._card()['cars_live'], 0)

    def test_the_card_never_leaks_contact_details(self):
        card = self._card()
        for field in ('phone', 'whatsapp', 'email', 'website'):
            self.assertNotIn(field, card)


# ---------------------------------------------------------------------------
# 5. Conversations
# ---------------------------------------------------------------------------

@override_settings(CACHES=DUMMY_CACHE)
class ConversationInboxTests(APITestCase):
    def setUp(self):
        self.importer = make_importer()
        self.buyer = make_buyer(name='Fahad Al Otaibi')
        self.other_buyer = make_buyer(email='b2@test.com', name='Noura')
        self.camry = make_listing(self.importer, title='Camry')
        self.lexus = make_listing(self.importer, title='Lexus')

    def _rows(self, user, query=''):
        self.client.force_authenticate(user=user)
        resp = self.client.get(f'/api/conversations/{query}')
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def test_a_buyers_question_is_unanswered_by_the_importer(self):
        conv = conversation(self.camry, self.buyer, self.importer)
        message(conv, self.buyer)

        row = self._rows(self.importer)['results'][0]
        self.assertTrue(row['unanswered_by_me'])
        # And the mirror image for the buyer, who is waiting on a reply.
        self.assertFalse(self._rows(self.buyer)['results'][0]['unanswered_by_me'])

    def test_answering_clears_it(self):
        conv = conversation(self.camry, self.buyer, self.importer)
        message(conv, self.buyer)
        message(conv, self.importer, content='On its way')
        self.assertFalse(self._rows(self.importer)['results'][0]['unanswered_by_me'])

    def test_reading_without_replying_does_not_clear_it(self):
        conv = conversation(self.camry, self.buyer, self.importer)
        message(conv, self.buyer)
        # Open the thread, the way the app does on entering it.
        self.client.force_authenticate(user=self.importer)
        self.client.post(f'/api/conversations/{conv.pk}/mark-read/')

        row = self._rows(self.importer)['results'][0]
        self.assertEqual(row['unread_count'], 0)
        self.assertTrue(row['unanswered_by_me'])

    def test_an_empty_thread_is_not_unanswered(self):
        conversation(self.camry, self.buyer, self.importer)
        self.assertFalse(self._rows(self.importer)['results'][0]['unanswered_by_me'])

    def test_a_system_note_is_not_unanswered(self):
        conv = conversation(self.camry, self.buyer, self.importer)
        message(conv, self.buyer, content='reserved', is_system=True)
        self.assertFalse(self._rows(self.importer)['results'][0]['unanswered_by_me'])

    def test_grouping_gathers_threads_under_their_car(self):
        first = conversation(self.camry, self.buyer, self.importer)
        message(first, self.buyer)
        second = conversation(self.camry, self.other_buyer, self.importer)
        message(second, self.other_buyer)
        third = conversation(self.lexus, self.buyer, self.importer)
        message(third, self.importer)

        data = self._rows(self.importer, '?group=listing')
        self.assertEqual(data['count'], 2)
        by_car = {g['listing']['id']: g for g in data['groups']}
        self.assertEqual(len(by_car[self.camry.pk]['conversations']), 2)
        self.assertEqual(len(by_car[self.lexus.pk]['conversations']), 1)

    def test_each_group_totals_what_is_owed(self):
        first = conversation(self.camry, self.buyer, self.importer)
        message(first, self.buyer)
        second = conversation(self.camry, self.other_buyer, self.importer)
        message(second, self.other_buyer)

        group = self._rows(self.importer, '?group=listing')['groups'][0]
        self.assertEqual(group['unanswered_total'], 2)
        self.assertEqual(group['unread_total'], 2)

    def test_the_flat_shape_is_untouched_without_the_param(self):
        conv = conversation(self.camry, self.buyer, self.importer)
        message(conv, self.buyer)
        data = self._rows(self.importer)
        self.assertIn('results', data)
        self.assertNotIn('groups', data)

    def test_a_buyer_may_group_too(self):
        conv = conversation(self.camry, self.buyer, self.importer)
        message(conv, self.importer)
        data = self._rows(self.buyer, '?group=listing')
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['groups'][0]['listing']['id'], self.camry.pk)

    def test_grouping_shows_only_my_own_threads(self):
        conv = conversation(self.camry, self.other_buyer, self.importer)
        message(conv, self.other_buyer)
        data = self._rows(self.buyer, '?group=listing')
        self.assertEqual(data['count'], 0)


class ImporterCityRegionTests(APITestCase):
    """The seller card says where an importer is, region included."""

    @classmethod
    def setUpTestData(cls):
        from locations.models import City, Region

        cls.user = User.objects.create_user(
            email='region-importer@test.sa', password='Passw0rd!x',
            name='Region Importer', role='importer',
        )
        region = Region.objects.create(
            name_en='Eastern Province', name_ar='المنطقة الشرقية', slug='eastern',
        )
        city = City.objects.create(name_en='Dammam', name_ar='الدمام', region=region)
        cls.profile, _ = ImporterProfile.objects.update_or_create(
            user=cls.user, defaults={'business_name': 'Region Imports', 'city': city},
        )

    def test_the_city_block_names_its_region(self):
        response = self.client.get(f'/api/importers/{self.profile.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        city = response.data['city']
        self.assertEqual(city['name_en'], 'Dammam')
        self.assertEqual(city['region']['name_en'], 'Eastern Province')
        self.assertEqual(city['region']['name_ar'], 'المنطقة الشرقية')

    def test_an_importer_with_no_city_is_unaffected(self):
        self.profile.city = None
        self.profile.save(update_fields=['city'])
        response = self.client.get(f'/api/importers/{self.profile.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['city'])

    def test_the_seller_card_fields_are_all_there(self):
        """What the mobile profile header reads, in one payload."""
        response = self.client.get(f'/api/importers/{self.profile.pk}/')
        for field in ('response_time_hours', 'rating_avg', 'reviews_count',
                      'cars_live', 'member_since', 'is_verified'):
            self.assertIn(field, response.data)

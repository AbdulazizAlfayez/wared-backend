"""
Social — likes and public comments on listings.

Throttling note: DRF keeps throttle history in the Django cache, and the cache
is NOT isolated by the test runner. Local dev sets REDIS_URL, so the cache is
the same Redis a running dev server uses — calling cache.clear() here would
flush it. These tests therefore override CACHES instead: DummyCache for the
functional classes (history is never stored, so the rates never fire) and a
named LocMemCache for the one class that deliberately tests throttling.
"""
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework import serializers
from rest_framework.test import APIClient

from accounts.models import User
from auditlog.models import AuditLog
from cars.models import Listing

from .models import CommentReport, ListingComment, ListingLike
from .serializers import CONTACT_ERROR, DELETED_BODY, validate_comment_body
from .throttles import CommentRateThrottle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, role='user', name='Test User'):
    return User.objects.create_user(email=email, password='pass1234', name=name, role=role)


def make_listing(owner, title='Test Listing', status='approved', **extra):
    """
    Defaults to a PUBLIC listing: public_market_q() requires status='approved'
    plus is_active=True and an import_status outside ('reserved', 'sold'). The
    model defaults cover the latter two; status defaults to 'pending', so it
    has to be passed explicitly.
    """
    defaults = dict(
        make='Toyota', model='Camry', year=2022,
        price=50000, mileage=10000, city='Riyadh',
    )
    defaults.update(extra)
    return Listing.objects.create(owner=owner, title=title, status=status, **defaults)


#: DummyCache stores nothing, so SimpleRateThrottle always sees an empty
#: history and the functional tests below never trip a rate limit.
NO_THROTTLE_CACHE = {
    'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'},
}


# ---------------------------------------------------------------------------
# Comment body validation — the contact filter
# ---------------------------------------------------------------------------

class CommentContactFilterTests(SimpleTestCase):
    """
    Comments talk about years, mileages and prices, all of which are long runs
    of digits. So the filter rejects only a Saudi mobile shape or an unbroken
    run of 9+ digits — deliberately narrower than messaging's _PHONE_RE, which
    rejected "2019 88000 km".
    """

    REJECTED = [
        # Local form, with and without separators.
        '0512345678',
        'call me on 0512345678',
        '05 1234 5678',
        '05-1234-5678',
        '055 555 5555',
        # International forms.
        '+966512345678',
        '+966 51 234 5678',
        '966512345678',
        '00966512345678',
        'whatsapp +966-51-234-5678 please',
        # Spaced out to evade a naive check.
        '0 5 1 2 3 4 5 6 7 8',
        # Unbroken run of nine or more digits.
        '123456789',
        'my number is 1234567890',
        # Email is still caught, via messaging's shared pattern.
        'reach me at seller@example.com',
    ]

    ALLOWED = [
        # The regression this pattern exists for.
        '2019 88000 km',
        '1.5 million',
        '2019 88000 km, asking 1.5 million',
        # "105" contains 0 then 5, but no mobile number follows it.
        'price 105 500 SAR',
        '10 500 000 SAR',
        '1,050,000 SAR negotiable',
        'mileage is 125000 km',
        'asking 45,000 SAR',
        'Is the 2020 model still available?',
        'VIN 1HGBH41JXMN109186',
        # Eight digits is one short of the unbroken-run threshold.
        'lot number 12345678',
    ]

    def test_contact_details_are_rejected(self):
        for body in self.REJECTED:
            with self.subTest(body=body):
                with self.assertRaises(serializers.ValidationError):
                    validate_comment_body(body)

    def test_ordinary_car_talk_is_allowed(self):
        for body in self.ALLOWED:
            with self.subTest(body=body):
                self.assertEqual(validate_comment_body(body), body.strip())

    def test_arabic_indic_digits_are_caught_too(self):
        """\\d is Unicode-aware, so Arabic-Indic numerals are not an escape."""
        with self.assertRaises(serializers.ValidationError):
            validate_comment_body('٠٥١٢٣٤٥٦٧٨')


# ---------------------------------------------------------------------------
# Likes
# ---------------------------------------------------------------------------

@override_settings(CACHES=NO_THROTTLE_CACHE)
class ListingLikeTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer = make_user('buyer@test.com', name='Buyer')
        self.seller = make_user('seller@test.com', name='Seller', role='importer')
        self.listing = make_listing(owner=self.seller)
        self.url = f'/api/listings/{self.listing.id}/like/'

    def test_like_creates_row_and_returns_count(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data['liked'])
        self.assertEqual(resp.data['like_count'], 1)
        self.assertEqual(ListingLike.objects.filter(listing=self.listing).count(), 1)

    def test_second_call_toggles_off(self):
        self.client.force_authenticate(user=self.buyer)
        self.client.post(self.url)
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['liked'])
        self.assertEqual(resp.data['like_count'], 0)
        self.assertEqual(ListingLike.objects.filter(listing=self.listing).count(), 0)

    def test_like_requires_authentication(self):
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_like_on_non_public_listing_404(self):
        pending = make_listing(owner=self.seller, status='pending')
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(f'/api/listings/{pending.id}/like/')
        self.assertEqual(resp.status_code, 404)

    def test_two_users_like_the_same_listing(self):
        other = make_user('other@test.com', name='Other')
        for user in (self.buyer, other):
            self.client.force_authenticate(user=user)
            self.client.post(self.url)
        self.assertEqual(ListingLike.objects.filter(listing=self.listing).count(), 2)


# ---------------------------------------------------------------------------
# Comments — reading
# ---------------------------------------------------------------------------

@override_settings(CACHES=NO_THROTTLE_CACHE)
class ListingCommentReadTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer = make_user('buyer@test.com', name='Buyer')
        self.seller = make_user('seller@test.com', name='Seller', role='importer')
        self.staff = make_user('staff@test.com', name='Staff', role='admin')
        self.staff.is_staff = True
        self.staff.save()
        self.listing = make_listing(owner=self.seller)
        self.url = f'/api/listings/{self.listing.id}/comments/'

    def _comment(self, user=None, body='Is the service history complete?', **extra):
        return ListingComment.objects.create(
            listing=self.listing, user=user or self.buyer, body=body, **extra
        )

    def test_anonymous_can_read(self):
        self._comment()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)

    def test_hidden_comment_is_excluded(self):
        self._comment(is_hidden=True)
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.data['results']), 0)

    def test_deleted_comment_without_replies_is_excluded(self):
        self._comment(is_deleted=True)
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.data['results']), 0)

    def test_deleted_comment_with_visible_reply_is_kept_as_tombstone(self):
        parent = self._comment(is_deleted=True)
        ListingComment.objects.create(
            listing=self.listing, user=self.seller, body='Yes, full history.', parent=parent
        )
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.data['results']), 1)
        row = resp.data['results'][0]
        self.assertEqual(row['body'], DELETED_BODY)
        self.assertEqual(len(row['replies']), 1)

    def test_replies_are_nested_oldest_first(self):
        parent = self._comment()
        first = ListingComment.objects.create(
            listing=self.listing, user=self.seller, body='First reply', parent=parent
        )
        second = ListingComment.objects.create(
            listing=self.listing, user=self.seller, body='Second reply', parent=parent
        )
        resp = self.client.get(self.url)
        replies = resp.data['results'][0]['replies']
        self.assertEqual([r['id'] for r in replies], [first.id, second.id])

    def test_replies_are_not_returned_as_top_level_rows(self):
        parent = self._comment()
        ListingComment.objects.create(
            listing=self.listing, user=self.seller, body='A reply', parent=parent
        )
        resp = self.client.get(self.url)
        self.assertEqual(len(resp.data['results']), 1)

    def test_report_count_absent_for_non_staff(self):
        self._comment()
        resp = self.client.get(self.url)
        self.assertNotIn('report_count', resp.data['results'][0])

    def test_report_count_present_for_staff(self):
        comment = self._comment()
        CommentReport.objects.create(comment=comment, reporter=self.seller, reason='spam')
        self.client.force_authenticate(user=self.staff)
        resp = self.client.get(self.url)
        self.assertEqual(resp.data['results'][0]['report_count'], 1)

    def test_comments_on_non_public_listing_404(self):
        pending = make_listing(owner=self.seller, status='pending')
        resp = self.client.get(f'/api/listings/{pending.id}/comments/')
        self.assertEqual(resp.status_code, 404)

    def test_serializer_shape(self):
        self._comment()
        resp = self.client.get(self.url)
        row = resp.data['results'][0]
        self.assertEqual(
            set(row.keys()),
            {'id', 'user', 'body', 'created_at', 'is_owner_reply', 'replies', 'can_delete'},
        )
        self.assertEqual(set(row['user'].keys()), {'id', 'name', 'avatar_url'})

    def test_comment_list_has_no_n_plus_one(self):
        """Query count must not grow with the number of comments or replies."""
        parent = self._comment()
        ListingComment.objects.create(
            listing=self.listing, user=self.seller, body='reply', parent=parent
        )
        with CaptureQueriesContext(connection) as few:
            self.assertEqual(self.client.get(self.url).status_code, 200)

        for i in range(5):
            extra = self._comment(body=f'Question number {i}')
            ListingComment.objects.create(
                listing=self.listing, user=self.seller, body=f'reply {i}', parent=extra
            )
        with CaptureQueriesContext(connection) as many:
            self.assertEqual(self.client.get(self.url).status_code, 200)

        self.assertEqual(
            len(many), len(few),
            f'N+1: {len(few)} queries for 1 thread vs {len(many)} for 6',
        )


# ---------------------------------------------------------------------------
# Comments — writing
# ---------------------------------------------------------------------------

@override_settings(CACHES=NO_THROTTLE_CACHE)
class ListingCommentCreateTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer = make_user('buyer@test.com', name='Buyer')
        self.other = make_user('other@test.com', name='Other')
        self.seller = make_user('seller@test.com', name='Seller', role='importer')
        self.listing = make_listing(owner=self.seller)
        self.url = f'/api/listings/{self.listing.id}/comments/'

    def test_create_requires_authentication(self):
        resp = self.client.post(self.url, {'body': 'Nice car'})
        self.assertEqual(resp.status_code, 401)

    def test_create_returns_201_and_read_shape(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(self.url, {'body': 'Is it still available?'})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['body'], 'Is it still available?')
        self.assertEqual(resp.data['user']['id'], self.buyer.id)
        self.assertEqual(resp.data['replies'], [])
        self.assertTrue(resp.data['can_delete'])
        self.assertFalse(resp.data['is_owner_reply'])

    def test_body_is_stripped(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(self.url, {'body': '  padded  '})
        self.assertEqual(resp.data['body'], 'padded')

    def test_blank_body_rejected(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(self.url, {'body': '   '})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('body', resp.data)

    def test_over_length_body_rejected(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(self.url, {'body': 'x' * (ListingComment.BODY_MAX_LENGTH + 1)})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('body', resp.data)

    def test_phone_number_rejected(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(self.url, {'body': 'call me on 0512345678'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn(CONTACT_ERROR, str(resp.data))

    def test_email_rejected(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(self.url, {'body': 'mail me at seller@example.com'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn(CONTACT_ERROR, str(resp.data))

    def test_ordinary_car_talk_is_accepted(self):
        """Through the endpoint, not just the validator."""
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(self.url, {'body': '2019 88000 km, asking 1.5 million'})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['body'], '2019 88000 km, asking 1.5 million')

    def test_comment_on_non_public_listing_404(self):
        pending = make_listing(owner=self.seller, status='pending')
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(f'/api/listings/{pending.id}/comments/', {'body': 'Hello'})
        self.assertEqual(resp.status_code, 404)

    # --- replies ----------------------------------------------------------

    def test_owner_can_reply(self):
        parent = ListingComment.objects.create(
            listing=self.listing, user=self.buyer, body='Any accidents?'
        )
        self.client.force_authenticate(user=self.seller)
        resp = self.client.post(self.url, {'body': 'None at all.', 'parent': parent.id})
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data['is_owner_reply'])

    def test_non_owner_reply_forbidden(self):
        parent = ListingComment.objects.create(
            listing=self.listing, user=self.buyer, body='Any accidents?'
        )
        self.client.force_authenticate(user=self.other)
        resp = self.client.post(self.url, {'body': 'I heard yes', 'parent': parent.id})
        self.assertEqual(resp.status_code, 403)

    def test_reply_to_a_reply_rejected(self):
        parent = ListingComment.objects.create(
            listing=self.listing, user=self.buyer, body='Any accidents?'
        )
        reply = ListingComment.objects.create(
            listing=self.listing, user=self.seller, body='None.', parent=parent
        )
        self.client.force_authenticate(user=self.seller)
        resp = self.client.post(self.url, {'body': 'Adding to that', 'parent': reply.id})
        self.assertEqual(resp.status_code, 400)

    def test_reply_to_parent_of_another_listing_rejected(self):
        other_listing = make_listing(owner=self.seller, title='Second car')
        parent = ListingComment.objects.create(
            listing=other_listing, user=self.buyer, body='Elsewhere'
        )
        self.client.force_authenticate(user=self.seller)
        resp = self.client.post(self.url, {'body': 'Wrong thread', 'parent': parent.id})
        self.assertEqual(resp.status_code, 400)

    def test_reply_to_removed_parent_rejected(self):
        parent = ListingComment.objects.create(
            listing=self.listing, user=self.buyer, body='Gone', is_deleted=True
        )
        self.client.force_authenticate(user=self.seller)
        resp = self.client.post(self.url, {'body': 'Answering anyway', 'parent': parent.id})
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# Comments — deletion
# ---------------------------------------------------------------------------

@override_settings(CACHES=NO_THROTTLE_CACHE)
class CommentDeleteTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer = make_user('buyer@test.com', name='Buyer')
        self.seller = make_user('seller@test.com', name='Seller', role='importer')
        self.listing = make_listing(owner=self.seller)
        self.comment = ListingComment.objects.create(
            listing=self.listing, user=self.buyer, body='My question'
        )
        self.url = f'/api/comments/{self.comment.id}/'

    def test_author_can_soft_delete(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, 204)
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.is_deleted)
        # Soft, not hard: the row survives so replies keep their context.
        self.assertTrue(ListingComment.objects.filter(pk=self.comment.pk).exists())

    def test_listing_owner_cannot_delete_someone_elses_comment(self):
        self.client.force_authenticate(user=self.seller)
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, 403)
        self.comment.refresh_from_db()
        self.assertFalse(self.comment.is_deleted)

    def test_delete_requires_authentication(self):
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_unknown_comment_404(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.delete('/api/comments/999999/')
        self.assertEqual(resp.status_code, 404)

    def test_delete_writes_an_audit_row(self):
        self.client.force_authenticate(user=self.buyer)
        self.client.delete(self.url)
        self.assertTrue(
            AuditLog.objects.filter(
                action='delete', model_name='ListingComment', object_id=self.comment.pk
            ).exists()
        )


# ---------------------------------------------------------------------------
# Comments — reporting
# ---------------------------------------------------------------------------

@override_settings(CACHES=NO_THROTTLE_CACHE)
class CommentReportTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.author = make_user('author@test.com', name='Author')
        self.seller = make_user('seller@test.com', name='Seller', role='importer')
        self.listing = make_listing(owner=self.seller)
        self.comment = ListingComment.objects.create(
            listing=self.listing, user=self.author, body='Questionable content'
        )
        self.url = f'/api/comments/{self.comment.id}/report/'

    def test_report_requires_authentication(self):
        resp = self.client.post(self.url, {'reason': 'spam'})
        self.assertEqual(resp.status_code, 401)

    def test_first_report_created(self):
        self.client.force_authenticate(user=self.seller)
        resp = self.client.post(self.url, {'reason': 'spam', 'note': 'obvious spam'})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(CommentReport.objects.filter(comment=self.comment).count(), 1)

    def test_duplicate_report_is_idempotent(self):
        self.client.force_authenticate(user=self.seller)
        self.client.post(self.url, {'reason': 'spam'})
        resp = self.client.post(self.url, {'reason': 'abuse'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(CommentReport.objects.filter(comment=self.comment).count(), 1)

    def test_cannot_report_own_comment(self):
        self.client.force_authenticate(user=self.author)
        resp = self.client.post(self.url, {'reason': 'spam'})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_reason_rejected(self):
        self.client.force_authenticate(user=self.seller)
        resp = self.client.post(self.url, {'reason': 'not-a-reason'})
        self.assertEqual(resp.status_code, 400)

    def test_auto_hide_at_threshold(self):
        reporters = [
            make_user(f'reporter{i}@test.com', name=f'Reporter {i}')
            for i in range(CommentReport.AUTO_HIDE_THRESHOLD)
        ]
        for i, reporter in enumerate(reporters, start=1):
            self.client.force_authenticate(user=reporter)
            self.client.post(self.url, {'reason': 'spam'})
            self.comment.refresh_from_db()
            expected = i >= CommentReport.AUTO_HIDE_THRESHOLD
            self.assertEqual(self.comment.is_hidden, expected, f'after {i} report(s)')

        self.assertTrue(
            AuditLog.objects.filter(
                action='status_change',
                model_name='ListingComment',
                object_id=self.comment.pk,
                new_value__source='auto',
            ).exists()
        )

    def test_auto_hidden_comment_disappears_from_the_list(self):
        for i in range(CommentReport.AUTO_HIDE_THRESHOLD):
            reporter = make_user(f'reporter{i}@test.com', name=f'Reporter {i}')
            self.client.force_authenticate(user=reporter)
            self.client.post(self.url, {'reason': 'spam'})

        self.client.force_authenticate(user=None)
        resp = self.client.get(f'/api/listings/{self.listing.id}/comments/')
        self.assertEqual(len(resp.data['results']), 0)


# ---------------------------------------------------------------------------
# Social counts on the cars endpoints
# ---------------------------------------------------------------------------

@override_settings(CACHES=NO_THROTTLE_CACHE)
class ListingSocialCountsTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer = make_user('buyer@test.com', name='Buyer')
        self.seller = make_user('seller@test.com', name='Seller', role='importer')
        self.listing = make_listing(owner=self.seller)

    def test_detail_exposes_social_fields(self):
        ListingLike.objects.create(user=self.buyer, listing=self.listing)
        ListingComment.objects.create(
            listing=self.listing, user=self.buyer, body='A question'
        )
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.get(f'/api/listings/{self.listing.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['like_count'], 1)
        self.assertEqual(resp.data['comment_count'], 1)
        self.assertTrue(resp.data['is_liked'])

    def test_is_liked_is_false_not_null_for_anonymous(self):
        ListingLike.objects.create(user=self.buyer, listing=self.listing)
        resp = self.client.get(f'/api/listings/{self.listing.id}/')
        self.assertIs(resp.data['is_liked'], False)
        self.assertEqual(resp.data['like_count'], 1)

    def test_list_exposes_social_fields(self):
        resp = self.client.get('/api/listings/')
        self.assertEqual(resp.status_code, 200)
        row = resp.data['results'][0]
        for field in ('like_count', 'comment_count', 'is_liked'):
            self.assertIn(field, row)

    def test_counts_are_not_multiplied_by_joins(self):
        """Two likes and two comments must not cross-multiply to four each."""
        other = make_user('other@test.com', name='Other')
        ListingLike.objects.create(user=self.buyer, listing=self.listing)
        ListingLike.objects.create(user=other, listing=self.listing)
        ListingComment.objects.create(listing=self.listing, user=self.buyer, body='One')
        ListingComment.objects.create(listing=self.listing, user=other, body='Two')

        resp = self.client.get('/api/listings/')
        row = resp.data['results'][0]
        self.assertEqual(row['like_count'], 2)
        self.assertEqual(row['comment_count'], 2)

    def test_hidden_and_deleted_comments_are_not_counted(self):
        ListingComment.objects.create(listing=self.listing, user=self.buyer, body='Visible')
        ListingComment.objects.create(
            listing=self.listing, user=self.buyer, body='Hidden', is_hidden=True
        )
        ListingComment.objects.create(
            listing=self.listing, user=self.buyer, body='Deleted', is_deleted=True
        )
        resp = self.client.get('/api/listings/')
        self.assertEqual(resp.data['results'][0]['comment_count'], 1)

    def test_listing_list_has_no_n_plus_one(self):
        """Query count must not grow with the number of listings."""
        ListingLike.objects.create(user=self.buyer, listing=self.listing)
        with CaptureQueriesContext(connection) as few:
            self.assertEqual(self.client.get('/api/listings/').status_code, 200)

        for i in range(5):
            extra = make_listing(owner=self.seller, title=f'Car {i}')
            ListingLike.objects.create(user=self.buyer, listing=extra)
            ListingComment.objects.create(listing=extra, user=self.buyer, body=f'Q{i}')
        with CaptureQueriesContext(connection) as many:
            resp = self.client.get('/api/listings/')
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 6)

        self.assertEqual(
            len(many), len(few),
            f'N+1: {len(few)} queries for 1 listing vs {len(many)} for 6',
        )


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------

@override_settings(CACHES={
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'social-throttle-tests',
    },
})
class ThrottleTests(TestCase):
    """
    Proves the scopes are actually wired. The rate is lowered in-place rather
    than posting 10 real comments, following assistant/tests.py.
    """

    def setUp(self):
        self.client = APIClient()
        self.buyer = make_user('buyer@test.com', name='Buyer')
        self.seller = make_user('seller@test.com', name='Seller', role='importer')
        self.listing = make_listing(owner=self.seller)
        self.url = f'/api/listings/{self.listing.id}/comments/'

        original = CommentRateThrottle.THROTTLE_RATES['social_comment']
        CommentRateThrottle.THROTTLE_RATES['social_comment'] = '2/min'

        def restore():
            CommentRateThrottle.THROTTLE_RATES['social_comment'] = original
        self.addCleanup(restore)

    def test_comment_rate_limit_returns_429(self):
        self.client.force_authenticate(user=self.buyer)
        for i in range(2):
            resp = self.client.post(self.url, {'body': f'Question {i}'})
            self.assertEqual(resp.status_code, 201)
        resp = self.client.post(self.url, {'body': 'One too many'})
        self.assertEqual(resp.status_code, 429)

    def test_reading_comments_is_not_throttled(self):
        self.client.force_authenticate(user=self.buyer)
        for _ in range(5):
            self.assertEqual(self.client.get(self.url).status_code, 200)

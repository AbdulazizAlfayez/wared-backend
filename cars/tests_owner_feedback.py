"""
The reviewer's note reaching the person who has to act on it.

`request-changes` writes to `admin_notes`, which the serializer strips for
everyone who is not an admin — so the note is invisible in the one field it
was written for. `owner_feedback` is what carries it to the owner, and these
pin that it does, on the list as well as the detail, with a timestamp, and
without leaking any of it to a buyer.
"""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing
from notifications.models import Notification

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}

NOTE = 'The second photo is of a different car. Please replace it, and add the VIN.'


def _user(email, role='importer'):
    return User.objects.create_user(email=email, password='pw12345!', name='N', role=role)


@override_settings(CACHES=DUMMY_CACHE)
class OwnerFeedbackTests(TestCase):
    def setUp(self):
        self.owner = _user('owner@feedback.test')
        self.admin = _user('admin@feedback.test', role='admin')
        self.buyer = _user('buyer@feedback.test', role='buyer')
        self.client = APIClient()
        self.listing = Listing.objects.create(
            owner=self.owner, title='2024 Audi RS6', make='Audi', model='RS6', year=2024,
            mileage=9000, city='Riyadh', price='320000', final_price_sar='320000',
            status='pending', is_active=True,
        )

    def _request_changes(self, note=NOTE):
        self.client.force_authenticate(self.admin)
        return self.client.patch(
            f'/api/listings/{self.listing.pk}/request-changes/',
            {'admin_notes': note}, format='json',
        )

    def _as_owner(self):
        self.client.force_authenticate(self.owner)
        return self.client.get(f'/api/listings/{self.listing.pk}/')

    def test_the_owner_is_shown_the_note(self):
        self._request_changes()

        response = self._as_owner()

        self.assertEqual(response.data['status'], 'changes_requested')
        self.assertEqual(response.data['owner_feedback'], NOTE)

    def test_the_note_carries_when_it_was_written(self):
        self._request_changes()

        self.assertIsNotNone(self._as_owner().data['feedback_at'])

    def test_the_list_carries_it_too(self):
        """
        My listings is where an importer actually looks — a note only on the
        detail screen is a note behind one more tap than it should be.
        """
        self._request_changes()
        self.client.force_authenticate(self.owner)

        response = self.client.get('/api/listings/my/')
        rows = response.data if isinstance(response.data, list) else response.data['results']
        row = next(r for r in rows if r['id'] == self.listing.pk)

        self.assertEqual(row['owner_feedback'], NOTE)
        self.assertIsNotNone(row['feedback_at'])

    def test_a_rejection_reaches_the_owner_the_same_way(self):
        self.client.force_authenticate(self.admin)
        self.client.patch(
            f'/api/listings/{self.listing.pk}/reject/',
            {'rejection_reason': 'Duplicate listing.'}, format='json',
        )

        response = self._as_owner()

        self.assertEqual(response.data['status'], 'rejected')
        self.assertEqual(response.data['owner_feedback'], 'Duplicate listing.')
        self.assertIsNotNone(response.data['feedback_at'])

    def test_an_approved_listing_carries_no_note(self):
        Listing.objects.filter(pk=self.listing.pk).update(
            status='approved', admin_notes='internal chatter',
        )

        response = self._as_owner()

        self.assertIsNone(response.data['owner_feedback'])
        self.assertIsNone(response.data['feedback_at'])

    def test_none_of_it_reaches_a_buyer(self):
        """
        The note is written about a car, for its owner. A buyer must not learn
        that a reviewer objected, what they said, or when.
        """
        self._request_changes()
        Listing.objects.filter(pk=self.listing.pk).update(status='approved')
        self.client.force_authenticate(self.buyer)

        response = self.client.get(f'/api/listings/{self.listing.pk}/')

        self.assertNotIn('owner_feedback', response.data)
        self.assertNotIn('feedback_at', response.data)
        self.assertNotIn('admin_notes', response.data)

    def test_an_anonymous_caller_gets_none_of_it_either(self):
        self._request_changes()
        Listing.objects.filter(pk=self.listing.pk).update(status='approved')
        self.client.force_authenticate(None)

        response = self.client.get(f'/api/listings/{self.listing.pk}/')

        self.assertNotIn('owner_feedback', response.data)
        self.assertNotIn('feedback_at', response.data)


@override_settings(CACHES=DUMMY_CACHE)
class ReviewNotificationTests(TestCase):
    def setUp(self):
        self.owner = _user('owner2@feedback.test')
        self.admin = _user('admin2@feedback.test', role='admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.listing = Listing.objects.create(
            owner=self.owner, title='2024 Audi RS6', make='Audi', model='RS6', year=2024,
            mileage=9000, city='Riyadh', price='320000', final_price_sar='320000',
            status='pending', is_active=True,
        )

    def _owner_notifications(self, kind):
        return Notification.objects.filter(
            recipient=self.owner, listing=self.listing, notification_type=kind,
        )

    def test_requesting_changes_tells_the_owner_once(self):
        self.client.patch(
            f'/api/listings/{self.listing.pk}/request-changes/',
            {'admin_notes': NOTE}, format='json',
        )

        rows = self._owner_notifications('listing_changes_requested')
        self.assertEqual(rows.count(), 1)
        notification = rows.first()
        self.assertIn(NOTE, notification.message)
        self.assertEqual(notification.metadata['listing_id'], self.listing.pk)

    def test_rejecting_tells_the_owner_once(self):
        self.client.patch(
            f'/api/listings/{self.listing.pk}/reject/',
            {'rejection_reason': 'Duplicate listing.'}, format='json',
        )

        rows = self._owner_notifications('listing_rejected')
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().metadata['listing_id'], self.listing.pk)

    def test_asking_twice_does_not_notify_twice(self):
        """
        `_notify_listing_status_change` returns early when the status has not
        moved, so a reviewer correcting their own note does not re-alert.
        """
        for _ in range(2):
            self.client.patch(
                f'/api/listings/{self.listing.pk}/request-changes/',
                {'admin_notes': NOTE}, format='json',
            )

        self.assertEqual(self._owner_notifications('listing_changes_requested').count(), 1)

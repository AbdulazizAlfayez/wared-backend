"""
Listing approval workflow tests.
Covers: create → pending, public exclusion, request_changes, approve, reject,
        resubmit on owner edit, and notification creation.
"""
from unittest.mock import patch

from django.test import override_settings, TestCase
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing


DUMMY_CACHE = {
    'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}
}

LISTING_DEFAULTS = dict(
    title='Test Car', make='Toyota', model='Camry', year=2024,
    price=50000, mileage=10000, city='Riyadh',
)


def _make_user(email, role='user', name='Test User'):
    return User.objects.create_user(email=email, password='pw12345!', name=name, role=role)


def _make_listing(owner, **overrides):
    data = {**LISTING_DEFAULTS, 'owner': owner}
    data.update(overrides)
    return Listing.objects.create(**data)


@override_settings(CACHES=DUMMY_CACHE)
class ListingApprovalWorkflowTests(TestCase):
    """End-to-end tests for the listing approval workflow."""

    def setUp(self):
        self.admin = _make_user('admin@test.com', role='admin', name='Admin')
        # Only importers can list cars on WARED — the listing owner in these
        # workflow tests must therefore be an importer.
        self.owner = _make_user('owner@test.com', role='importer', name='Owner')
        self.client = APIClient()

    # ------------------------------------------------------------------
    # 1. Listing created via API → status is 'pending'
    # ------------------------------------------------------------------
    def test_create_listing_status_pending(self):
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post('/api/listings/', LISTING_DEFAULTS, format='json')
        self.assertIn(resp.status_code, (200, 201), resp.data)
        self.assertEqual(resp.data['status'], 'pending')

    # ------------------------------------------------------------------
    # 2. Public list excludes non-approved listings
    # ------------------------------------------------------------------
    def test_public_list_excludes_pending(self):
        _make_listing(self.owner, status='pending')
        approved = _make_listing(self.owner, status='approved', title='Visible')
        resp = self.client.get('/api/listings/')
        self.assertEqual(resp.status_code, 200)
        ids = [item['id'] for item in resp.data.get('results', resp.data)]
        self.assertIn(approved.pk, ids)

    # ------------------------------------------------------------------
    # 3. Admin request_changes → status changes_requested + admin_notes
    # ------------------------------------------------------------------
    @patch('cars.views._send_listing_email')
    @patch('notifications.utils.notify')
    def test_admin_request_changes(self, mock_notify, mock_email):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f'/api/listings/{listing.pk}/request-changes/',
            {'admin_notes': 'Please add better photos'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'changes_requested')
        self.assertEqual(listing.admin_notes, 'Please add better photos')

    # ------------------------------------------------------------------
    # 4. Admin request_changes without note → 400
    # ------------------------------------------------------------------
    def test_request_changes_without_note_400(self):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f'/api/listings/{listing.pk}/request-changes/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # 5. Admin approve → status approved
    # ------------------------------------------------------------------
    @patch('cars.views._send_listing_email')
    @patch('notifications.utils.notify')
    def test_admin_approve(self, mock_notify, mock_email):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(f'/api/listings/{listing.pk}/approve/', format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'approved')

    # ------------------------------------------------------------------
    # 6. Admin reject without reason → 400
    # ------------------------------------------------------------------
    def test_reject_without_reason_400(self):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(
            f'/api/listings/{listing.pk}/reject/',
            {},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # 7. Owner edits changes_requested listing → status returns to pending
    # ------------------------------------------------------------------
    @patch('cars.views._send_listing_email')
    @patch('notifications.utils.notify')
    def test_owner_edit_resubmits_changes_requested(self, mock_notify, mock_email):
        listing = _make_listing(self.owner, status='changes_requested')
        self.client.force_authenticate(user=self.owner)
        resp = self.client.patch(
            f'/api/listings/{listing.pk}/',
            {'title': 'Updated Title'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'pending')

    # ------------------------------------------------------------------
    # 8. Notification created on request_changes
    # ------------------------------------------------------------------
    @patch('cars.views._send_listing_email')
    def test_notification_on_request_changes(self, mock_email):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.admin)
        with patch('notifications.utils.notify') as mock_notify:
            self.client.patch(
                f'/api/listings/{listing.pk}/request-changes/',
                {'admin_notes': 'Fix the price'},
                format='json',
            )
            self.assertTrue(mock_notify.called)

    # ------------------------------------------------------------------
    # 9. SECURITY: importer cannot self-approve via PATCH
    # ------------------------------------------------------------------
    def test_importer_cannot_self_approve(self):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.owner)
        resp = self.client.patch(
            f'/api/listings/{listing.pk}/',
            {'status': 'approved'},
            format='json',
        )
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'pending',
                         "Importer must NOT be able to set status=approved")

    # ------------------------------------------------------------------
    # 10. SECURITY: create with status=approved is forced to pending
    # ------------------------------------------------------------------
    def test_create_with_injected_approved_status(self):
        self.client.force_authenticate(user=self.owner)
        data = {**LISTING_DEFAULTS, 'status': 'approved'}
        resp = self.client.post('/api/listings/', data, format='json')
        self.assertIn(resp.status_code, (200, 201), resp.data)
        self.assertEqual(resp.data['status'], 'pending',
                         "Client-sent status=approved must be overridden to pending")

    # ------------------------------------------------------------------
    # 11. Public detail returns 404 for pending listing
    # ------------------------------------------------------------------
    def test_public_detail_excludes_pending(self):
        listing = _make_listing(self.owner, status='pending', import_status='available')
        self.client.logout()
        resp = self.client.get(f'/api/listings/{listing.pk}/')
        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # 12. Admin approve → listing becomes publicly visible
    # ------------------------------------------------------------------
    @patch('cars.views._send_listing_email')
    @patch('notifications.utils.notify')
    def test_admin_approve_makes_public(self, mock_notify, mock_email):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.admin)
        self.client.patch(f'/api/listings/{listing.pk}/approve/')
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'approved')
        # Now check public visibility
        self.client.logout()
        resp = self.client.get(f'/api/listings/{listing.pk}/')
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # 13. SECURITY: importer bulk-approve is rejected
    # ------------------------------------------------------------------
    def test_importer_bulk_approve_rejected(self):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post('/api/listings/bulk/status/', {
            'listing_ids': [listing.pk],
            'status': 'approved',
        }, format='json')
        self.assertEqual(resp.status_code, 403)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'pending')

    # ------------------------------------------------------------------
    # 14. Importer can NOT mark a listing as sold manually — cars become
    #     Sold automatically when WARED confirms full payment. Admin can.
    # ------------------------------------------------------------------
    def test_importer_cannot_mark_sold_manually(self):
        listing = _make_listing(self.owner, status='approved')
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post('/api/listings/bulk/status/', {
            'listing_ids': [listing.pk],
            'status': 'sold',
        }, format='json')
        self.assertEqual(resp.status_code, 403)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'approved')

    def test_admin_can_mark_sold(self):
        listing = _make_listing(self.owner, status='approved')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post('/api/listings/bulk/status/', {
            'listing_ids': [listing.pk],
            'status': 'sold',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'sold')

    # ------------------------------------------------------------------
    # 15. Importer cannot mark pending listing as sold
    # ------------------------------------------------------------------
    def test_importer_cannot_mark_pending_as_sold(self):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.owner)
        resp = self.client.post('/api/listings/bulk/status/', {
            'listing_ids': [listing.pk],
            'status': 'sold',
        }, format='json')
        listing.refresh_from_db()
        self.assertNotEqual(listing.status, 'sold')

    # ------------------------------------------------------------------
    # 16. Importer cannot update import_status on pending listing
    # ------------------------------------------------------------------
    def test_importer_cannot_change_import_status_when_pending(self):
        listing = _make_listing(self.owner, status='pending', import_status='sourcing')
        self.client.force_authenticate(user=self.owner)
        resp = self.client.patch(
            f'/api/listings/{listing.pk}/',
            {'import_status': 'available'},
            format='json',
        )
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # 17. Admin bulk-approve still works
    # ------------------------------------------------------------------
    @patch('cars.views._send_listing_email')
    @patch('notifications.utils.notify')
    def test_admin_bulk_approve_works(self, mock_notify, mock_email):
        listing = _make_listing(self.owner, status='pending')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post('/api/listings/bulk/status/', {
            'listing_ids': [listing.pk],
            'status': 'approved',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        listing.refresh_from_db()
        self.assertEqual(listing.status, 'approved')

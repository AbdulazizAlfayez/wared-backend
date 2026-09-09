"""
Tests for the importers app — Phase D.
Run: python manage.py test importers --verbosity=2
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from .models import CRVerificationHistory, ImporterProfile

User = get_user_model()


def make_user(email, role='user', name='Test User'):
    user = User.objects.create_user(email=email, password='testpass123', name=name, role=role)
    return user


def make_importer_user(email='importer@test.com', name='Importer User'):
    user = make_user(email=email, role='importer', name=name)
    # Signal should auto-create profile; ensure it exists
    ImporterProfile.objects.get_or_create(
        user=user,
        defaults={'business_name': name},
    )
    return user


class TestAutoCreateProfileOnRoleChange(TestCase):
    """Test 1 — signal auto-creates ImporterProfile when role becomes importer."""

    def test_auto_create_profile_on_role_change(self):
        user = make_user(email='newimporter@test.com', role='user', name='New Importer')
        # Should not have a profile yet
        self.assertFalse(ImporterProfile.objects.filter(user=user).exists())

        # Change role to importer and save — signal fires
        user.role = 'importer'
        user.save()

        self.assertTrue(ImporterProfile.objects.filter(user=user).exists())


class TestPublicBrowseOnlyShowsVerified(TestCase):
    """Test 2 — GET /api/importers/ only returns is_verified=True profiles."""

    def setUp(self):
        self.client = APIClient()
        self.importer1 = make_importer_user('verified@test.com', 'Verified Co')
        self.importer2 = make_importer_user('unverified@test.com', 'Unverified Co')

        ImporterProfile.objects.filter(user=self.importer1).update(is_verified=True)
        ImporterProfile.objects.filter(user=self.importer2).update(is_verified=False)

    def test_public_browse_only_shows_verified(self):
        response = self.client.get('/api/importers/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        # Handle paginated response
        results = data.get('results', data) if isinstance(data, dict) else data
        business_names = [r['business_name'] for r in results]
        self.assertIn('Verified Co', business_names)
        self.assertNotIn('Unverified Co', business_names)


class TestImporterDashboardStats(TestCase):
    """Test 3 — GET /api/dashboard/importer/ returns correct stats."""

    def setUp(self):
        self.client = APIClient()
        self.importer = make_importer_user('dash_importer@test.com', 'Dash Importer')
        self.client.force_authenticate(user=self.importer)

    def test_importer_dashboard_stats(self):
        response = self.client.get('/api/dashboard/importer/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertIn('total_cars_listed', data)
        self.assertIn('active_orders_count', data)
        self.assertIn('total_revenue', data)
        self.assertIn('cars_by_import_status', data)
        self.assertIn('average_rating', data)
        self.assertIn('pending_messages', data)
        # No listings yet, so total should be 0
        self.assertEqual(data['total_cars_listed'], 0)
        self.assertEqual(data['active_orders_count'], 0)


class TestBuyerCannotReviewWithoutCompletedOrder(TestCase):
    """Test 4 — Buyer with no completed orders gets 403 when posting a review."""

    def setUp(self):
        self.client = APIClient()
        self.importer = make_importer_user('rev_importer@test.com', 'Rev Importer')
        ImporterProfile.objects.filter(user=self.importer).update(is_verified=True)
        self.profile = ImporterProfile.objects.get(user=self.importer)

        self.buyer = make_user('buyer_rev@test.com', role='user', name='Buyer')
        self.client.force_authenticate(user=self.buyer)

    def test_buyer_cannot_review_without_completed_order(self):
        response = self.client.post(
            f'/api/importers/{self.profile.pk}/reviews/',
            {'rating': 5, 'title': 'Great!', 'comment': 'Wonderful service.'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TestImporterCanUpdateOwnProfile(TestCase):
    """Test 5 — PATCH /api/importers/me/ updates business_name."""

    def setUp(self):
        self.client = APIClient()
        self.importer = make_importer_user('patch_importer@test.com', 'Old Name')
        self.client.force_authenticate(user=self.importer)

    def test_importer_can_update_own_profile(self):
        response = self.client.patch(
            '/api/importers/me/',
            {'business_name': 'New Business Name'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['business_name'], 'New Business Name')

        # Verify DB updated
        profile = ImporterProfile.objects.get(user=self.importer)
        self.assertEqual(profile.business_name, 'New Business Name')


# ============================================================================
# Phase B — CR Lifecycle Tests
# ============================================================================

from datetime import date, timedelta
from django.test import override_settings
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile

LOCAL_STORAGE = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}}


class CRStatusViewTest(TestCase):
    """Test CR status endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.importer = make_importer_user('cr_status@test.com', 'CR Status Co')
        self.client.force_authenticate(self.importer)

    def test_cr_status_returns_data(self):
        resp = self.client.get('/api/importer/cr/status/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['cr_verification_status'], 'pending_initial')
        self.assertIsNone(resp.data['cr_expiry_date'])
        self.assertTrue(resp.data['can_submit_renewal'])


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.FileSystemStorage')
class CRSubmitRenewalTest(TestCase):
    """Test CR renewal submission."""

    def setUp(self):
        self.client = APIClient()
        self.importer = make_importer_user('cr_renew@test.com', 'CR Renew Co')
        self.client.force_authenticate(self.importer)

    def test_submit_renewal(self):
        future_date = (date.today() + timedelta(days=365)).isoformat()
        doc = SimpleUploadedFile('cr.pdf', b'fake-pdf-content', content_type='application/pdf')
        resp = self.client.post('/api/importer/cr/submit-renewal/', {
            'cr_document': doc,
            'cr_expiry_date': future_date,
        }, format='multipart')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'pending_review')

        profile = ImporterProfile.objects.get(user=self.importer)
        self.assertEqual(profile.cr_verification_status, 'renewal_under_review')
        self.assertTrue(CRVerificationHistory.objects.filter(importer_profile=profile, action='renewal_submitted').exists())

    def test_submit_with_past_date_fails(self):
        past_date = (date.today() - timedelta(days=10)).isoformat()
        doc = SimpleUploadedFile('cr.pdf', b'fake', content_type='application/pdf')
        resp = self.client.post('/api/importer/cr/submit-renewal/', {
            'cr_document': doc,
            'cr_expiry_date': past_date,
        }, format='multipart')
        self.assertEqual(resp.status_code, 400)

    def test_submit_without_document_fails(self):
        future_date = (date.today() + timedelta(days=365)).isoformat()
        resp = self.client.post('/api/importer/cr/submit-renewal/', {
            'cr_expiry_date': future_date,
        }, format='multipart')
        self.assertEqual(resp.status_code, 400)


class CRAutoSuspensionTest(TestCase):
    """Test auto-suspension when CR expires."""

    def setUp(self):
        self.importer = make_importer_user('cr_suspend@test.com', 'Suspend Co')
        self.profile = ImporterProfile.objects.get(user=self.importer)

    def test_auto_suspend_changes_status(self):
        from .tasks import auto_suspend_importer

        self.profile.cr_verification_status = 'expiring_soon'
        self.profile.cr_expiry_date = date.today() - timedelta(days=1)
        self.profile.save()

        auto_suspend_importer(self.profile.id)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.cr_verification_status, 'suspended')
        self.assertIsNotNone(self.profile.cr_suspended_at)
        self.assertTrue(CRVerificationHistory.objects.filter(
            importer_profile=self.profile, action='auto_suspended'
        ).exists())

    def test_already_suspended_skipped(self):
        from .tasks import auto_suspend_importer

        self.profile.cr_verification_status = 'suspended'
        self.profile.save()

        result = auto_suspend_importer(self.profile.id)
        self.assertIn('Already suspended', result)


class CRListingSuspensionTest(TestCase):
    """Test that listings get archived when importer is suspended."""

    def setUp(self):
        self.importer = make_importer_user('listing_suspend@test.com', 'Listing Suspend Co')
        self.profile = ImporterProfile.objects.get(user=self.importer)
        # Create a listing
        from cars.models import Listing
        self.listing = Listing.objects.create(
            owner=self.importer, make='Toyota', model='Camry', year=2024,
            price=100000, mileage=10000, status='approved', is_active=True,
        )

    def test_listings_archived_on_suspension(self):
        from .tasks import auto_suspend_importer

        self.profile.cr_verification_status = 'verified'
        self.profile.cr_expiry_date = date.today() - timedelta(days=1)
        self.profile.save()

        auto_suspend_importer(self.profile.id)

        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, 'archived')
        self.assertFalse(self.listing.is_active)


class CRCheckExpirationTaskTest(TestCase):
    """Test the daily CR expiration check task."""

    def test_60_day_reminder(self):
        from .tasks import check_cr_expirations

        importer = make_importer_user('cr60@test.com', 'CR 60 Co')
        profile = ImporterProfile.objects.get(user=importer)
        profile.cr_verification_status = 'verified'
        profile.cr_expiry_date = date.today() + timedelta(days=60)
        profile.save()

        result = check_cr_expirations()
        self.assertIn("'reminded_60': 1", result)
        self.assertTrue(CRVerificationHistory.objects.filter(
            importer_profile=profile, action='reminder_sent_60'
        ).exists())

    def test_30_day_status_change(self):
        from .tasks import check_cr_expirations

        importer = make_importer_user('cr30@test.com', 'CR 30 Co')
        profile = ImporterProfile.objects.get(user=importer)
        profile.cr_verification_status = 'verified'
        profile.cr_expiry_date = date.today() + timedelta(days=30)
        profile.save()

        check_cr_expirations()

        profile.refresh_from_db()
        self.assertEqual(profile.cr_verification_status, 'expiring_soon')

    def test_auto_suspend_past_expiry(self):
        from .tasks import check_cr_expirations

        importer = make_importer_user('cr_past@test.com', 'CR Past Co')
        profile = ImporterProfile.objects.get(user=importer)
        profile.cr_verification_status = 'verified'
        profile.cr_expiry_date = date.today() - timedelta(days=1)
        profile.save()

        check_cr_expirations()

        profile.refresh_from_db()
        self.assertEqual(profile.cr_verification_status, 'suspended')


class CRAdminApproveTest(TestCase):
    """Test admin CR approval flow."""

    def setUp(self):
        self.admin = make_user('admin@test.com', role='admin', name='Admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.importer = make_importer_user('approve@test.com', 'Approve Co')
        self.profile = ImporterProfile.objects.get(user=self.importer)
        self.profile.cr_verification_status = 'renewal_under_review'
        self.profile.cr_expiry_date = date.today() + timedelta(days=365)
        self.profile.save()

    def test_admin_approve(self):
        resp = self.client.post(f'/api/admin/cr-reviews/{self.profile.id}/approve/', {
            'notes': 'Looks good',
        })
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.cr_verification_status, 'verified')
        self.assertTrue(CRVerificationHistory.objects.filter(
            importer_profile=self.profile, action='renewal_approved'
        ).exists())

    def test_admin_approve_reactivates_suspended(self):
        self.profile.cr_verification_status = 'suspended'
        self.profile.save()

        from cars.models import Listing
        listing = Listing.objects.create(
            owner=self.importer, make='BMW', model='X5', year=2024,
            price=200000, mileage=5000, status='archived', is_active=False,
        )

        resp = self.client.post(f'/api/admin/cr-reviews/{self.profile.id}/approve/', {
            'new_expiry_date': (date.today() + timedelta(days=365)).isoformat(),
        })
        self.assertEqual(resp.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.cr_verification_status, 'verified')
        self.assertIsNotNone(self.profile.cr_reactivated_at)

        listing.refresh_from_db()
        self.assertEqual(listing.status, 'approved')
        self.assertTrue(listing.is_active)


class CRAdminRejectTest(TestCase):
    """Test admin CR rejection flow."""

    def setUp(self):
        self.admin = make_user('admin_rej@test.com', role='admin', name='Admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.importer = make_importer_user('reject@test.com', 'Reject Co')
        self.profile = ImporterProfile.objects.get(user=self.importer)
        self.profile.cr_verification_status = 'renewal_under_review'
        self.profile.cr_expiry_date = date.today() + timedelta(days=365)
        self.profile.save()

    def test_admin_reject(self):
        resp = self.client.post(f'/api/admin/cr-reviews/{self.profile.id}/reject/', {
            'reason': 'Document unclear',
        })
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.cr_verification_status, 'verified')
        self.assertTrue(CRVerificationHistory.objects.filter(
            importer_profile=self.profile, action='renewal_rejected'
        ).exists())


class CRAdminDashboardTest(TestCase):
    """Test admin CR dashboard endpoint."""

    def setUp(self):
        self.admin = make_user('admin_dash@test.com', role='admin', name='Admin')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_dashboard_returns_stats(self):
        resp = self.client.get('/api/admin/cr-dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('importers_verified', resp.data)
        self.assertIn('importers_expired_suspended', resp.data)
        self.assertIn('recent_actions', resp.data)

    def test_non_admin_blocked(self):
        user = make_user('nonadmin@test.com')
        self.client.force_authenticate(user)
        resp = self.client.get('/api/admin/cr-dashboard/')
        self.assertEqual(resp.status_code, 403)


@override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.FileSystemStorage')
class CRHistoryLoggingTest(TestCase):
    """Test that all actions create history entries."""

    def test_renewal_creates_history(self):
        importer = make_importer_user('history@test.com', 'History Co')
        profile = ImporterProfile.objects.get(user=importer)
        client = APIClient()
        client.force_authenticate(importer)

        future_date = (date.today() + timedelta(days=365)).isoformat()
        doc = SimpleUploadedFile('cr.pdf', b'fake', content_type='application/pdf')
        client.post('/api/importer/cr/submit-renewal/', {
            'cr_document': doc, 'cr_expiry_date': future_date,
        }, format='multipart')

        self.assertEqual(CRVerificationHistory.objects.filter(importer_profile=profile).count(), 1)
        entry = CRVerificationHistory.objects.first()
        self.assertEqual(entry.action, 'renewal_submitted')
        self.assertEqual(entry.performed_by, importer)

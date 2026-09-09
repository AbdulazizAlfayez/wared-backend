"""
Phase 5.5 — Compliance & Privacy Tests.
Tests for account deletion, data export, legal documents, and cookie consent.
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import AccountDeletionRequest, DataExportRequest
from legal_documents.models import CookieConsent, LegalDocument, UserLegalAcceptance

User = get_user_model()


def make_user(email='test@example.com', password='testpass123', **kwargs):
    return User.objects.create_user(email=email, password=password, name='Test User', **kwargs)


def make_legal_docs():
    """Create active ToS + Privacy Policy."""
    tos = LegalDocument.objects.create(
        document_type='terms_of_service', version='1.0.0',
        content_en='ToS EN', content_ar='ToS AR',
        effective_date=date.today(), is_active=True,
    )
    pp = LegalDocument.objects.create(
        document_type='privacy_policy', version='1.0.0',
        content_en='PP EN', content_ar='PP AR',
        effective_date=date.today(), is_active=True,
    )
    return tos, pp


# ============================================================================
# Legal Document Tests
# ============================================================================

class LegalDocumentModelTest(TestCase):
    def test_only_one_active_per_type(self):
        """Activating a new version deactivates the old one."""
        doc1 = LegalDocument.objects.create(
            document_type='terms_of_service', version='1.0.0',
            content_en='v1', content_ar='v1',
            effective_date=date.today(), is_active=True,
        )
        doc2 = LegalDocument.objects.create(
            document_type='terms_of_service', version='2.0.0',
            content_en='v2', content_ar='v2',
            effective_date=date.today(), is_active=True,
        )
        doc1.refresh_from_db()
        self.assertFalse(doc1.is_active)
        self.assertTrue(doc2.is_active)

    def test_different_types_can_both_be_active(self):
        tos, pp = make_legal_docs()
        self.assertTrue(tos.is_active)
        self.assertTrue(pp.is_active)


class LegalDocumentAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tos, self.pp = make_legal_docs()

    def test_get_active_document(self):
        resp = self.client.get('/api/legal/documents/terms_of_service/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['version'], '1.0.0')

    def test_invalid_type_returns_400(self):
        resp = self.client.get('/api/legal/documents/invalid_type/')
        self.assertEqual(resp.status_code, 400)


class LegalAcceptanceTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user()
        self.tos, self.pp = make_legal_docs()

    def test_accept_document(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post('/api/legal/accept/', {
            'document_type': 'terms_of_service',
            'version': '1.0.0',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(UserLegalAcceptance.objects.filter(user=self.user, document=self.tos).exists())

    def test_acceptance_status_shows_unaccepted(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/legal/acceptance-status/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['needs_reacceptance'])

    def test_acceptance_status_after_accepting_all(self):
        self.client.force_authenticate(self.user)
        UserLegalAcceptance.objects.create(user=self.user, document=self.tos)
        UserLegalAcceptance.objects.create(user=self.user, document=self.pp)
        resp = self.client.get('/api/legal/acceptance-status/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['needs_reacceptance'])

    def test_new_version_forces_reacceptance(self):
        """When a new version is activated, user needs to re-accept."""
        self.client.force_authenticate(self.user)
        UserLegalAcceptance.objects.create(user=self.user, document=self.tos)
        UserLegalAcceptance.objects.create(user=self.user, document=self.pp)

        # Create new version — deactivates old one
        LegalDocument.objects.create(
            document_type='terms_of_service', version='2.0.0',
            content_en='v2', content_ar='v2',
            effective_date=date.today(), is_active=True,
        )
        resp = self.client.get('/api/legal/acceptance-status/')
        self.assertTrue(resp.data['needs_reacceptance'])


# ============================================================================
# Cookie Consent Tests
# ============================================================================

class CookieConsentTest(TestCase):
    def test_authenticated_user_consent(self):
        user = make_user()
        client = APIClient()
        client.force_authenticate(user)
        resp = client.post('/api/legal/cookie-consent/', {
            'analytics': True, 'marketing': False,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data['analytics'])
        self.assertFalse(resp.data['marketing'])

    def test_get_consent_returns_latest(self):
        user = make_user()
        client = APIClient()
        client.force_authenticate(user)
        client.post('/api/legal/cookie-consent/', {'analytics': True, 'marketing': False})
        resp = client.get('/api/legal/cookie-consent/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['analytics'])


# ============================================================================
# Account Deletion Tests
# ============================================================================

class AccountDeletionRequestTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_request_deletion(self):
        resp = self.client.post('/api/account/deletion/request/', {'reason': 'Testing'})
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertTrue(self.user.is_deletion_pending)
        self.assertTrue(AccountDeletionRequest.objects.filter(user=self.user, status='pending').exists())

    def test_duplicate_request_fails(self):
        self.client.post('/api/account/deletion/request/')
        # Re-authenticate since user is now inactive
        self.client.force_authenticate(self.user)
        resp = self.client.post('/api/account/deletion/request/')
        self.assertEqual(resp.status_code, 400)

    def test_cancel_deletion(self):
        self.client.post('/api/account/deletion/request/')
        # Cancel via email+password (since user is deactivated)
        cancel_client = APIClient()
        resp = cancel_client.post('/api/account/deletion/cancel/', {
            'email': 'test@example.com',
            'password': 'testpass123',
        })
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertFalse(self.user.is_deletion_pending)

    def test_cancel_with_wrong_password_fails(self):
        self.client.post('/api/account/deletion/request/')
        cancel_client = APIClient()
        resp = cancel_client.post('/api/account/deletion/cancel/', {
            'email': 'test@example.com',
            'password': 'wrongpassword',
        })
        self.assertEqual(resp.status_code, 401)

    def test_cancel_after_grace_period_fails(self):
        self.client.post('/api/account/deletion/request/')
        req = AccountDeletionRequest.objects.get(user=self.user)
        req.scheduled_deletion_date = timezone.now() - timedelta(days=1)
        req.save()
        cancel_client = APIClient()
        resp = cancel_client.post('/api/account/deletion/cancel/', {
            'email': 'test@example.com',
            'password': 'testpass123',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('expired', resp.data['error'].lower())


class AccountDeletionTaskTest(TestCase):
    def test_process_deletions_anonymizes_user(self):
        from accounts.tasks import process_account_deletions

        user = make_user(email='delete-me@example.com')
        AccountDeletionRequest.objects.create(
            user=user,
            scheduled_deletion_date=timezone.now() - timedelta(days=1),
            status='pending',
        )
        user.is_active = False
        user.is_deletion_pending = True
        user.save()

        result = process_account_deletions()
        self.assertIn('1', result)

        user.refresh_from_db()
        self.assertIn('deleted_', user.email)
        self.assertEqual(user.name, 'Deleted User')
        self.assertEqual(user.phone, '')

        req = AccountDeletionRequest.objects.get(user=user)
        self.assertEqual(req.status, 'completed')

    def test_does_not_process_future_deletions(self):
        from accounts.tasks import process_account_deletions

        user = make_user(email='future@example.com')
        AccountDeletionRequest.objects.create(
            user=user,
            scheduled_deletion_date=timezone.now() + timedelta(days=10),
            status='pending',
        )

        result = process_account_deletions()
        self.assertIn('0', result)
        user.refresh_from_db()
        self.assertEqual(user.email, 'future@example.com')


# ============================================================================
# Data Export Tests
# ============================================================================

class DataExportTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_request_export(self):
        resp = self.client.post('/api/account/export/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'pending')
        self.assertTrue(DataExportRequest.objects.filter(user=self.user).exists())

    def test_rate_limit_24h(self):
        self.client.post('/api/account/export/')
        resp = self.client.post('/api/account/export/')
        self.assertEqual(resp.status_code, 429)

    def test_export_status(self):
        self.client.post('/api/account/export/')
        resp = self.client.get('/api/account/export/status/')
        self.assertEqual(resp.status_code, 200)
        # With CELERY_TASK_ALWAYS_EAGER the export completes immediately,
        # so accept either 'pending' (queued) or 'ready' (already generated)
        self.assertIn(resp.data['status'], ('pending', 'processing', 'ready'))

    def test_generate_export_task(self):
        from accounts.tasks import generate_user_data_export

        export_req = DataExportRequest.objects.create(user=self.user)
        result = generate_user_data_export(export_req.id)
        export_req.refresh_from_db()
        self.assertEqual(export_req.status, 'ready')
        self.assertIsNotNone(export_req.download_url)
        self.assertGreater(export_req.file_size_bytes, 0)

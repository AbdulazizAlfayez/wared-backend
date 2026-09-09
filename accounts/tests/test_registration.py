"""
Tests for the registration endpoint — particularly the unverified re-registration flow.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

User = get_user_model()


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class RegistrationTest(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_register_with_existing_unverified_email_resends_otp(self):
        """Re-registering with an unverified email should resend OTP, not error."""
        # First registration
        resp1 = self.client.post('/api/auth/register/', {
            'email': 'unverified@test.com',
            'name': 'First Try',
            'password': 'StrongPass123!',
            'password2': 'StrongPass123!',
        })
        self.assertEqual(resp1.status_code, 201)

        # Second registration with same email (user never verified)
        resp2 = self.client.post('/api/auth/register/', {
            'email': 'unverified@test.com',
            'name': 'Second Try',
            'password': 'NewPass456!',
            'password2': 'NewPass456!',
        })
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.data['status'], 'otp_resent')
        self.assertTrue(resp2.data['requires_verification'])
        self.assertIn('tokens', resp2.data)

    def test_register_with_existing_verified_email_returns_error(self):
        """Registering with a verified email should return 400."""
        user = User.objects.create_user(
            email='verified@test.com', password='Pass123!', name='Verified'
        )
        user.is_email_verified = True
        user.save()

        resp = self.client.post('/api/auth/register/', {
            'email': 'verified@test.com',
            'name': 'Attacker',
            'password': 'HackPass123!',
            'password2': 'HackPass123!',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('email', resp.data)

    def test_register_with_unverified_email_updates_password(self):
        """Re-registering with unverified email should update the password."""
        # Create unverified user
        user = User.objects.create_user(
            email='repass@test.com', password='OldPass123!', name='Old Name'
        )

        # Re-register with new password
        resp = self.client.post('/api/auth/register/', {
            'email': 'repass@test.com',
            'name': 'New Name',
            'password': 'BrandNew789!',
            'password2': 'BrandNew789!',
        })
        self.assertEqual(resp.status_code, 200)

        # Verify password was updated
        user.refresh_from_db()
        self.assertTrue(user.check_password('BrandNew789!'))
        self.assertEqual(user.name, 'New Name')


# ============================================================================
# /api/me/ has_accepted_current_terms Tests
# ============================================================================

from datetime import date
from legal_documents.models import LegalDocument, UserLegalAcceptance


class MeEndpointTermsTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email='me@test.com', password='pass123', name='Me')

    def test_me_includes_has_accepted_current_terms(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('has_accepted_current_terms', resp.data)

    def test_true_when_both_accepted(self):
        tos = LegalDocument.objects.create(document_type='terms_of_service', version='1.0', content_en='t', content_ar='t', effective_date=date.today(), is_active=True)
        pp = LegalDocument.objects.create(document_type='privacy_policy', version='1.0', content_en='p', content_ar='p', effective_date=date.today(), is_active=True)
        UserLegalAcceptance.objects.create(user=self.user, document=tos)
        UserLegalAcceptance.objects.create(user=self.user, document=pp)
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/me/')
        self.assertTrue(resp.data['has_accepted_current_terms'])

    def test_false_when_only_tos_accepted(self):
        tos = LegalDocument.objects.create(document_type='terms_of_service', version='1.0', content_en='t', content_ar='t', effective_date=date.today(), is_active=True)
        LegalDocument.objects.create(document_type='privacy_policy', version='1.0', content_en='p', content_ar='p', effective_date=date.today(), is_active=True)
        UserLegalAcceptance.objects.create(user=self.user, document=tos)
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/me/')
        self.assertFalse(resp.data['has_accepted_current_terms'])

    def test_false_after_new_version_published(self):
        tos_v1 = LegalDocument.objects.create(document_type='terms_of_service', version='1.0', content_en='t', content_ar='t', effective_date=date.today(), is_active=True)
        UserLegalAcceptance.objects.create(user=self.user, document=tos_v1)
        # Publish v2 — deactivates v1 via save() override
        tos_v2 = LegalDocument.objects.create(document_type='terms_of_service', version='2.0', content_en='t2', content_ar='t2', effective_date=date.today(), is_active=True)
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/me/')
        self.assertFalse(resp.data['has_accepted_current_terms'])
        # Accept v2
        UserLegalAcceptance.objects.create(user=self.user, document=tos_v2)
        resp2 = self.client.get('/api/me/')
        self.assertTrue(resp2.data['has_accepted_current_terms'])


# ============================================================================
# User is_deleted / deleted_at / ActiveUserManager Tests
# ============================================================================

from accounts.models import AccountDeletionRequest


class UserIsDeletedTest(TestCase):
    def test_default_is_deleted_false(self):
        u = User.objects.create_user(email='new@test.com', password='p', name='N')
        self.assertFalse(u.is_deleted)
        self.assertIsNone(u.deleted_at)

    def test_deletion_task_sets_is_deleted(self):
        from accounts.tasks import process_account_deletions
        from django.utils import timezone
        from datetime import timedelta
        u = User.objects.create_user(email='del@test.com', password='p', name='Del')
        AccountDeletionRequest.objects.create(user=u, scheduled_deletion_date=timezone.now() - timedelta(days=1), status='pending')
        u.is_active = False
        u.is_deletion_pending = True
        u.save()
        process_account_deletions()
        u.refresh_from_db()
        self.assertTrue(u.is_deleted)
        self.assertIsNotNone(u.deleted_at)
        self.assertIn('deleted_', u.email)

    def test_active_users_excludes_deleted(self):
        u1 = User.objects.create_user(email='a1@test.com', password='p', name='A1')
        u2 = User.objects.create_user(email='a2@test.com', password='p', name='A2')
        u2.is_deleted = True
        u2.save()
        self.assertEqual(User.objects.filter(email__in=['a1@test.com', 'a2@test.com']).count(), 2)
        self.assertEqual(User.active_users.filter(email__in=['a1@test.com', 'a2@test.com']).count(), 1)

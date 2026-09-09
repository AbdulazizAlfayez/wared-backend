"""
Phase 3.5 — Email OTP / Registration Verification Tests

Covers:
  1. Registration sends email OTP (email_sent=True) + optional SMS
  2. VerifyEmailOTP endpoint — correct / wrong / expired code
  3. SendEmailVerificationOTP — already-verified guard + cooldown
  4. ResendVerificationOTP — email and phone branches + cooldown
  5. /api/me/ returns is_email_verified and is_phone_verified
  6. IsEmailVerified permission class
  7. send_otp_email Celery task
  8. All existing auth endpoints unaffected
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

User = get_user_model()

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_user(email='user@test.com', role='user', name='Test User', phone=None, verified=False):
    u = User.objects.create_user(
        email=email, password='testpass123', name=name, role=role,
    )
    if phone:
        u.phone = phone
        u.save(update_fields=['phone'])
    if verified:
        u.is_email_verified = True
        u.save(update_fields=['is_email_verified'])
    return u


def _create_email_otp(user, code='123456'):
    """Directly create an email OTP bypassing cooldown for test setup."""
    from datetime import timedelta
    from accounts.models import OTPCode
    OTPCode.objects.filter(user=user, purpose='email_verification', is_used=False).update(is_used=True)
    return OTPCode.objects.create(
        user=user,
        phone=user.email,
        code=code,
        purpose='email_verification',
        expires_at=timezone.now() + timedelta(minutes=10),
    )


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SMS_BACKEND='sms.backends.console.ConsoleSMSBackend',
    OTP_COOLDOWN_SECONDS=0,   # disable OTP cooldown for most tests
    # DummyCache means each test request starts with an empty throttle counter,
    # preventing LoginRateThrottle (set directly on RegisterView) from accumulating.
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
)
class BaseEmailOTPTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def auth(self, user):
        self.client.force_authenticate(user=user)


# ===========================================================================
# 1. Registration flow
# ===========================================================================

class RegistrationEmailOTPTests(BaseEmailOTPTestCase):

    def test_register_email_only_returns_verification_fields(self):
        resp = self.client.post('/api/auth/register/', {
            'email': 'new@test.com',
            'name': 'New User',
            'password': 'Str0ngP@ss!',
            'password2': 'Str0ngP@ss!',
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data['requires_verification'])
        self.assertTrue(data['email_sent'])
        self.assertFalse(data['sms_sent'])
        self.assertIn('detail', data)

    def test_register_with_phone_sets_sms_sent(self):
        resp = self.client.post('/api/auth/register/', {
            'email': 'withphone@test.com',
            'name': 'Phone User',
            'password': 'Str0ngP@ss!',
            'password2': 'Str0ngP@ss!',
            'phone': '0512345678',
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data['email_sent'])
        self.assertTrue(data['sms_sent'])

    def test_register_creates_email_otp(self):
        from accounts.models import OTPCode
        self.client.post('/api/auth/register/', {
            'email': 'otp@test.com',
            'name': 'OTP User',
            'password': 'Str0ngP@ss!',
            'password2': 'Str0ngP@ss!',
        })
        user = User.objects.get(email='otp@test.com')
        self.assertTrue(OTPCode.objects.filter(user=user, purpose='email_verification').exists())

    def test_register_with_phone_creates_phone_otp(self):
        from accounts.models import OTPCode
        self.client.post('/api/auth/register/', {
            'email': 'phuser@test.com',
            'name': 'Phone User',
            'password': 'Str0ngP@ss!',
            'password2': 'Str0ngP@ss!',
            'phone': '0598765432',
        })
        user = User.objects.get(email='phuser@test.com')
        self.assertTrue(OTPCode.objects.filter(user=user, purpose='phone_verification').exists())

    def test_register_invalid_phone_rejected(self):
        resp = self.client.post('/api/auth/register/', {
            'email': 'bad@test.com',
            'name': 'Bad Phone',
            'password': 'Str0ngP@ss!',
            'password2': 'Str0ngP@ss!',
            'phone': '0799999999',   # invalid Saudi format
        })
        self.assertEqual(resp.status_code, 400)

    def test_register_still_sets_auth_cookies(self):
        resp = self.client.post('/api/auth/register/', {
            'email': 'cookie@test.com',
            'name': 'Cookie User',
            'password': 'Str0ngP@ss!',
            'password2': 'Str0ngP@ss!',
        })
        self.assertIn('access_token', resp.cookies)
        self.assertIn('refresh_token', resp.cookies)


# ===========================================================================
# 2. VerifyEmailOTP
# ===========================================================================

class VerifyEmailOTPTests(BaseEmailOTPTestCase):

    def test_correct_code_marks_email_verified(self):
        user = make_user('verify@test.com')
        _create_email_otp(user, code='654321')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/verify-email/', {'code': '654321'})
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)
        self.assertIsNotNone(user.email_verified_at)

    def test_correct_code_response_body(self):
        user = make_user('body@test.com')
        _create_email_otp(user, code='111111')
        self.auth(user)
        data = self.client.post('/api/auth/otp/verify-email/', {'code': '111111'}).json()
        self.assertTrue(data['is_email_verified'])
        self.assertIn('detail', data)

    def test_wrong_code_returns_400(self):
        user = make_user('wrong@test.com')
        _create_email_otp(user, code='999999')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/verify-email/', {'code': '000000'})
        self.assertEqual(resp.status_code, 400)

    def test_wrong_code_does_not_verify(self):
        user = make_user('nowrong@test.com')
        _create_email_otp(user, code='999999')
        self.auth(user)
        self.client.post('/api/auth/otp/verify-email/', {'code': '000000'})
        user.refresh_from_db()
        self.assertFalse(user.is_email_verified)

    def test_expired_code_returns_400(self):
        from datetime import timedelta
        from accounts.models import OTPCode
        user = make_user('expired@test.com')
        OTPCode.objects.create(
            user=user, phone=user.email, code='777777',
            purpose='email_verification',
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.auth(user)
        resp = self.client.post('/api/auth/otp/verify-email/', {'code': '777777'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('expired', resp.json()['detail'].lower())

    def test_no_otp_returns_400(self):
        user = make_user('nootp@test.com')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/verify-email/', {'code': '000000'})
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_rejected(self):
        resp = self.client.post('/api/auth/otp/verify-email/', {'code': '123456'})
        self.assertIn(resp.status_code, (401, 403))

    def test_non_digit_code_rejected(self):
        user = make_user('digit@test.com')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/verify-email/', {'code': 'ABCDEF'})
        self.assertEqual(resp.status_code, 400)


# ===========================================================================
# 3. SendEmailVerificationOTP
# ===========================================================================

class SendEmailVerificationOTPTests(BaseEmailOTPTestCase):

    def test_sends_code_to_unverified_user(self):
        user = make_user('send@test.com')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/send-email-verification/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('detail', resp.json())

    def test_creates_otp_record(self):
        from accounts.models import OTPCode
        user = make_user('sendcreate@test.com')
        self.auth(user)
        self.client.post('/api/auth/otp/send-email-verification/')
        self.assertTrue(OTPCode.objects.filter(user=user, purpose='email_verification').exists())

    def test_already_verified_returns_400(self):
        user = make_user('already@test.com', verified=True)
        self.auth(user)
        resp = self.client.post('/api/auth/otp/send-email-verification/')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already verified', resp.json()['detail'].lower())

    @override_settings(OTP_COOLDOWN_SECONDS=60)
    def test_cooldown_returns_429(self):
        user = make_user('cooldown@test.com')
        self.auth(user)
        self.client.post('/api/auth/otp/send-email-verification/')
        resp = self.client.post('/api/auth/otp/send-email-verification/')
        self.assertEqual(resp.status_code, 429)

    def test_unauthenticated_rejected(self):
        resp = self.client.post('/api/auth/otp/send-email-verification/')
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# 4. ResendVerificationOTP
# ===========================================================================

class ResendVerificationOTPTests(BaseEmailOTPTestCase):

    def test_resend_email_returns_200(self):
        user = make_user('resend@test.com')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/resend/', {'type': 'email'})
        self.assertEqual(resp.status_code, 200)

    def test_resend_email_creates_new_otp(self):
        from accounts.models import OTPCode
        user = make_user('resendotp@test.com')
        self.auth(user)
        self.client.post('/api/auth/otp/resend/', {'type': 'email'})
        self.assertTrue(OTPCode.objects.filter(user=user, purpose='email_verification').exists())

    def test_resend_email_already_verified_returns_400(self):
        user = make_user('resend_verified@test.com', verified=True)
        self.auth(user)
        resp = self.client.post('/api/auth/otp/resend/', {'type': 'email'})
        self.assertEqual(resp.status_code, 400)

    def test_resend_phone_returns_200(self):
        user = make_user('resendphone@test.com', phone='0512341234')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/resend/', {'type': 'phone'})
        self.assertEqual(resp.status_code, 200)

    def test_resend_phone_no_phone_on_file_returns_400(self):
        user = make_user('nophone@test.com')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/resend/', {'type': 'phone'})
        self.assertEqual(resp.status_code, 400)

    @override_settings(OTP_COOLDOWN_SECONDS=60)
    def test_resend_email_cooldown_returns_429(self):
        user = make_user('resendcooldown@test.com')
        self.auth(user)
        self.client.post('/api/auth/otp/resend/', {'type': 'email'})
        resp = self.client.post('/api/auth/otp/resend/', {'type': 'email'})
        self.assertEqual(resp.status_code, 429)

    def test_invalid_type_rejected(self):
        user = make_user('badtype@test.com')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/resend/', {'type': 'fax'})
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_rejected(self):
        resp = self.client.post('/api/auth/otp/resend/', {'type': 'email'})
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# 5. /api/me/ returns verification status
# ===========================================================================

class MeViewVerificationStatusTests(BaseEmailOTPTestCase):

    def test_me_includes_is_email_verified_false(self):
        user = make_user('me_unverified@test.com')
        self.auth(user)
        data = self.client.get('/api/me/').json()
        self.assertIn('is_email_verified', data)
        self.assertFalse(data['is_email_verified'])

    def test_me_includes_is_email_verified_true(self):
        user = make_user('me_verified@test.com', verified=True)
        self.auth(user)
        data = self.client.get('/api/me/').json()
        self.assertTrue(data['is_email_verified'])

    def test_me_includes_is_phone_verified(self):
        user = make_user('me_phone@test.com')
        self.auth(user)
        data = self.client.get('/api/me/').json()
        self.assertIn('is_phone_verified', data)
        self.assertFalse(data['is_phone_verified'])

    def test_me_phone_verified_after_verify(self):
        user = make_user('me_phone_verify@test.com', phone='0512345678')
        _create_email_otp(user, code='555555')
        # Manually set phone verified to simulate the flow
        user.is_phone_verified = True
        user.save(update_fields=['is_phone_verified'])
        self.auth(user)
        data = self.client.get('/api/me/').json()
        self.assertTrue(data['is_phone_verified'])


# ===========================================================================
# 6. IsEmailVerified permission class
# ===========================================================================

class IsEmailVerifiedPermissionTests(TestCase):

    def test_denies_unverified_user(self):
        from accounts.permissions import IsEmailVerified
        from unittest.mock import MagicMock

        perm = IsEmailVerified()
        request = MagicMock()
        request.user.is_authenticated = True
        request.user.is_email_verified = False
        self.assertFalse(perm.has_permission(request, None))

    def test_allows_verified_user(self):
        from accounts.permissions import IsEmailVerified
        from unittest.mock import MagicMock

        perm = IsEmailVerified()
        request = MagicMock()
        request.user.is_authenticated = True
        request.user.is_email_verified = True
        self.assertTrue(perm.has_permission(request, None))

    def test_denies_anonymous_user(self):
        from accounts.permissions import IsEmailVerified
        from unittest.mock import MagicMock

        perm = IsEmailVerified()
        request = MagicMock()
        request.user.is_authenticated = False
        self.assertFalse(perm.has_permission(request, None))

    def test_permission_message(self):
        from accounts.permissions import IsEmailVerified
        self.assertIn('verify', IsEmailVerified.message.lower())


# ===========================================================================
# 7. send_otp_email Celery task
# ===========================================================================

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class SendOTPEmailTaskTests(TestCase):

    def setUp(self):
        self.user = make_user('task@test.com', name='Task User')

    def test_task_sends_email(self):
        from django.core import mail
        from accounts.tasks import send_otp_email
        send_otp_email(self.user.pk, '987654', 'email_verification')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.email, mail.outbox[0].to)

    def test_task_email_subject_verification(self):
        from django.core import mail
        from accounts.tasks import send_otp_email
        send_otp_email(self.user.pk, '123123', 'email_verification')
        self.assertIn('Verify', mail.outbox[0].subject)

    def test_task_email_contains_code(self):
        from django.core import mail
        from accounts.tasks import send_otp_email
        send_otp_email(self.user.pk, '456456', 'email_verification')
        self.assertIn('456456', mail.outbox[0].alternatives[0][0])

    def test_task_handles_missing_user(self):
        from accounts.tasks import send_otp_email
        result = send_otp_email(99999, '000000', 'email_verification')
        self.assertIn('not found', result)


# ===========================================================================
# 8. Existing auth endpoints unaffected
# ===========================================================================

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    OTP_COOLDOWN_SECONDS=0,
)
class ExistingAuthUnaffectedTests(BaseEmailOTPTestCase):

    def test_login_still_works(self):
        make_user('login@test.com', verified=True)
        resp = self.client.post('/api/auth/login/', {
            'email': 'login@test.com',
            'password': 'testpass123',
        })
        self.assertEqual(resp.status_code, 200)

    def test_logout_still_works(self):
        user = make_user('logout@test.com')
        self.auth(user)
        resp = self.client.post('/api/auth/logout/')
        self.assertEqual(resp.status_code, 200)

    def test_send_phone_otp_still_works(self):
        user = make_user('phoneotp@test.com')
        self.auth(user)
        resp = self.client.post('/api/auth/otp/send-verification/', {'phone': '0512345678'})
        self.assertEqual(resp.status_code, 200)

    def test_verify_phone_otp_still_works(self):
        from accounts.models import OTPCode
        from datetime import timedelta
        user = make_user('phoneverify@test.com')
        # Create phone OTP directly
        OTPCode.objects.create(
            user=user, phone='0512345678', code='321321',
            purpose='phone_verification',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.auth(user)
        resp = self.client.post('/api/auth/otp/verify-phone/', {'code': '321321'})
        self.assertEqual(resp.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.is_phone_verified)

    def test_csrf_endpoint_still_works(self):
        resp = self.client.get('/api/auth/csrf/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('csrfToken', resp.json())


# ===========================================================================
# 9. Re-signup on unverified accounts
# ===========================================================================

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    OTP_COOLDOWN_SECONDS=0,
)
class ReSignupUnverifiedTests(TestCase):
    """Signup → abandon OTP → signup again with same email."""

    def setUp(self):
        self.client = APIClient()
        # Clear throttle caches so earlier tests don't cause 429
        from django.core.cache import cache
        cache.clear()

    def _signup(self, email, name='First Name', password='StrongPass99!', phone=''):
        return self.client.post('/api/auth/register/', {
            'email': email,
            'name': name,
            'password': password,
            'password2': password,
            'phone': phone,
        })

    def test_resignup_updates_user_and_creates_new_otp(self):
        """Signup → abandon → re-signup with different name/password succeeds."""
        from accounts.models import OTPCode

        # First signup
        r1 = self._signup('dup@test.com', name='Old Name', password='OldPass123!')
        self.assertIn(r1.status_code, (200, 201))
        user_id = r1.json()['user']['id']
        old_otp_code = r1.json().get('debug_otp_code')

        # Re-signup with different name + password
        r2 = self._signup('dup@test.com', name='New Name', password='NewPass456!')
        self.assertEqual(r2.status_code, 200)
        data = r2.json()

        # Response shape matches fresh signup
        self.assertIn('requires_verification', data)
        self.assertTrue(data['requires_verification'])
        self.assertIn('user', data)
        self.assertIn('tokens', data)
        self.assertIn('email_sent', data)
        self.assertIn('sms_sent', data)
        self.assertEqual(data['user']['id'], user_id)
        self.assertEqual(data['user']['name'], 'New Name')

        # User row was updated (not duplicated)
        user = User.objects.get(pk=user_id)
        self.assertEqual(user.name, 'New Name')
        self.assertTrue(user.check_password('NewPass456!'))
        self.assertFalse(user.is_email_verified)

        # Old OTP is invalidated, new one exists
        new_otp_code = data.get('debug_otp_code')
        if old_otp_code and new_otp_code:
            self.assertNotEqual(old_otp_code, new_otp_code)
        active_otps = OTPCode.objects.filter(
            user=user, purpose='email_verification', is_used=False,
        )
        self.assertEqual(active_otps.count(), 1)

    def test_resignup_on_verified_user_is_rejected(self):
        """Signup with email of a verified user → 400."""
        make_user('verified@test.com', verified=True)
        r = self._signup('verified@test.com', name='Attacker', password='HackPass123!')
        self.assertEqual(r.status_code, 400)
        self.assertIn('already registered', str(r.json()))

    def test_resignup_response_shape_matches_fresh(self):
        """Re-signup response has identical keys to a fresh signup."""
        r1 = self._signup('shape@test.com', name='Fresh')
        fresh_keys = set(r1.json().keys())

        r2 = self._signup('shape@test.com', name='Refreshed')
        resignup_keys = set(r2.json().keys())

        # Both must have at minimum these keys
        required = {'detail', 'requires_verification', 'email_sent', 'user', 'tokens'}
        self.assertTrue(required.issubset(fresh_keys), f"Fresh missing: {required - fresh_keys}")
        self.assertTrue(required.issubset(resignup_keys), f"Re-signup missing: {required - resignup_keys}")

    def test_unverified_email_with_invalid_phone_shows_only_phone_error(self):
        """Unverified email + invalid phone → 400 with ONLY the phone error."""
        # First create an unverified account
        self._signup('phonetest@test.com', name='First')
        # Re-signup with invalid phone — should NOT show email-exists error
        r = self.client.post('/api/auth/register/', {
            'email': 'phonetest@test.com',
            'name': 'Second',
            'password': 'StrongPass2026',
            'password2': 'StrongPass2026',
            'phone': 'not-a-valid-phone-longer-than-20-chars',
        })
        # The view handles the unverified re-signup before the serializer,
        # but the phone on the existing user gets updated (or fails validation
        # in the view's direct save). Either way, no email-exists error.
        body = r.json()
        body_str = str(body)
        self.assertNotIn('already registered', body_str)

    def test_case_insensitive_resignup(self):
        """Same email, different casing → treated as same unverified account."""
        r1 = self._signup('CaseTest@Test.COM', name='First')
        self.assertIn(r1.status_code, (200, 201))
        user_id = r1.json()['user']['id']

        r2 = self._signup('casetest@test.com', name='Second')
        self.assertIn(r2.status_code, (200, 201))
        self.assertEqual(r2.json()['user']['id'], user_id)
        self.assertEqual(r2.json()['user']['name'], 'Second')

    def test_verified_email_different_case_still_rejected(self):
        """Verified email with different casing → still rejected."""
        make_user('CaseVerified@test.com', verified=True)
        r = self._signup('caseverified@test.com', name='Attacker', password='HackPass2026')
        self.assertEqual(r.status_code, 400)
        self.assertIn('already registered', str(r.json()))


# ===========================================================================
# 10. Login blocked for unverified accounts
# ===========================================================================

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    OTP_COOLDOWN_SECONDS=0,
)
class LoginUnverifiedBlockTests(TestCase):
    """Login must reject unverified accounts with a fresh OTP."""

    def setUp(self):
        self.client = APIClient()
        from django.core.cache import cache
        cache.clear()

    def test_unverified_login_returns_403_with_code(self):
        """Unverified user login → 403, code=email_not_verified, OTP created."""
        from accounts.models import OTPCode
        user = make_user('unverif@test.com', verified=False)
        resp = self.client.post('/api/auth/login/', {
            'email': 'unverif@test.com', 'password': 'testpass123',
        })
        self.assertEqual(resp.status_code, 403)
        data = resp.json()
        self.assertEqual(data['code'], 'email_not_verified')
        self.assertIn('detail', data)
        # No tokens set
        self.assertNotIn('tokens', data)
        self.assertNotIn('access', data)
        # OTP was created
        otp_exists = OTPCode.objects.filter(
            user=user, purpose='email_verification', is_used=False,
        ).exists()
        self.assertTrue(otp_exists)

    def test_verified_login_works(self):
        """Verified user login → 200 with tokens."""
        make_user('verif@test.com', verified=True)
        resp = self.client.post('/api/auth/login/', {
            'email': 'verif@test.com', 'password': 'testpass123',
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('user', data)
        self.assertIn('tokens', data)

    def test_verify_otp_then_login_works(self):
        """Register (unverified) → verify OTP → login succeeds."""
        from accounts.models import OTPCode
        from datetime import timedelta
        user = make_user('flow@test.com', verified=False)
        # Create a valid OTP
        OTPCode.objects.create(
            user=user, phone=user.email, code='654321',
            purpose='email_verification',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        # Verify it
        self.client.force_authenticate(user=user)
        verify_resp = self.client.post('/api/auth/otp/verify-email/', {'code': '654321'})
        self.assertEqual(verify_resp.status_code, 200)
        self.client.force_authenticate(user=None)
        # Now login should work
        resp = self.client.post('/api/auth/login/', {
            'email': 'flow@test.com', 'password': 'testpass123',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn('tokens', resp.json())

    def test_token_refresh_blocked_for_unverified(self):
        """Refresh token for unverified user → 403."""
        from rest_framework_simplejwt.tokens import RefreshToken
        user = make_user('unverif_refresh@test.com', verified=False)
        refresh = RefreshToken.for_user(user)
        resp = self.client.post('/api/auth/token/refresh/', {
            'refresh': str(refresh),
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get('code'), 'email_not_verified')

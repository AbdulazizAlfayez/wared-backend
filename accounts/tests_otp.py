"""
Phase 3.4 — OTP / SMS System Tests

Covers:
  1. SMS backend (ConsoleSMSBackend)
  2. sms/utils.py (send_sms + get_sms_backend)
  3. OTP helper: generate_otp, create_otp, verify_otp
  4. accounts.tasks.send_otp_sms
  5. OTP endpoints: send-verification, verify-phone, send-login, verify-login
  6. SMS notification tasks: send_new_lead_sms, send_appointment_sms
  7. Phone format validation
  8. Existing auth endpoints still work
"""

import datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

User = get_user_model()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PHONE   = '+966512345678'
VALID_PHONE_2 = '0512345678'
INVALID_PHONE = '123456'


def make_user(email='u@test.com', role='user', name='User',
              phone=None, is_phone_verified=False):
    u = User.objects.create_user(email=email, password='pass1234!', name=name, role=role)
    if phone:
        u.phone = phone
        u.is_phone_verified = is_phone_verified
        u.save(update_fields=['phone', 'is_phone_verified'])
    return u


def make_listing(owner):
    from cars.models import Listing
    return Listing.objects.create(
        title='Test Car', make='Toyota', model='Camry',
        year=2022, price=80000, mileage=20000, city='Riyadh',
        owner=owner, status='approved', is_active=True,
        fuel_type='petrol', transmission='automatic',
    )


# ---------------------------------------------------------------------------
# 1. SMS backend
# ---------------------------------------------------------------------------

class ConsoleSMSBackendTests(TestCase):

    def test_send_sms_returns_true_and_prints(self):
        from sms.backends.console import ConsoleSMSBackend
        backend = ConsoleSMSBackend()
        with patch('builtins.print') as mock_print:
            result = backend.send_sms(VALID_PHONE, 'Hello')
        self.assertTrue(result)
        mock_print.assert_called_once()
        output = mock_print.call_args[0][0]
        self.assertIn(VALID_PHONE, output)
        self.assertIn('Hello', output)


# ---------------------------------------------------------------------------
# 2. sms/utils.py
# ---------------------------------------------------------------------------

@override_settings(SMS_BACKEND='sms.backends.console.ConsoleSMSBackend')
class SMSUtilsTests(TestCase):

    def test_get_sms_backend_returns_console_instance(self):
        from sms.backends.console import ConsoleSMSBackend
        from sms.utils import get_sms_backend
        backend = get_sms_backend()
        self.assertIsInstance(backend, ConsoleSMSBackend)

    def test_send_sms_delegates_to_backend(self):
        from sms.utils import send_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True) as mock:
            result = send_sms(VALID_PHONE, 'test msg')
        mock.assert_called_once_with(VALID_PHONE, 'test msg')
        self.assertTrue(result)


# ---------------------------------------------------------------------------
# 3. OTP helpers
# ---------------------------------------------------------------------------

class OTPHelpersTests(TestCase):

    def setUp(self):
        self.user = make_user()

    def test_generate_otp_is_6_digits(self):
        from accounts.otp import generate_otp
        code = generate_otp()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_create_otp_success(self):
        from accounts.otp import create_otp
        otp, error = create_otp(self.user, VALID_PHONE, 'phone_verification')
        self.assertIsNone(error)
        self.assertIsNotNone(otp)
        self.assertEqual(otp.phone, VALID_PHONE)
        self.assertFalse(otp.is_used)
        self.assertFalse(otp.is_expired())

    def test_create_otp_cooldown_blocks_second_request(self):
        from accounts.otp import create_otp
        create_otp(self.user, VALID_PHONE, 'phone_verification')
        otp2, error = create_otp(self.user, VALID_PHONE, 'phone_verification')
        self.assertIsNone(otp2)
        self.assertIn('wait', error.lower())

    def test_create_otp_invalidates_previous(self):
        from accounts.models import OTPCode
        from accounts.otp import create_otp
        otp1, _ = create_otp(self.user, VALID_PHONE, 'phone_verification')
        # Force-age otp1 past the cooldown
        OTPCode.objects.filter(pk=otp1.pk).update(
            created_at=timezone.now() - datetime.timedelta(seconds=120)
        )
        otp2, _ = create_otp(self.user, VALID_PHONE, 'phone_verification')
        otp1.refresh_from_db()
        self.assertTrue(otp1.is_used)
        self.assertFalse(otp2.is_used)

    def test_verify_otp_correct_code(self):
        from accounts.otp import create_otp, verify_otp
        otp, _ = create_otp(self.user, VALID_PHONE, 'phone_verification')
        success, msg = verify_otp(self.user, otp.code, 'phone_verification')
        self.assertTrue(success)
        otp.refresh_from_db()
        self.assertTrue(otp.is_used)

    def test_verify_otp_wrong_code_increments_attempts(self):
        from accounts.models import OTPCode
        from accounts.otp import create_otp, verify_otp
        otp, _ = create_otp(self.user, VALID_PHONE, 'phone_verification')
        success, msg = verify_otp(self.user, '000000', 'phone_verification')
        self.assertFalse(success)
        self.assertIn('Invalid', msg)
        otp.refresh_from_db()
        self.assertEqual(otp.attempts, 1)

    def test_verify_otp_expired(self):
        from accounts.models import OTPCode
        from accounts.otp import create_otp, verify_otp
        otp, _ = create_otp(self.user, VALID_PHONE, 'phone_verification')
        OTPCode.objects.filter(pk=otp.pk).update(
            expires_at=timezone.now() - datetime.timedelta(minutes=1)
        )
        success, msg = verify_otp(self.user, otp.code, 'phone_verification')
        self.assertFalse(success)
        self.assertIn('expired', msg.lower())

    @override_settings(OTP_MAX_ATTEMPTS=3)
    def test_verify_otp_max_attempts_exceeded(self):
        from accounts.models import OTPCode
        from accounts.otp import create_otp, verify_otp
        otp, _ = create_otp(self.user, VALID_PHONE, 'phone_verification')
        OTPCode.objects.filter(pk=otp.pk).update(attempts=3)
        success, msg = verify_otp(self.user, otp.code, 'phone_verification')
        self.assertFalse(success)
        self.assertIn('Too many', msg)

    def test_verify_otp_no_code_exists(self):
        from accounts.otp import verify_otp
        success, msg = verify_otp(self.user, '123456', 'phone_verification')
        self.assertFalse(success)
        self.assertIn('No OTP', msg)


# ---------------------------------------------------------------------------
# 4. accounts.tasks.send_otp_sms
# ---------------------------------------------------------------------------

@override_settings(SMS_BACKEND='sms.backends.console.ConsoleSMSBackend',
                   CELERY_TASK_ALWAYS_EAGER=True)
class SendOTPSMSTaskTests(TestCase):

    def setUp(self):
        self.user = make_user()

    def test_send_otp_sms_phone_verification(self):
        from accounts.tasks import send_otp_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True) as mock:
            result = send_otp_sms(self.user.pk, VALID_PHONE, '123456', 'phone_verification')
        mock.assert_called_once()
        msg = mock.call_args[0][1]
        self.assertIn('123456', msg)
        self.assertIn('verification', msg.lower())
        self.assertIn('sent', result)

    def test_send_otp_sms_login(self):
        from accounts.tasks import send_otp_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True) as mock:
            result = send_otp_sms(self.user.pk, VALID_PHONE, '654321', 'login')
        msg = mock.call_args[0][1]
        self.assertIn('654321', msg)
        self.assertIn('login', msg.lower())

    def test_send_otp_sms_failure_returns_failed(self):
        from accounts.tasks import send_otp_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=False):
            result = send_otp_sms(self.user.pk, VALID_PHONE, '000000', 'login')
        self.assertIn('failed', result.lower())


# ---------------------------------------------------------------------------
# 5. OTP API endpoints
# ---------------------------------------------------------------------------

@override_settings(SMS_BACKEND='sms.backends.console.ConsoleSMSBackend',
                   CELERY_TASK_ALWAYS_EAGER=True,
                   OTP_COOLDOWN_SECONDS=0)     # disable cooldown for endpoint tests
class SendVerificationOTPEndpointTests(TestCase):

    def setUp(self):
        self.user   = make_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_send_verification_returns_200(self):
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True):
            resp = self.client.post('/api/auth/otp/send-verification/', {'phone': VALID_PHONE})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('sent', resp.data['detail'].lower())

    def test_send_verification_creates_otp(self):
        from accounts.models import OTPCode
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True):
            self.client.post('/api/auth/otp/send-verification/', {'phone': VALID_PHONE})
        self.assertTrue(OTPCode.objects.filter(user=self.user, purpose='phone_verification').exists())

    def test_send_verification_invalid_phone_returns_400(self):
        resp = self.client.post('/api/auth/otp/send-verification/', {'phone': INVALID_PHONE})
        self.assertEqual(resp.status_code, 400)

    def test_send_verification_unauthenticated_returns_401(self):
        client = APIClient()
        resp = client.post('/api/auth/otp/send-verification/', {'phone': VALID_PHONE})
        self.assertEqual(resp.status_code, 401)

    @override_settings(OTP_COOLDOWN_SECONDS=60)
    def test_send_verification_cooldown_returns_429(self):
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True):
            self.client.post('/api/auth/otp/send-verification/', {'phone': VALID_PHONE})
            resp = self.client.post('/api/auth/otp/send-verification/', {'phone': VALID_PHONE})
        self.assertEqual(resp.status_code, 429)
        self.assertIn('wait', resp.data['detail'].lower())


@override_settings(SMS_BACKEND='sms.backends.console.ConsoleSMSBackend',
                   CELERY_TASK_ALWAYS_EAGER=True,
                   OTP_COOLDOWN_SECONDS=0)
class VerifyPhoneOTPEndpointTests(TestCase):

    def setUp(self):
        self.user   = make_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _send_otp(self):
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True):
            self.client.post('/api/auth/otp/send-verification/', {'phone': VALID_PHONE})
        from accounts.models import OTPCode
        return OTPCode.objects.filter(user=self.user, purpose='phone_verification').latest('created_at')

    def test_verify_correct_code_marks_phone_verified(self):
        otp = self._send_otp()
        resp = self.client.post('/api/auth/otp/verify-phone/', {'code': otp.code})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_phone_verified'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_phone_verified)
        self.assertEqual(self.user.phone, VALID_PHONE)
        self.assertIsNotNone(self.user.phone_verified_at)

    def test_verify_wrong_code_returns_400(self):
        self._send_otp()
        resp = self.client.post('/api/auth/otp/verify-phone/', {'code': '000000'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Invalid', resp.data['detail'])

    def test_verify_non_digit_code_returns_400(self):
        resp = self.client.post('/api/auth/otp/verify-phone/', {'code': 'abcdef'})
        self.assertEqual(resp.status_code, 400)

    def test_verify_no_otp_exists_returns_400(self):
        resp = self.client.post('/api/auth/otp/verify-phone/', {'code': '123456'})
        self.assertEqual(resp.status_code, 400)

    def test_verify_expired_code_returns_400(self):
        from accounts.models import OTPCode
        otp = self._send_otp()
        OTPCode.objects.filter(pk=otp.pk).update(
            expires_at=timezone.now() - datetime.timedelta(minutes=1)
        )
        resp = self.client.post('/api/auth/otp/verify-phone/', {'code': otp.code})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('expired', resp.data['detail'].lower())

    @override_settings(OTP_MAX_ATTEMPTS=3)
    def test_verify_after_max_attempts_returns_400(self):
        from accounts.models import OTPCode
        otp = self._send_otp()
        OTPCode.objects.filter(pk=otp.pk).update(attempts=3)
        resp = self.client.post('/api/auth/otp/verify-phone/', {'code': otp.code})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Too many', resp.data['detail'])


@override_settings(SMS_BACKEND='sms.backends.console.ConsoleSMSBackend',
                   CELERY_TASK_ALWAYS_EAGER=True,
                   OTP_COOLDOWN_SECONDS=0,
                   DEFAULT_THROTTLE_RATES={'otp': '1000/hour', 'login': '1000/min', 'password_reset': '1000/hour'})
class SendLoginOTPEndpointTests(TestCase):

    def setUp(self):
        self.user   = make_user(phone=VALID_PHONE, is_phone_verified=True)
        self.client = APIClient()

    def test_send_login_otp_verified_phone_returns_200(self):
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True):
            resp = self.client.post('/api/auth/otp/send-login/', {'phone': VALID_PHONE})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('code has been sent', resp.data['detail'].lower())

    def test_send_login_otp_unknown_phone_still_returns_200(self):
        resp = self.client.post('/api/auth/otp/send-login/', {'phone': '+966599999999'})
        self.assertEqual(resp.status_code, 200)

    def test_send_login_otp_invalid_phone_returns_400(self):
        resp = self.client.post('/api/auth/otp/send-login/', {'phone': INVALID_PHONE})
        self.assertEqual(resp.status_code, 400)

    def test_send_login_otp_creates_otp_for_known_user(self):
        from accounts.models import OTPCode
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True):
            self.client.post('/api/auth/otp/send-login/', {'phone': VALID_PHONE})
        self.assertTrue(OTPCode.objects.filter(user=self.user, purpose='login').exists())


@override_settings(SMS_BACKEND='sms.backends.console.ConsoleSMSBackend',
                   CELERY_TASK_ALWAYS_EAGER=True,
                   OTP_COOLDOWN_SECONDS=0,
                   DEFAULT_THROTTLE_RATES={'otp': '1000/hour', 'login': '1000/min', 'password_reset': '1000/hour'})
class VerifyLoginOTPEndpointTests(TestCase):

    def setUp(self):
        self.user   = make_user(phone=VALID_PHONE, is_phone_verified=True)
        self.client = APIClient()

    def _send_otp(self):
        """Create a login OTP directly (bypasses HTTP throttle in tests)."""
        from accounts.otp import create_otp
        otp, _ = create_otp(self.user, VALID_PHONE, 'login')
        return otp

    def test_verify_login_otp_returns_200_and_sets_cookies(self):
        otp = self._send_otp()
        resp = self.client.post('/api/auth/otp/verify-login/', {
            'phone': VALID_PHONE, 'code': otp.code,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn('user', resp.data)
        self.assertIn('access_token', resp.cookies)
        self.assertIn('refresh_token', resp.cookies)

    def test_verify_login_otp_wrong_code_returns_400(self):
        self._send_otp()
        resp = self.client.post('/api/auth/otp/verify-login/', {
            'phone': VALID_PHONE, 'code': '000000',
        })
        self.assertEqual(resp.status_code, 400)

    def test_verify_login_otp_unknown_phone_returns_400(self):
        resp = self.client.post('/api/auth/otp/verify-login/', {
            'phone': '+966599999999', 'code': '123456',
        })
        self.assertEqual(resp.status_code, 400)

    def test_verify_login_otp_logs_audit_action(self):
        from auditlog.models import AuditLog
        otp = self._send_otp()
        self.client.post('/api/auth/otp/verify-login/', {
            'phone': VALID_PHONE, 'code': otp.code,
        })
        self.assertTrue(AuditLog.objects.filter(user=self.user, action='otp_login').exists())

    def test_verify_login_otp_invalid_phone_format_returns_400(self):
        resp = self.client.post('/api/auth/otp/verify-login/', {
            'phone': INVALID_PHONE, 'code': '123456',
        })
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# 6. SMS notification tasks
# ---------------------------------------------------------------------------

@override_settings(SMS_BACKEND='sms.backends.console.ConsoleSMSBackend',
                   CELERY_TASK_ALWAYS_EAGER=True)
class LeadSMSTaskTests(TestCase):

    def setUp(self):
        from notifications.models import NotificationPreference
        self.dealer = make_user('dealer@t.com', role='importer', phone=VALID_PHONE, is_phone_verified=True)
        NotificationPreference.objects.create(user=self.dealer, sms_notifications=True)
        self.buyer  = make_user('buyer@t.com',  role='user')
        self.listing = make_listing(self.dealer)
        from leads.models import Lead
        self.lead = Lead.objects.create(
            listing=self.listing, buyer=self.buyer, dealer=self.dealer,
            message='Interested', source='listing_page',
        )

    def test_send_new_lead_sms_with_verified_phone(self):
        from notifications.sms_tasks import send_new_lead_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True) as mock:
            result = send_new_lead_sms(self.lead.pk)
        mock.assert_called_once()
        self.assertIn(VALID_PHONE, mock.call_args[0][0])
        self.assertIn('sent', result)

    def test_send_new_lead_sms_skips_unverified_phone(self):
        self.dealer.is_phone_verified = False
        self.dealer.save(update_fields=['is_phone_verified'])
        from notifications.sms_tasks import send_new_lead_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True) as mock:
            result = send_new_lead_sms(self.lead.pk)
        mock.assert_not_called()
        self.assertIn('disabled', result)

    def test_send_new_lead_sms_sms_pref_off(self):
        from notifications.models import NotificationPreference
        NotificationPreference.objects.filter(user=self.dealer).update(sms_notifications=False)
        from notifications.sms_tasks import send_new_lead_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True) as mock:
            result = send_new_lead_sms(self.lead.pk)
        mock.assert_not_called()

    def test_send_new_lead_sms_not_found(self):
        from notifications.sms_tasks import send_new_lead_sms
        result = send_new_lead_sms(99999)
        self.assertIn('not found', result)


@override_settings(SMS_BACKEND='sms.backends.console.ConsoleSMSBackend',
                   CELERY_TASK_ALWAYS_EAGER=True)
class AppointmentSMSTaskTests(TestCase):

    def setUp(self):
        from notifications.models import NotificationPreference
        self.seller = make_user('seller@t.com', role='importer')
        self.buyer  = make_user('buyer@t.com',  role='user',
                                phone=VALID_PHONE, is_phone_verified=True)
        NotificationPreference.objects.create(user=self.buyer, sms_notifications=True)
        self.listing = make_listing(self.seller)
        from bookings.models import Appointment
        future = datetime.date.today() + datetime.timedelta(days=5)
        self.appt = Appointment.objects.create(
            listing=self.listing, buyer=self.buyer, seller=self.seller,
            appointment_date=future, appointment_time=datetime.time(10, 0),
        )

    def test_send_appointment_sms_confirmed(self):
        from notifications.sms_tasks import send_appointment_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True) as mock:
            result = send_appointment_sms(self.appt.pk, 'confirmed')
        mock.assert_called_once()
        msg = mock.call_args[0][1]
        self.assertIn('confirmed', msg.lower())
        self.assertIn('sent', result)

    def test_send_appointment_sms_rejected(self):
        from notifications.sms_tasks import send_appointment_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True) as mock:
            result = send_appointment_sms(self.appt.pk, 'rejected')
        mock.assert_called_once()
        msg = mock.call_args[0][1]
        self.assertIn('could not be confirmed', msg.lower())

    def test_send_appointment_sms_skips_unverified_buyer(self):
        self.buyer.is_phone_verified = False
        self.buyer.save(update_fields=['is_phone_verified'])
        from notifications.sms_tasks import send_appointment_sms
        with patch('sms.backends.console.ConsoleSMSBackend.send_sms', return_value=True) as mock:
            send_appointment_sms(self.appt.pk, 'confirmed')
        mock.assert_not_called()

    def test_send_appointment_sms_not_found(self):
        from notifications.sms_tasks import send_appointment_sms
        result = send_appointment_sms(99999, 'confirmed')
        self.assertIn('not found', result)


# ---------------------------------------------------------------------------
# 7. Phone format validation (serializer level)
# ---------------------------------------------------------------------------

class PhoneValidationTests(TestCase):

    def _validate(self, phone):
        from accounts.serializers import SendOTPSerializer
        s = SendOTPSerializer(data={'phone': phone})
        s.is_valid()
        return s

    def test_valid_plus966_format(self):
        s = self._validate('+966512345678')
        self.assertTrue(s.is_valid())

    def test_valid_05_format(self):
        s = self._validate('0512345678')
        self.assertTrue(s.is_valid())

    def test_invalid_no_prefix(self):
        s = self._validate('512345678')
        self.assertFalse(s.is_valid())

    def test_invalid_too_short(self):
        s = self._validate('+96651234')
        self.assertFalse(s.is_valid())

    def test_invalid_letters(self):
        s = self._validate('+966ABCDEFGH')
        self.assertFalse(s.is_valid())

    def test_invalid_plus1(self):
        s = self._validate('+15555555555')
        self.assertFalse(s.is_valid())


# ---------------------------------------------------------------------------
# 8. Existing auth endpoints still work
# ---------------------------------------------------------------------------

class ExistingAuthEndpointsTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_user()
        # Login now blocks unverified emails — verify so classic login works
        self.user.is_email_verified = True
        self.user.save(update_fields=['is_email_verified'])

    def test_login_still_works(self):
        resp = self.client.post('/api/auth/login/', {
            'email': self.user.email, 'password': 'pass1234!',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn('user', resp.data)

    def test_logout_still_works(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post('/api/auth/logout/')
        self.assertEqual(resp.status_code, 200)

    def test_me_still_works(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get('/api/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['email'], self.user.email)

    def test_register_still_works(self):
        resp = self.client.post('/api/auth/register/', {
            'email': 'new@test.com', 'name': 'New User',
            'password': 'Str0ng!pass', 'password2': 'Str0ng!pass',
        })
        self.assertEqual(resp.status_code, 201)

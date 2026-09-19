"""
Mobile Google sign-in, the in-app password reset, and the verification flags.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import OTPCode, User

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}
GOOGLE_SETTINGS = dict(
    GOOGLE_OAUTH2_CLIENT_ID='web.apps.googleusercontent.com',
    GOOGLE_OAUTH2_IOS_CLIENT_ID='ios.apps.googleusercontent.com',
    GOOGLE_OAUTH2_ANDROID_CLIENT_ID='android.apps.googleusercontent.com',
    CACHES=DUMMY_CACHE,
)


@override_settings(**GOOGLE_SETTINGS)
class MobileGoogleTests(TestCase):
    URL = '/api/auth/mobile/google/'

    def setUp(self):
        self.client = APIClient()

    @patch('google.oauth2.id_token.verify_oauth2_token')
    def test_a_new_google_user_is_created_and_gets_tokens_in_the_body(self, verify):
        verify.return_value = {
            'email': 'newbie@gmail.com', 'given_name': 'New', 'family_name': 'Bie',
        }

        response = self.client.post(self.URL, {'id_token': 'x'}, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        # The same flat shape MobileLoginView returns, so the app stores a
        # session one way regardless of how it was obtained.
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['email'], 'newbie@gmail.com')
        self.assertEqual(response.data['user']['name'], 'New Bie')
        # No cookies: the web flow sets them, mobile must not rely on them.
        self.assertEqual(len(response.cookies), 0)

    @patch('google.oauth2.id_token.verify_oauth2_token')
    def test_google_proves_the_address_so_it_is_not_re_verified(self, verify):
        verify.return_value = {'email': 'verified@gmail.com', 'given_name': 'V'}

        self.client.post(self.URL, {'id_token': 'x'}, format='json')

        self.assertTrue(User.objects.get(email='verified@gmail.com').is_email_verified)

    @patch('google.oauth2.id_token.verify_oauth2_token')
    def test_an_existing_account_signs_in_rather_than_duplicating(self, verify):
        User.objects.create_user(email='known@gmail.com', password='pw12345!', name='Known')
        verify.return_value = {'email': 'known@gmail.com', 'given_name': 'Known'}

        response = self.client.post(self.URL, {'id_token': 'x'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['user']['created'])
        self.assertEqual(User.objects.filter(email='known@gmail.com').count(), 1)

    @patch('google.oauth2.id_token.verify_oauth2_token')
    def test_a_mobile_token_is_accepted_even_though_its_audience_is_not_the_website(self, verify):
        """
        An ID token from the iOS SDK carries the iOS client id in `aud`. The
        web endpoint checks only the website's, which is why it rejects every
        mobile token — this one tries each configured audience.
        """
        def only_ios(token, request, audience):
            if audience != 'ios.apps.googleusercontent.com':
                raise ValueError('Wrong audience')
            return {'email': 'ios@gmail.com', 'given_name': 'iOS'}

        verify.side_effect = only_ios

        response = self.client.post(self.URL, {'id_token': 'x'}, format='json')

        self.assertEqual(response.status_code, 201, response.data)

    @patch('google.oauth2.id_token.verify_oauth2_token')
    def test_a_forged_token_is_refused(self, verify):
        verify.side_effect = ValueError('bad token')

        response = self.client.post(self.URL, {'id_token': 'forged'}, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_a_missing_token_is_a_400_not_a_crash(self):
        self.assertEqual(self.client.post(self.URL, {}, format='json').status_code, 400)

    @override_settings(
        GOOGLE_OAUTH2_CLIENT_ID='', GOOGLE_OAUTH2_IOS_CLIENT_ID='',
        GOOGLE_OAUTH2_ANDROID_CLIENT_ID='', CACHES=DUMMY_CACHE,
    )
    def test_an_unconfigured_server_says_so_rather_than_blaming_the_token(self):
        response = self.client.post(self.URL, {'id_token': 'x'}, format='json')
        self.assertEqual(response.status_code, 503)


@override_settings(CACHES=DUMMY_CACHE, DEBUG_OTP=True)
class PasswordResetCodeTests(TestCase):
    REQUEST = '/api/auth/password-reset/request/'
    CONFIRM = '/api/auth/password-reset/confirm/'

    def setUp(self):
        self.user = User.objects.create_user(
            email='reset@test.com', password='OldPassw0rd!', name='R',
        )
        self.client = APIClient()

    def _request_code(self):
        response = self.client.post(self.REQUEST, {'email': 'reset@test.com'}, format='json')
        return response, OTPCode.objects.filter(
            user=self.user, purpose='password_reset',
        ).latest('created_at')

    def test_a_code_is_issued_and_resets_the_password(self):
        _, otp = self._request_code()

        response = self.client.post(self.CONFIRM, {
            'email': 'reset@test.com', 'code': otp.code, 'new_password': 'BrandNewPw1!',
        }, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPw1!'))

    def test_an_unknown_address_gets_the_same_answer(self):
        """The response must not be a way to discover who has an account."""
        known = self.client.post(self.REQUEST, {'email': 'reset@test.com'}, format='json')
        unknown = self.client.post(self.REQUEST, {'email': 'nobody@test.com'}, format='json')

        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.data['detail'], unknown.data['detail'])

    def test_a_wrong_code_is_refused(self):
        self._request_code()

        response = self.client.post(self.CONFIRM, {
            'email': 'reset@test.com', 'code': '000000', 'new_password': 'BrandNewPw1!',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPassw0rd!'))

    def test_a_code_cannot_be_used_twice(self):
        _, otp = self._request_code()
        payload = {
            'email': 'reset@test.com', 'code': otp.code, 'new_password': 'BrandNewPw1!',
        }
        self.client.post(self.CONFIRM, payload, format='json')

        again = self.client.post(self.CONFIRM, payload, format='json')

        self.assertEqual(again.status_code, 400)

    def test_a_weak_password_is_refused(self):
        _, otp = self._request_code()

        response = self.client.post(self.CONFIRM, {
            'email': 'reset@test.com', 'code': otp.code, 'new_password': '123',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('new_password', response.data)

    def test_the_emailed_link_flow_still_works(self):
        """The website's reset must not have been traded away for the app's."""
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        response = self.client.post(self.CONFIRM, {
            'uid': urlsafe_base64_encode(force_bytes(self.user.pk)),
            'token': default_token_generator.make_token(self.user),
            'new_password': 'LinkPassw0rd!',
            'confirm_password': 'LinkPassw0rd!',
        }, format='json')

        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('LinkPassw0rd!'))


@override_settings(CACHES=DUMMY_CACHE, DEBUG_OTP=True)
class VerificationFlagTests(TestCase):
    def test_me_exposes_both_spellings_of_the_verification_flags(self):
        user = User.objects.create_user(email='me@test.com', password='pw12345!', name='M')
        user.is_email_verified = True
        user.save()
        client = APIClient()
        client.force_authenticate(user)

        response = client.get('/api/me/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['email_verified'])
        self.assertFalse(response.data['phone_verified'])
        # The originals stay, so nothing written against them breaks.
        self.assertTrue(response.data['is_email_verified'])

    def test_phone_otp_marks_the_number_verified(self):
        """The SMS backend is the console stub until a provider is wired up."""
        user = User.objects.create_user(email='otp@test.com', password='pw12345!', name='O')
        client = APIClient()
        client.force_authenticate(user)

        sent = client.post(
            '/api/auth/otp/send-verification/', {'phone': '0551234567'}, format='json',
        )
        self.assertEqual(sent.status_code, 200, sent.data)

        otp = OTPCode.objects.filter(user=user, purpose='phone_verification').latest('created_at')
        verified = client.post('/api/auth/otp/verify-phone/', {'code': otp.code}, format='json')

        self.assertEqual(verified.status_code, 200, verified.data)
        user.refresh_from_db()
        self.assertTrue(user.is_phone_verified)
        self.assertTrue(client.get('/api/me/').data['phone_verified'])

    def test_email_otp_marks_the_address_verified(self):
        user = User.objects.create_user(email='eotp@test.com', password='pw12345!', name='E')
        client = APIClient()
        client.force_authenticate(user)

        client.post('/api/auth/otp/send-email-verification/', {}, format='json')
        otp = OTPCode.objects.filter(user=user, purpose='email_verification').latest('created_at')
        verified = client.post('/api/auth/otp/verify-email/', {'code': otp.code}, format='json')

        self.assertEqual(verified.status_code, 200, verified.data)
        user.refresh_from_db()
        self.assertTrue(user.is_email_verified)

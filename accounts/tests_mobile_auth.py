"""
Tests for mobile token-based authentication endpoints.
These must NEVER set auth cookies and must return tokens in the body.
"""
from django.conf import settings
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User


def _create_verified_user(email='mobile@test.com', password='TestPass123!', name='Mobile User'):
    user = User.objects.create_user(email=email, password=password, name=name)
    user.is_email_verified = True
    user.save()
    return user


class MobileLoginTest(APITestCase):
    def setUp(self):
        self.user = _create_verified_user()

    def test_login_returns_tokens_in_body(self):
        resp = self.client.post('/api/auth/mobile/login/', {
            'email': 'mobile@test.com', 'password': 'TestPass123!',
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)
        self.assertIn('user', resp.data)
        self.assertEqual(resp.data['user']['email'], 'mobile@test.com')

    def test_login_sets_no_auth_cookies(self):
        resp = self.client.post('/api/auth/mobile/login/', {
            'email': 'mobile@test.com', 'password': 'TestPass123!',
        }, format='json')
        cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access_token')
        refresh_name = settings.SIMPLE_JWT.get('AUTH_COOKIE_REFRESH', 'refresh_token')
        self.assertNotIn(cookie_name, resp.cookies)
        self.assertNotIn(refresh_name, resp.cookies)

    def test_login_invalid_credentials(self):
        resp = self.client.post('/api/auth/mobile/login/', {
            'email': 'mobile@test.com', 'password': 'wrong',
        }, format='json')
        self.assertEqual(resp.status_code, 401)

    def test_login_unverified_email(self):
        self.user.is_email_verified = False
        self.user.save()
        resp = self.client.post('/api/auth/mobile/login/', {
            'email': 'mobile@test.com', 'password': 'TestPass123!',
        }, format='json')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get('code'), 'email_not_verified')


class MobileRefreshTest(APITestCase):
    def setUp(self):
        self.user = _create_verified_user()
        self.refresh = RefreshToken.for_user(self.user)

    def test_refresh_rotates_and_returns_new_tokens(self):
        resp = self.client.post('/api/auth/mobile/refresh/', {
            'refresh': str(self.refresh),
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)
        # New refresh is different from old
        self.assertNotEqual(resp.data['refresh'], str(self.refresh))

    def test_refresh_blacklists_old_token(self):
        resp = self.client.post('/api/auth/mobile/refresh/', {
            'refresh': str(self.refresh),
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        # Using the old token again must fail
        resp2 = self.client.post('/api/auth/mobile/refresh/', {
            'refresh': str(self.refresh),
        }, format='json')
        self.assertEqual(resp2.status_code, 401)

    def test_blacklisted_refresh_is_rejected(self):
        self.refresh.blacklist()
        resp = self.client.post('/api/auth/mobile/refresh/', {
            'refresh': str(self.refresh),
        }, format='json')
        self.assertEqual(resp.status_code, 401)


class MobileLogoutTest(APITestCase):
    def setUp(self):
        self.user = _create_verified_user()
        self.refresh = RefreshToken.for_user(self.user)

    def test_logout_blacklists_token(self):
        resp = self.client.post('/api/auth/mobile/logout/', {
            'refresh': str(self.refresh),
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        # Token should now be blacklisted
        resp2 = self.client.post('/api/auth/mobile/refresh/', {
            'refresh': str(self.refresh),
        }, format='json')
        self.assertEqual(resp2.status_code, 401)


class MobileRegisterTest(APITestCase):
    def test_register_returns_tokens_in_body(self):
        resp = self.client.post('/api/auth/mobile/register/', {
            'email': 'newmobile@test.com',
            'password': 'StrongPass99!',
            'password2': 'StrongPass99!',
            'name': 'New Mobile',
        }, format='json')
        self.assertIn(resp.status_code, (200, 201), msg=f"Register failed: {resp.data}")
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)
        self.assertTrue(resp.data['requires_verification'])


class MobileThrottleTest(APITestCase):
    def test_throttle_triggers(self):
        from django.core.cache import cache
        cache.clear()

        from accounts.mobile_views import MobileAuthThrottle
        MobileAuthThrottle.THROTTLE_RATES['mobile_auth'] = '2/hour'
        try:
            statuses = []
            for _ in range(4):
                resp = self.client.post('/api/auth/mobile/login/', {
                    'email': 'x@test.com', 'password': 'x',
                }, format='json')
                statuses.append(resp.status_code)
            self.assertIn(429, statuses)
        finally:
            MobileAuthThrottle.THROTTLE_RATES['mobile_auth'] = '30/hour'
            cache.clear()

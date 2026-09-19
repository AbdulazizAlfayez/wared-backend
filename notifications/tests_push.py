"""
Push: registering a phone, and a notification reaching it.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from notifications.models import Device, Notification, NotificationPreference
from notifications.push import looks_like_expo_token

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}
TOKEN = 'ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]'
OTHER_TOKEN = 'ExponentPushToken[yyyyyyyyyyyyyyyyyyyyyy]'


def _user(email):
    return User.objects.create_user(email=email, password='pw12345!', name='N')


@override_settings(CACHES=DUMMY_CACHE)
class DeviceRegistrationTests(TestCase):
    def setUp(self):
        self.user = _user('device@test.com')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_a_phone_registers_for_push(self):
        response = self.client.post(
            '/api/devices/', {'expo_push_token': TOKEN, 'platform': 'ios'}, format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(Device.objects.filter(user=self.user, expo_push_token=TOKEN).exists())

    def test_registering_twice_does_not_duplicate_the_phone(self):
        for _ in range(2):
            self.client.post(
                '/api/devices/', {'expo_push_token': TOKEN, 'platform': 'ios'}, format='json',
            )

        self.assertEqual(Device.objects.filter(expo_push_token=TOKEN).count(), 1)

    def test_a_token_follows_the_account_that_registered_it_last(self):
        """
        Otherwise a shared phone keeps delivering the previous user's
        notifications to whoever signs in next.
        """
        self.client.post(
            '/api/devices/', {'expo_push_token': TOKEN, 'platform': 'ios'}, format='json',
        )

        second = APIClient()
        second_user = _user('second@test.com')
        second.force_authenticate(second_user)
        second.post('/api/devices/', {'expo_push_token': TOKEN, 'platform': 'ios'}, format='json')

        self.assertEqual(Device.objects.get(expo_push_token=TOKEN).user, second_user)
        self.assertEqual(Device.objects.filter(expo_push_token=TOKEN).count(), 1)

    def test_rubbish_is_refused_before_it_reaches_expo(self):
        response = self.client.post(
            '/api/devices/', {'expo_push_token': 'not-a-token', 'platform': 'ios'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('expo_push_token', response.data)

    def test_signing_out_removes_the_phone(self):
        self.client.post(
            '/api/devices/', {'expo_push_token': TOKEN, 'platform': 'ios'}, format='json',
        )

        response = self.client.delete(
            '/api/devices/', {'expo_push_token': TOKEN}, format='json',
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Device.objects.filter(expo_push_token=TOKEN).exists())

    def test_a_phone_you_do_not_own_is_not_yours_to_remove(self):
        Device.objects.create(user=_user('owner@test.com'), expo_push_token=TOKEN, platform='ios')

        response = self.client.delete(
            '/api/devices/', {'expo_push_token': TOKEN}, format='json',
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Device.objects.filter(expo_push_token=TOKEN).exists())

    def test_registration_needs_a_signed_in_user(self):
        anon = APIClient()
        response = anon.post(
            '/api/devices/', {'expo_push_token': TOKEN, 'platform': 'ios'}, format='json',
        )
        self.assertIn(response.status_code, (401, 403))


@override_settings(CACHES=DUMMY_CACHE)
class PushDeliveryTests(TestCase):
    def setUp(self):
        self.user = _user('push@test.com')
        Device.objects.create(user=self.user, expo_push_token=TOKEN, platform='ios')

    def _notification(self):
        return Notification.objects.create(
            recipient=self.user, notification_type='system',
            title='Reservation accepted', message='Your car is on its way.',
        )

    @patch('notifications.push.requests.post')
    def test_a_notification_is_delivered_to_the_phone(self, post):
        post.return_value.json.return_value = {'data': [{'status': 'ok'}]}
        post.return_value.raise_for_status.return_value = None
        from notifications.tasks import send_push_for_notification

        result = send_push_for_notification(self._notification().pk)

        self.assertIn('sent to 1', result)
        body = post.call_args.kwargs['json']
        self.assertEqual(body[0]['to'], TOKEN)
        # The push says exactly what the in-app row says.
        self.assertEqual(body[0]['title'], 'Reservation accepted')

    @patch('notifications.push.requests.post')
    def test_opting_out_of_push_stops_it(self, post):
        prefs, _ = NotificationPreference.objects.get_or_create(user=self.user)
        prefs.push_notifications = False
        prefs.save()
        from notifications.tasks import send_push_for_notification

        result = send_push_for_notification(self._notification().pk)

        self.assertEqual(result, 'push disabled')
        post.assert_not_called()

    @patch('notifications.push.requests.post')
    def test_an_uninstalled_app_stops_being_chased(self, post):
        """A dead token would otherwise ride along on every future send."""
        post.return_value.json.return_value = {
            'data': [{'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}]
        }
        post.return_value.raise_for_status.return_value = None
        from notifications.tasks import send_push_for_notification

        send_push_for_notification(self._notification().pk)

        self.assertFalse(Device.objects.get(expo_push_token=TOKEN).is_active)

    @patch('notifications.push.requests.post')
    def test_a_user_with_no_phone_is_not_an_error(self, post):
        Device.objects.all().delete()
        from notifications.tasks import send_push_for_notification

        self.assertEqual(send_push_for_notification(self._notification().pk), 'no devices')
        post.assert_not_called()

    @patch('notifications.tasks.send_push_for_notification.delay')
    def test_notify_queues_a_push(self, delay):
        from notifications.utils import notify

        notification = notify(
            recipient=self.user, notification_type='system',
            title='t', message='m',
        )

        delay.assert_called_once_with(notification.pk)

    @patch('notifications.tasks.send_push_for_notification.delay')
    def test_a_type_the_user_muted_never_reaches_the_queue(self, delay):
        prefs, _ = NotificationPreference.objects.get_or_create(user=self.user)
        prefs.new_message = False
        prefs.save()
        from notifications.utils import notify

        self.assertIsNone(
            notify(recipient=self.user, notification_type='new_message', title='t', message='m'),
        )
        delay.assert_not_called()

    def test_token_shape_check(self):
        self.assertTrue(looks_like_expo_token(TOKEN))
        self.assertFalse(looks_like_expo_token('ExponentPushToken[unterminated'))
        self.assertFalse(looks_like_expo_token(''))


@override_settings(CACHES=DUMMY_CACHE)
class PreferenceExposureTests(TestCase):
    def test_the_push_toggles_are_readable_and_writable(self):
        user = _user('prefs@test.com')
        client = APIClient()
        client.force_authenticate(user)

        response = client.get('/api/notifications/preferences/')
        self.assertEqual(response.status_code, 200)
        for field in ('push_notifications', 'order_updates_push'):
            self.assertIn(field, response.data)

        patched = client.patch(
            '/api/notifications/preferences/', {'push_notifications': False}, format='json',
        )
        self.assertEqual(patched.status_code, 200)
        self.assertFalse(
            NotificationPreference.objects.get(user=user).push_notifications,
        )

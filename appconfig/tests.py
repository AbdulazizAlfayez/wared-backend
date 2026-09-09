from rest_framework.test import APITestCase


class ConfigEndpointTest(APITestCase):
    def test_config_unauthenticated(self):
        resp = self.client.get('/api/config/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('min_supported_version', resp.data)
        self.assertIn('latest_version', resp.data)
        self.assertIn('maintenance_mode', resp.data)
        self.assertIn('features', resp.data)
        self.assertFalse(resp.data['maintenance_mode'])

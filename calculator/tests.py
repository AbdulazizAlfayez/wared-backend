from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import ExchangeRate
from .services import calculate_import_cost


def seed_rates():
    """Create the standard exchange rates used in tests."""
    defaults = {
        'usd': '3.7500',
        'aed': '1.0200',
        'jpy': '0.0250',
        'krw': '0.0029',
        'eur': '4.1000',
        'cad': '2.7500',
        'gbp': '4.7500',
        'qar': '1.0300',
    }
    for currency, rate in defaults.items():
        ExchangeRate.objects.get_or_create(
            currency=currency,
            defaults={'rate_to_sar': Decimal(rate), 'source': 'test'},
        )


class ExchangeRateEndpointTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        seed_rates()

    def test_exchange_rates_returns_all_seeded(self):
        url = '/api/calculator/exchange-rates/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        currencies = {r['currency'] for r in data}
        expected = {'usd', 'aed', 'jpy', 'krw', 'eur', 'cad', 'gbp', 'qar'}
        self.assertEqual(currencies, expected)

    def test_exchange_rates_no_auth_required(self):
        # No credentials — should still return 200
        url = '/api/calculator/exchange-rates/'
        response = APIClient().get(url)
        self.assertEqual(response.status_code, 200)


class CostEstimateEndpointTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        seed_rates()

    def test_usa_16000_correct_math(self):
        url = '/api/calculator/estimate/?source_country=usa&car_value=16000'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # car_price_sar = 16000 * 3.75 = 60000
        self.assertEqual(Decimal(data['car_price_sar']), Decimal('60000.00'))
        # customs_duty = 60000 * 0.05 = 3000
        self.assertEqual(Decimal(data['customs_duty_amount']), Decimal('3000.00'))
        # vat = (60000 + 3000) * 0.15 = 9450
        self.assertEqual(Decimal(data['vat_amount']), Decimal('9450.00'))
        # shipping usa: 4000–8000
        self.assertEqual(Decimal(data['shipping_estimate_low']),  Decimal('4000.00'))
        self.assertEqual(Decimal(data['shipping_estimate_high']), Decimal('8000.00'))
        # totals: 60000 + 3000 + 9450 + 800 + 500 + 500 + shipping
        self.assertEqual(Decimal(data['total_estimate_low']),  Decimal('78250.00'))
        self.assertEqual(Decimal(data['total_estimate_high']), Decimal('82250.00'))
        # source currency defaults to usd for usa
        self.assertEqual(data['source_currency'], 'usd')
        self.assertIsNone(data['age_restriction_warning'])

    def test_missing_source_country_returns_400(self):
        url = '/api/calculator/estimate/?car_value=16000'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertIn('source_country', response.json())

    def test_missing_car_value_returns_400(self):
        url = '/api/calculator/estimate/?source_country=usa'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)
        self.assertIn('car_value', response.json())

    def test_car_older_than_5_years_returns_warning(self):
        from datetime import date
        old_year = date.today().year - 6
        url = f'/api/calculator/estimate/?source_country=japan&car_value=5000&year={old_year}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()['age_restriction_warning'])
        self.assertIn('5 years', response.json()['age_restriction_warning'])

    def test_car_within_5_years_no_warning(self):
        from datetime import date
        recent_year = date.today().year - 3
        url = f'/api/calculator/estimate/?source_country=japan&car_value=5000&year={recent_year}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['age_restriction_warning'])

    def test_rates_used_correctly_uae(self):
        url = '/api/calculator/estimate/?source_country=uae&car_value=50000'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # uae → aed: 50000 * 1.02 = 51000
        self.assertEqual(Decimal(data['car_price_sar']), Decimal('51000.00'))
        self.assertEqual(data['source_currency'], 'aed')

    def test_explicit_source_currency_override(self):
        # Force usd for a uae car
        url = '/api/calculator/estimate/?source_country=uae&car_value=10000&source_currency=usd'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # usd rate: 3.75 → 10000 * 3.75 = 37500
        self.assertEqual(Decimal(data['car_price_sar']), Decimal('37500.00'))
        self.assertEqual(data['source_currency'], 'usd')

    def test_unknown_currency_returns_400(self):
        url = '/api/calculator/estimate/?source_country=usa&car_value=10000&source_currency=xyz'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 400)

    def test_breakdown_keys_present(self):
        url = '/api/calculator/estimate/?source_country=canada&car_value=20000'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        breakdown = response.json()['breakdown']
        for key in ('car_price', 'customs', 'vat', 'shipping_low',
                    'shipping_high', 'inspection', 'port_handling', 'transportation'):
            self.assertIn(key, breakdown, f"Missing breakdown key: {key}")

    def test_no_auth_required(self):
        url = '/api/calculator/estimate/?source_country=usa&car_value=10000'
        response = APIClient().get(url)
        self.assertEqual(response.status_code, 200)


# ============================================================================
# Exchange Rate Auto-Update Tests
# ============================================================================

from unittest.mock import patch, MagicMock
from django.test import override_settings


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=False)
class ExchangeRateUpdateTaskTest(TestCase):

    def setUp(self):
        ExchangeRate.objects.all().delete()
        ExchangeRate.objects.create(currency='usd', rate_to_sar=Decimal('3.750000'), source='api')
        ExchangeRate.objects.create(currency='eur', rate_to_sar=Decimal('4.100000'), source='api')
        ExchangeRate.objects.create(currency='jpy', rate_to_sar=Decimal('0.025000'), source='api')
        ExchangeRate.objects.create(currency='aed', rate_to_sar=Decimal('1.020000'), source='api')
        ExchangeRate.objects.create(currency='krw', rate_to_sar=Decimal('0.002850'), source='api')
        ExchangeRate.objects.create(currency='gbp', rate_to_sar=Decimal('4.700000'), source='api')
        ExchangeRate.objects.create(currency='cad', rate_to_sar=Decimal('2.750000'), source='api')
        ExchangeRate.objects.create(currency='qar', rate_to_sar=Decimal('1.030000'), source='api')

    def _mock_response(self, rates):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'rates': rates}
        resp.raise_for_status = MagicMock()
        return resp

    @patch('calculator.tasks.requests.get')
    def test_update_success(self, mock_get):
        # 1 SAR = 0.2667 USD → 1 USD = 3.7509 SAR
        mock_get.return_value = self._mock_response({
            'USD': 0.2667, 'EUR': 0.2439, 'JPY': 40.0,
            'KRW': 350.0, 'AED': 0.9800, 'GBP': 0.2128, 'CAD': 0.3636,
        })

        from .tasks import update_exchange_rates
        result = update_exchange_rates.apply().result

        self.assertEqual(result['status'], 'success')
        self.assertGreaterEqual(result['updated'], 3)

        usd = ExchangeRate.objects.get(currency='usd')
        self.assertEqual(usd.source, 'api')
        self.assertEqual(usd.last_api_error, '')
        self.assertAlmostEqual(float(usd.rate_to_sar), 1 / 0.2667, places=3)

    @patch('calculator.tasks.requests.get')
    def test_respects_manual_override(self, mock_get):
        ExchangeRate.objects.filter(currency='usd').update(source='manual', rate_to_sar=Decimal('3.800000'))

        mock_get.return_value = self._mock_response({
            'USD': 0.2667, 'EUR': 0.2439, 'JPY': 40.0,
            'KRW': 350.0, 'AED': 0.9800, 'GBP': 0.2128, 'CAD': 0.3636,
        })

        from .tasks import update_exchange_rates
        result = update_exchange_rates.apply().result

        usd = ExchangeRate.objects.get(currency='usd')
        self.assertEqual(usd.source, 'manual')
        self.assertEqual(float(usd.rate_to_sar), 3.8)
        self.assertEqual(result['skipped_manual'], 1)

    @patch('calculator.tasks.requests.get')
    def test_api_failure_keeps_old_rates(self, mock_get):
        """On network failure, rates stay unchanged and error is logged."""
        import requests as req_lib
        mock_get.side_effect = req_lib.ConnectionError("Connection timeout")

        from .tasks import update_exchange_rates
        # Run with max_retries=0 to avoid retry loops in tests
        update_exchange_rates.max_retries = 0
        result = update_exchange_rates.apply().result
        update_exchange_rates.max_retries = 3  # restore

        self.assertEqual(result['status'], 'failed')
        usd = ExchangeRate.objects.get(currency='usd')
        self.assertEqual(float(usd.rate_to_sar), 3.75)
        self.assertIn('Connection timeout', usd.last_api_error)

    @patch('calculator.tasks.requests.get')
    def test_malformed_response(self, mock_get):
        """Empty rates response is treated as failure."""
        mock_get.return_value = self._mock_response({})

        from .tasks import update_exchange_rates
        update_exchange_rates.max_retries = 0
        result = update_exchange_rates.apply().result
        update_exchange_rates.max_retries = 3

        self.assertEqual(result['status'], 'failed')
        usd = ExchangeRate.objects.get(currency='usd')
        self.assertEqual(float(usd.rate_to_sar), 3.75)

    @patch('calculator.tasks.requests.get')
    def test_previous_rate_saved(self, mock_get):
        mock_get.return_value = self._mock_response({
            'USD': 0.2632, 'EUR': 0.2439, 'JPY': 40.0,
            'KRW': 350.0, 'AED': 0.9800, 'GBP': 0.2128, 'CAD': 0.3636,
        })

        from .tasks import update_exchange_rates
        update_exchange_rates.apply()

        usd = ExchangeRate.objects.get(currency='usd')
        self.assertIsNotNone(usd.previous_rate)
        self.assertAlmostEqual(float(usd.previous_rate), 3.75, places=2)
        self.assertNotEqual(float(usd.rate_to_sar), 3.75)

    def test_qar_in_supported(self):
        from .tasks import SUPPORTED_CURRENCIES
        self.assertIn('qar', SUPPORTED_CURRENCIES)

    @patch('calculator.tasks.requests.get')
    def test_qar_updated_from_api(self, mock_get):
        mock_get.return_value = self._mock_response({
            'USD': 0.2667, 'EUR': 0.2439, 'JPY': 40.0,
            'KRW': 350.0, 'AED': 0.9800, 'GBP': 0.2128, 'CAD': 0.3636,
            'QAR': 0.9709,
        })
        from .tasks import update_exchange_rates
        update_exchange_rates.apply()
        qar = ExchangeRate.objects.get(currency='qar')
        self.assertEqual(qar.source, 'api')
        self.assertAlmostEqual(float(qar.rate_to_sar), 1 / 0.9709, places=3)

"""
Pricing is the server's arithmetic now.

Two clients used to compute it independently and disagree: the website summed
the SAR costs and left the car out, the app converted the source price first.
These tests pin the one answer, and the refusals that keep a wrong one from
being stored.
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from calculator.models import ExchangeRate
from cars.models import Listing
from cars.pricing import MissingExchangeRate, compute_pricing

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}


def _importer(email='pricing@test.com'):
    return User.objects.create_user(
        email=email, password='pw12345!', name='Importer', role='importer',
    )


@override_settings(CACHES=DUMMY_CACHE)
class ComputedPricingTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.update_or_create(
            currency='usd', defaults={'rate_to_sar': Decimal('3.750000')},
        )
        ExchangeRate.objects.update_or_create(
            currency='krw', defaults={'rate_to_sar': Decimal('0.002480')},
        )
        self.owner = _importer()

    def _listing(self, **overrides):
        data = dict(
            title='2023 Toyota Camry', make='Toyota', model='Camry', year=2023,
            mileage=14500, city='Riyadh', price=1, owner=self.owner,
        )
        data.update(overrides)
        return Listing.objects.create(**data)

    def test_source_price_is_converted_before_the_costs_are_added(self):
        listing = self._listing(
            source_price=Decimal('25000'), source_currency='usd',
            shipping_cost=Decimal('5500'), customs_duty_amount=Decimal('10500'),
            vat_amount=Decimal('18000'), margin_sar=Decimal('37250'),
        )
        listing.refresh_from_db()

        self.assertEqual(listing.total_landed_cost, Decimal('127750.00'))
        self.assertEqual(listing.final_price_sar, Decimal('165000.00'))
        self.assertEqual(listing.fx_rate_used, Decimal('3.750000'))

    def test_price_follows_the_computed_final_price(self):
        """The card reads `price`; the two drifting apart mis-advertises a car."""
        listing = self._listing(
            source_price=Decimal('10000'), source_currency='usd',
            margin_sar=Decimal('5000'), price=Decimal('999999'),
        )
        listing.refresh_from_db()

        self.assertEqual(listing.price, listing.final_price_sar)
        self.assertEqual(listing.price, Decimal('42500.00'))

    def test_a_foreign_price_is_never_summed_raw(self):
        """
        The bug this module exists to stop: 75,000,000 KRW added straight into
        the SAR costs reported a SAR 254,256 car as SAR 75,050,506.
        """
        listing = self._listing(
            source_price=Decimal('75000000'), source_currency='krw',
            shipping_cost=Decimal('5500'), customs_duty_amount=Decimal('10500'),
            vat_amount=Decimal('31406.25'), inspection_fee=Decimal('2000'),
            transportation_cost=Decimal('1100'),
        )
        listing.refresh_from_db()

        naive = Decimal('75000000') + Decimal('50506.25')
        self.assertNotEqual(listing.total_landed_cost, naive)
        self.assertEqual(listing.total_landed_cost, Decimal('236506.25'))

    def test_margin_is_the_only_thing_between_cost_and_price(self):
        listing = self._listing(
            source_price=Decimal('10000'), source_currency='usd',
            margin_sar=Decimal('7500'),
        )
        listing.refresh_from_db()
        self.assertEqual(
            listing.final_price_sar, listing.total_landed_cost + Decimal('7500'),
        )

    def test_a_listing_with_no_import_pricing_is_left_alone(self):
        """A plain local car has no landed cost, and a zero would be a lie."""
        listing = self._listing(price=Decimal('50000'))
        listing.refresh_from_db()

        self.assertIsNone(listing.total_landed_cost)
        self.assertEqual(listing.price, Decimal('50000'))

    def test_an_unconvertible_currency_is_refused_rather_than_understated(self):
        ExchangeRate.objects.filter(currency='jpy').delete()
        listing = Listing(
            title='x', make='a', model='b', year=2023, mileage=1, city='Riyadh',
            price=1, owner=self.owner,
            source_price=Decimal('1000'), source_currency='jpy',
        )
        with self.assertRaises(MissingExchangeRate):
            compute_pricing(listing)

    def test_the_rate_used_is_stamped_so_the_total_can_be_explained_later(self):
        listing = self._listing(source_price=Decimal('1000'), source_currency='usd')
        listing.refresh_from_db()

        self.assertEqual(listing.fx_rate_used, Decimal('3.750000'))
        self.assertIsNotNone(listing.fx_rate_date)


@override_settings(CACHES=DUMMY_CACHE)
class PricingApiTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.update_or_create(
            currency='usd', defaults={'rate_to_sar': Decimal('3.750000')},
        )
        self.owner = _importer('api@test.com')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_the_client_cannot_set_the_computed_fields(self):
        response = self.client.post('/api/listings/', {
            'title': '2023 Toyota Camry', 'make': 'Toyota', 'model': 'Camry',
            'year': 2023, 'mileage': 14500, 'city': 'Riyadh',
            'source_price': '10000', 'source_currency': 'usd',
            'margin_sar': '5000',
            # Both ignored: the server derives them.
            'total_landed_cost': '1', 'final_price_sar': '1',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['total_landed_cost']), Decimal('37500.00'))
        self.assertEqual(Decimal(response.data['final_price_sar']), Decimal('42500.00'))

    def test_price_is_not_required_when_it_can_be_derived(self):
        response = self.client.post('/api/listings/', {
            'title': 'Derived', 'make': 'Toyota', 'model': 'Camry', 'year': 2023,
            'mileage': 100, 'city': 'Riyadh',
            'source_price': '10000', 'source_currency': 'usd', 'margin_sar': '5000',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Decimal(response.data['price']), Decimal('42500.00'))

    def test_price_is_still_required_without_import_pricing(self):
        response = self.client.post('/api/listings/', {
            'title': 'Local', 'make': 'Toyota', 'model': 'Camry', 'year': 2023,
            'mileage': 100, 'city': 'Riyadh',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('price', response.data)

    def test_a_computed_price_outside_the_accepted_range_is_refused(self):
        response = self.client.post('/api/listings/', {
            'title': 'Too much', 'make': 'Toyota', 'model': 'Camry', 'year': 2023,
            'mileage': 100, 'city': 'Riyadh',
            'source_price': '10000', 'source_currency': 'usd',
            'margin_sar': '99000000',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        # The message names the field the importer can actually move.
        self.assertIn('margin_sar', response.data)

    def test_calculated_total_is_gone_from_the_breakdown(self):
        listing = Listing.objects.create(
            title='x', make='a', model='b', year=2023, mileage=1, city='Riyadh',
            price=Decimal('50000'), owner=self.owner,
            source_price=Decimal('10000'), source_currency='usd',
        )
        response = self.client.get(f'/api/listings/{listing.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('calculated_total', response.data['cost_breakdown'])
        self.assertIn('total_landed_cost', response.data['cost_breakdown'])

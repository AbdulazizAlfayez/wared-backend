"""
The price is the importer's number again.

Phase 4 made it a formula — landed cost + margin — and the formula priced cars
the importers had not agreed to. `final_price_sar` is writable, `price`
mirrors it, and the cost lines are informational: they feed the buyer's cost
breakdown and never move the price.

What survives from Phase 4 is the arithmetic of `total_landed_cost`, including
the bug it was written to stop: a foreign source price must be converted
BEFORE the SAR costs are added.
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from calculator.models import ExchangeRate
from cars.models import Listing
from cars.pricing import compute_landed_cost

DUMMY_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}


def _importer(email='pricing@test.com'):
    return User.objects.create_user(
        email=email, password='pw12345!', name='Importer', role='importer',
    )


@override_settings(CACHES=DUMMY_CACHE)
class ImporterEnteredPriceTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.update_or_create(
            currency='usd', defaults={'rate_to_sar': Decimal('3.750000')},
        )
        self.owner = _importer()

    def _listing(self, **overrides):
        data = dict(
            title='2023 Toyota Camry', make='Toyota', model='Camry', year=2023,
            mileage=14500, city='Riyadh', price=Decimal('165000'), owner=self.owner,
        )
        data.update(overrides)
        return Listing.objects.create(**data)

    def test_the_costs_do_not_move_the_price(self):
        listing = self._listing(
            final_price_sar=Decimal('165000'),
            source_price=Decimal('25000'), source_currency='usd',
            shipping_cost=Decimal('5500'), customs_duty_amount=Decimal('10500'),
            vat_amount=Decimal('18000'),
        )
        listing.refresh_from_db()

        self.assertEqual(listing.final_price_sar, Decimal('165000.00'))
        self.assertEqual(listing.price, Decimal('165000.00'))
        # Informational only, and unrelated to the asking price.
        self.assertEqual(listing.total_landed_cost, Decimal('127750.00'))

    def test_editing_a_cost_line_leaves_the_price_alone(self):
        listing = self._listing(
            final_price_sar=Decimal('165000'), shipping_cost=Decimal('5500'),
        )
        listing.shipping_cost = Decimal('9000')
        listing.save()
        listing.refresh_from_db()

        self.assertEqual(listing.final_price_sar, Decimal('165000.00'))
        self.assertEqual(listing.total_landed_cost, Decimal('9000.00'))

    def test_margin_sar_is_gone(self):
        self.assertFalse(hasattr(Listing, 'margin_sar'))
        self.assertNotIn(
            'margin_sar', [field.name for field in Listing._meta.get_fields()],
        )

    def test_fx_stamps_are_gone(self):
        names = [field.name for field in Listing._meta.get_fields()]
        self.assertNotIn('fx_rate_used', names)
        self.assertNotIn('fx_rate_date', names)


@override_settings(CACHES=DUMMY_CACHE)
class LandedCostTests(TestCase):
    """The informational total, and the conversion-order bug it exists to stop."""

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
            mileage=14500, city='Riyadh', price=Decimal('165000'),
            final_price_sar=Decimal('165000'), owner=self.owner,
        )
        data.update(overrides)
        return Listing.objects.create(**data)

    def test_source_price_is_converted_before_the_costs_are_added(self):
        listing = self._listing(
            source_price=Decimal('25000'), source_currency='usd',
            shipping_cost=Decimal('5500'), customs_duty_amount=Decimal('10500'),
            vat_amount=Decimal('18000'),
        )
        listing.refresh_from_db()
        self.assertEqual(listing.total_landed_cost, Decimal('127750.00'))

    def test_a_foreign_price_is_never_summed_raw(self):
        """75,000,000 KRW added raw would read as a SAR 75 million car."""
        listing = self._listing(
            source_price=Decimal('75000000'), source_currency='krw',
            shipping_cost=Decimal('4000'),
        )
        listing.refresh_from_db()
        self.assertEqual(listing.total_landed_cost, Decimal('190000.00'))

    def test_no_import_pricing_leaves_the_total_null(self):
        listing = self._listing()
        listing.refresh_from_db()
        self.assertIsNone(listing.total_landed_cost)
        self.assertEqual(listing.final_price_sar, Decimal('165000.00'))

    def test_an_unconvertible_currency_leaves_the_total_null_but_saves(self):
        """Informational: never worth refusing a listing over."""
        # Every currency ships with a seeded rate; this is an admin having
        # removed one.
        ExchangeRate.objects.filter(currency='jpy').delete()
        listing = self._listing(
            source_price=Decimal('20000'), source_currency='jpy',
            shipping_cost=Decimal('4000'),
        )
        listing.refresh_from_db()
        self.assertIsNone(listing.total_landed_cost)
        self.assertEqual(listing.final_price_sar, Decimal('165000.00'))
        self.assertEqual(listing.price, Decimal('165000.00'))

    def test_compute_is_pure(self):
        listing = self._listing(shipping_cost=Decimal('4000'))
        self.assertEqual(compute_landed_cost(listing), Decimal('4000.00'))


@override_settings(CACHES=DUMMY_CACHE)
class PricingApiTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.update_or_create(
            currency='usd', defaults={'rate_to_sar': Decimal('3.750000')},
        )
        self.owner = _importer('api-pricing@test.com')
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    PAYLOAD = {
        'title': '2023 Toyota Camry', 'make': 'Toyota', 'model': 'Camry',
        'year': 2023, 'mileage': 14500, 'city': 'Riyadh',
    }

    def _post(self, **overrides):
        payload = dict(self.PAYLOAD)
        payload.update(overrides)
        return self.client.post('/api/listings/', payload, format='json')

    def test_the_importer_sets_the_price(self):
        resp = self._post(final_price_sar='165000', shipping_cost='5500')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Decimal(resp.data['final_price_sar']), Decimal('165000.00'))
        self.assertEqual(Decimal(resp.data['price']), Decimal('165000.00'))
        self.assertEqual(Decimal(resp.data['total_landed_cost']), Decimal('5500.00'))

    def test_price_alone_still_works_and_mirrors_into_final(self):
        resp = self._post(price='165000')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Decimal(resp.data['final_price_sar']), Decimal('165000.00'))
        self.assertEqual(Decimal(resp.data['price']), Decimal('165000.00'))

    def test_cost_lines_stay_writable_and_informational(self):
        resp = self._post(
            final_price_sar='165000', source_price='25000', source_currency='usd',
            shipping_cost='5500', customs_duty_amount='10500', vat_amount='18000',
            inspection_fee='1000', transportation_cost='750',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        breakdown = resp.data['cost_breakdown']
        self.assertEqual(Decimal(breakdown['shipping_cost']), Decimal('5500.00'))
        self.assertEqual(Decimal(breakdown['inspection_fee']), Decimal('1000.00'))
        # Still the importer's price, not the sum.
        self.assertEqual(Decimal(resp.data['final_price_sar']), Decimal('165000.00'))
        self.assertEqual(Decimal(resp.data['total_landed_cost']), Decimal('129500.00'))

    def test_price_is_required_on_submit(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('final_price_sar', resp.data)

    def test_price_is_not_required_on_a_draft(self):
        resp = self._post(status='draft')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['status'], 'draft')
        self.assertIsNone(resp.data['final_price_sar'])

    def test_a_draft_cannot_be_submitted_without_a_price(self):
        listing = self._post(status='draft').data
        resp = self.client.post(f"/api/listings/{listing['id']}/submit/")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn('final_price_sar', resp.data)

    def test_a_draft_with_a_price_submits(self):
        listing = self._post(status='draft', final_price_sar='165000').data
        resp = self.client.post(f"/api/listings/{listing['id']}/submit/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['status'], 'pending')

    def test_below_the_floor_is_refused(self):
        resp = self._post(final_price_sar='4999')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('final_price_sar', resp.data)

    def test_above_the_ceiling_is_refused(self):
        resp = self._post(final_price_sar='5000001')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('final_price_sar', resp.data)

    def test_the_bounds_are_inclusive(self):
        for value in ('5000', '5000000'):
            with self.subTest(value=value):
                resp = self._post(final_price_sar=value, title=f'Car {value}')
                self.assertEqual(resp.status_code, 201, resp.data)

    def test_editing_the_price_updates_both_fields(self):
        listing = self._post(final_price_sar='165000').data
        resp = self.client.patch(
            f"/api/listings/{listing['id']}/", {'final_price_sar': '150000'}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Decimal(resp.data['final_price_sar']), Decimal('150000.00'))
        self.assertEqual(Decimal(resp.data['price']), Decimal('150000.00'))

    def test_margin_is_not_accepted_any_more(self):
        resp = self._post(final_price_sar='165000', margin_sar='37250')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertNotIn('margin_sar', resp.data)

    def test_calculated_total_is_gone_from_the_breakdown(self):
        resp = self._post(final_price_sar='165000', shipping_cost='5500')
        self.assertNotIn('calculated_total', resp.data['cost_breakdown'])
        self.assertNotIn('margin_sar', resp.data['cost_breakdown'])
        self.assertNotIn('fx_rate_used', resp.data['cost_breakdown'])

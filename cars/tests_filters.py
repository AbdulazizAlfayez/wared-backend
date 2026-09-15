"""
Multi-value listing filters and the filter-options facets.

Cache note: DRF and the facets share the Django cache, and local dev points it
at the same Redis the running server uses. These tests override CACHES rather
than calling `cache.clear()`, which would flush that shared instance.
"""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from cars.filter_options import CACHE_KEY, compute_filter_options
from cars.models import Listing

LIST_URL = '/api/listings/'
OPTIONS_URL = '/api/listings/filter-options/'
LEGACY_OPTIONS_URL = '/api/imported-cars/filter-options/'

#: Isolated per test class, so nothing touches the dev server's Redis.
LOCAL_CACHE = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'filter-tests',
    },
}


def make_user(email, role='importer', name='Importer'):
    return User.objects.create_user(email=email, password='pass1234', name=name, role=role)


def make_listing(owner, **extra):
    """
    Public by default: `public_market_q()` wants status='approved', is_active
    and an import_status outside ('reserved', 'sold'). Only `status` has to be
    passed explicitly — it defaults to 'pending'.
    """
    defaults = dict(
        title='Test Listing',
        make='Toyota',
        model='Camry',
        year=2022,
        price=50000,
        mileage=10000,
        city='Riyadh',
        status='approved',
        condition='used',
        body_type='sedan',
        transmission='automatic',
        fuel_type='petrol',
        import_status='available',
    )
    defaults.update(extra)
    return Listing.objects.create(owner=owner, **defaults)


def ids(response):
    return {row['id'] for row in response.data['results']}


@override_settings(CACHES=LOCAL_CACHE)
class MultiValueFilterTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = make_user('importer@filters.test')

        self.toyota = make_listing(self.owner, make='Toyota', body_type='sedan',
                                   source_country='usa')
        self.nissan = make_listing(self.owner, make='Nissan', body_type='suv',
                                   source_country='japan')
        self.ford = make_listing(self.owner, make='Ford', body_type='truck',
                                 source_country='usa')

    def get(self, query=''):
        return self.client.get(f'{LIST_URL}?page_size=50&{query}')

    # --- single value, unchanged -----------------------------------------

    def test_single_value_still_works(self):
        response = self.get('make=Toyota')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ids(response), {self.toyota.id})

    def test_single_value_is_still_a_substring_match(self):
        # The old filter was `icontains`; a partial must keep working.
        self.assertEqual(ids(self.get('make=Toy')), {self.toyota.id})

    # --- OR within a parameter -------------------------------------------

    def test_two_makes_are_ored(self):
        self.assertEqual(
            ids(self.get('make=Toyota,Nissan')),
            {self.toyota.id, self.nissan.id},
        )

    def test_three_makes_are_ored(self):
        self.assertEqual(
            ids(self.get('make=Toyota,Nissan,Ford')),
            {self.toyota.id, self.nissan.id, self.ford.id},
        )

    def test_choice_field_is_ored(self):
        self.assertEqual(
            ids(self.get('body_type=sedan,suv')),
            {self.toyota.id, self.nissan.id},
        )

    def test_source_country_is_ored(self):
        self.assertEqual(
            ids(self.get('source_country=usa,japan')),
            {self.toyota.id, self.nissan.id, self.ford.id},
        )

    def test_whitespace_around_commas_is_tolerated(self):
        self.assertEqual(
            ids(self.get('make=Toyota , Nissan')),
            {self.toyota.id, self.nissan.id},
        )

    def test_an_unknown_value_simply_matches_nothing(self):
        # A ChoiceFilter would have rejected the whole request; for an OR list
        # the useful answer is "no such cars".
        self.assertEqual(ids(self.get('body_type=spaceship')), set())
        self.assertEqual(ids(self.get('body_type=sedan,spaceship')), {self.toyota.id})

    # --- AND across parameters -------------------------------------------

    def test_different_parameters_are_anded(self):
        # Toyota OR Nissan, AND an SUV -> only the Nissan.
        self.assertEqual(
            ids(self.get('make=Toyota,Nissan&body_type=suv')),
            {self.nissan.id},
        )

    def test_and_across_three_parameters(self):
        self.assertEqual(
            ids(self.get('make=Toyota,Nissan&source_country=usa&body_type=sedan')),
            {self.toyota.id},
        )

    def test_and_can_produce_nothing(self):
        self.assertEqual(ids(self.get('make=Toyota&body_type=suv')), set())

    # --- case-insensitivity ----------------------------------------------

    def test_text_filter_is_case_insensitive(self):
        for value in ('toyota', 'TOYOTA', 'ToYoTa'):
            with self.subTest(value=value):
                self.assertEqual(ids(self.get(f'make={value}')), {self.toyota.id})

    def test_choice_filter_is_case_insensitive(self):
        for value in ('SUV', 'Suv', 'suv'):
            with self.subTest(value=value):
                self.assertEqual(ids(self.get(f'body_type={value}')), {self.nissan.id})

    def test_mixed_case_multi_value(self):
        self.assertEqual(
            ids(self.get('make=toyota,NISSAN')),
            {self.toyota.id, self.nissan.id},
        )

    # --- ranges are untouched --------------------------------------------

    def test_price_and_year_ranges_are_unchanged(self):
        cheap = make_listing(self.owner, make='Kia', price=10000, year=2016)
        self.assertIn(cheap.id, ids(self.get('price_max=20000')))
        self.assertNotIn(self.toyota.id, ids(self.get('price_max=20000')))
        self.assertIn(cheap.id, ids(self.get('year_min=2015&year_max=2017')))


@override_settings(CACHES=LOCAL_CACHE)
class CountEndpointTests(TestCase):
    """
    Section 3: the list endpoint already answers "how many?" cheaply, so no
    separate count endpoint was added.
    """

    def setUp(self):
        self.client = APIClient()
        self.owner = make_user('importer@count.test')
        make_listing(self.owner, make='Toyota')
        make_listing(self.owner, make='Toyota')
        make_listing(self.owner, make='Nissan')

    def test_count_is_returned_with_one_row(self):
        response = self.client.get(f'{LIST_URL}?page_size=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 3)
        self.assertEqual(len(response.data['results']), 1)

    def test_count_respects_the_same_filters(self):
        response = self.client.get(f'{LIST_URL}?page_size=1&make=Toyota')
        self.assertEqual(response.data['count'], 2)

    def test_count_respects_multi_value_filters(self):
        response = self.client.get(f'{LIST_URL}?page_size=1&make=Toyota,Nissan')
        self.assertEqual(response.data['count'], 3)


@override_settings(CACHES=LOCAL_CACHE)
class FilterOptionsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = make_user('importer@options.test')

        make_listing(self.owner, make='Toyota', model='Camry', body_type='sedan',
                     source_country='usa', price=50000, year=2020)
        make_listing(self.owner, make='Toyota', model='Land Cruiser', body_type='suv',
                     source_country='usa', price=300000, year=2024)
        make_listing(self.owner, make='Nissan', model='Patrol', body_type='suv',
                     source_country='japan', price=200000, year=2022)

    def test_shape(self):
        response = self.client.get(OPTIONS_URL)
        self.assertEqual(response.status_code, 200)

        for key in (
            'makes', 'cities', 'source_countries', 'imported_from', 'condition',
            'body_type', 'transmission', 'fuel_type', 'drive_type',
            'import_status', 'price', 'year',
        ):
            self.assertIn(key, response.data)

        make = response.data['makes'][0]
        self.assertEqual(set(make.keys()), {'value', 'label_en', 'label_ar', 'count', 'models'})
        self.assertEqual(
            set(make['models'][0].keys()), {'value', 'label_en', 'label_ar', 'count'}
        )

    def test_is_public(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(OPTIONS_URL).status_code, 200)

    def test_makes_are_sorted_by_count_then_name(self):
        data = self.client.get(OPTIONS_URL).data
        self.assertEqual([m['value'] for m in data['makes']], ['Toyota', 'Nissan'])
        self.assertEqual(data['makes'][0]['count'], 2)

    def test_models_are_nested_under_their_make(self):
        data = self.client.get(OPTIONS_URL).data
        toyota = next(m for m in data['makes'] if m['value'] == 'Toyota')
        self.assertEqual({m['value'] for m in toyota['models']}, {'Camry', 'Land Cruiser'})

    def test_zero_count_options_are_omitted(self):
        data = self.client.get(OPTIONS_URL).data
        # Nothing in the fixture is a convertible, so it must not be offered.
        self.assertNotIn('convertible', [b['value'] for b in data['body_type']])
        for facet in ('body_type', 'condition', 'fuel_type', 'import_status'):
            for option in data[facet]:
                self.assertGreater(option['count'], 0)

    def test_price_and_year_bounds(self):
        data = self.client.get(OPTIONS_URL).data
        self.assertEqual(data['price']['min'], 50000)
        self.assertEqual(data['price']['max'], 300000)
        self.assertEqual(data['price']['step'], 5000)
        self.assertEqual(data['year']['min'], 2020)
        self.assertEqual(data['year']['max'], 2024)

    def test_source_countries_carry_real_arabic_when_a_row_exists(self):
        from source_countries.models import SourceCountry

        # `update_or_create`, not `create`: the reference table may already be
        # seeded by a migration, and under --keepdb it certainly is.
        SourceCountry.objects.update_or_create(
            code='usa',
            defaults={
                'name_en': 'United States',
                'name_ar': 'الولايات المتحدة',
                'iso_code': 'US',
            },
        )
        data = self.client.get(OPTIONS_URL).data
        usa = next(c for c in data['source_countries'] if c['value'] == 'usa')
        self.assertEqual(usa['label_en'], 'United States')
        self.assertEqual(usa['label_ar'], 'الولايات المتحدة')

    def test_cities_take_their_labels_from_the_locations_app(self):
        from locations.models import City, Region

        # Same reasoning as above — the locations tables are reference data.
        region, _ = Region.objects.get_or_create(
            slug='riyadh-region',
            defaults={'name_en': 'Riyadh', 'name_ar': 'الرياض'},
        )
        City.objects.update_or_create(
            slug='riyadh',
            defaults={'region': region, 'name_en': 'Riyadh', 'name_ar': 'الرياض'},
        )

        data = self.client.get(OPTIONS_URL).data
        riyadh = next(c for c in data['cities'] if c['value'] == 'riyadh')
        self.assertEqual(riyadh['label_ar'], 'الرياض')
        self.assertEqual(riyadh['count'], 3)

    def test_a_facet_value_is_usable_as_a_filter(self):
        # The whole point: whatever the facet offers must filter something.
        data = self.client.get(OPTIONS_URL).data
        for facet in ('makes', 'cities', 'body_type', 'source_countries'):
            for option in data[facet]:
                with self.subTest(facet=facet, value=option['value']):
                    param = {'makes': 'make', 'cities': 'city',
                             'source_countries': 'source_country'}.get(facet, facet)
                    response = self.client.get(
                        f"{LIST_URL}?page_size=1&{param}={option['value']}"
                    )
                    self.assertEqual(response.data['count'], option['count'])


@override_settings(CACHES=LOCAL_CACHE)
class FilterOptionsVisibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = make_user('importer@visibility.test')
        make_listing(self.owner, make='Toyota')

    def makes(self):
        return [m['value'] for m in self.client.get(OPTIONS_URL).data['makes']]

    def test_an_unapproved_listing_is_not_offered(self):
        make_listing(self.owner, make='Lamborghini', status='pending')
        self.assertNotIn('Lamborghini', self.makes())

    def test_a_sold_listing_is_not_offered(self):
        make_listing(self.owner, make='Bugatti', import_status='sold')
        self.assertNotIn('Bugatti', self.makes())

    def test_an_inactive_listing_is_not_offered(self):
        make_listing(self.owner, make='Koenigsegg', is_active=False)
        self.assertNotIn('Koenigsegg', self.makes())

    def test_an_approved_listing_is_offered(self):
        make_listing(self.owner, make='Ferrari')
        self.assertIn('Ferrari', self.makes())


@override_settings(CACHES=LOCAL_CACHE)
class FilterOptionsCacheTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = make_user('importer@cache.test')
        make_listing(self.owner, make='Toyota')

    def test_the_response_is_cached(self):
        from django.core.cache import cache

        self.assertIsNone(cache.get(CACHE_KEY))
        self.client.get(OPTIONS_URL)
        self.assertIsNotNone(cache.get(CACHE_KEY))

    def test_saving_a_listing_busts_the_cache(self):
        from django.core.cache import cache

        self.client.get(OPTIONS_URL)
        self.assertIsNotNone(cache.get(CACHE_KEY))

        make_listing(self.owner, make='Ferrari')
        # The post_save hook cleared it, so the next read is recomputed.
        self.assertIsNone(cache.get(CACHE_KEY))

        makes = [m['value'] for m in self.client.get(OPTIONS_URL).data['makes']]
        self.assertIn('Ferrari', makes)

    def test_deleting_a_listing_busts_the_cache(self):
        from django.core.cache import cache

        doomed = make_listing(self.owner, make='Ferrari')
        self.client.get(OPTIONS_URL)
        self.assertIsNotNone(cache.get(CACHE_KEY))

        doomed.delete()
        self.assertIsNone(cache.get(CACHE_KEY))
        self.assertNotIn('Ferrari', [m['value'] for m in self.client.get(OPTIONS_URL).data['makes']])

    def test_compute_bypasses_the_cache(self):
        # The uncached path is what the tests above assert against; make sure
        # it is genuinely independent of whatever is cached.
        make_listing(self.owner, make='Ferrari')
        makes = [m['value'] for m in compute_filter_options()['makes']]
        self.assertIn('Ferrari', makes)


@override_settings(CACHES=LOCAL_CACHE)
class LegacyFilterOptionsTests(TestCase):
    """The older endpoint keeps its shape while sharing the new computation."""

    def setUp(self):
        self.client = APIClient()
        self.owner = make_user('importer@legacy.test')
        make_listing(self.owner, make='Toyota', body_type='sedan')
        make_listing(self.owner, make='Nissan', body_type='suv')

    def test_shape_is_unchanged(self):
        response = self.client.get(LEGACY_OPTIONS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data.keys()),
            {'makes', 'body_types', 'year_range', 'price_range_sar'},
        )

    def test_makes_are_still_bare_strings_sorted_alphabetically(self):
        data = self.client.get(LEGACY_OPTIONS_URL).data
        self.assertEqual(data['makes'], ['Nissan', 'Toyota'])
        self.assertEqual(data['body_types'], ['sedan', 'suv'])

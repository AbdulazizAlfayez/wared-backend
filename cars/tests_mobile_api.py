"""
Tests for mobile-oriented listing API features:
cursor pagination, image variants, lean list payload, and query count.
"""
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import User
from cars.models import Listing, ListingImage
from cars.utils.cloudinary_urls import cloudinary_transform, image_variants


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_importer():
    return User.objects.create_user(
        email='imp@test.com', password='pass123', role='importer', name='Imp',
    )


def _create_listing(owner, **kwargs):
    defaults = dict(
        title='Test Car', make='Toyota', model='Camry', year=2024,
        price=80000, mileage=1000, condition='new', status='approved',
        is_active=True, import_status='available',
    )
    defaults.update(kwargs)
    return Listing.objects.create(owner=owner, **defaults)


# ---------------------------------------------------------------------------
# 1. Cloudinary URL transform helper (unit test)
# ---------------------------------------------------------------------------

class CloudinaryTransformTest(TestCase):
    def test_inserts_transform(self):
        url = 'https://res.cloudinary.com/demo/image/upload/v1/listings/abc.jpg'
        result = cloudinary_transform(url, 'w_160,h_120,c_fill')
        self.assertEqual(
            result,
            'https://res.cloudinary.com/demo/image/upload/w_160,h_120,c_fill/v1/listings/abc.jpg',
        )

    def test_handles_none(self):
        self.assertEqual(cloudinary_transform(None, 'w_160'), None)

    def test_handles_non_cloudinary(self):
        url = 'https://example.com/img.jpg'
        self.assertEqual(cloudinary_transform(url, 'w_160'), url)

    def test_image_variants_all_three(self):
        url = 'https://res.cloudinary.com/demo/image/upload/v1/abc.jpg'
        v = image_variants(url)
        self.assertIn('thumb', v)
        self.assertIn('card', v)
        self.assertIn('full', v)
        self.assertIn('w_160', v['thumb'])
        self.assertIn('w_640', v['card'])
        self.assertIn('w_1600', v['full'])

    def test_image_variants_none_input(self):
        v = image_variants(None)
        self.assertIsNone(v['thumb'])
        self.assertIsNone(v['card'])
        self.assertIsNone(v['full'])


# ---------------------------------------------------------------------------
# 2. Cursor pagination
# ---------------------------------------------------------------------------

class CursorPaginationTest(APITestCase):
    def setUp(self):
        self.imp = _create_importer()
        self.listings = []
        for i in range(5):
            self.listings.append(
                _create_listing(self.imp, title=f'Car {i}', make=f'Brand{i}')
            )

    def test_cursor_returns_next_previous(self):
        resp = self.client.get('/api/listings/?pagination=cursor&page_size=2')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('next', resp.data)
        self.assertIn('previous', resp.data)
        self.assertIn('results', resp.data)
        self.assertEqual(len(resp.data['results']), 2)

    def test_cursor_stable_after_insert(self):
        """Inserting a new listing mid-pagination doesn't skip/repeat rows."""
        resp1 = self.client.get('/api/listings/?pagination=cursor&page_size=3')
        self.assertEqual(len(resp1.data['results']), 3)
        ids_page1 = {r['id'] for r in resp1.data['results']}

        # Insert a new listing
        _create_listing(self.imp, title='New Mid-Page', make='NewBrand')

        # Fetch next page using cursor
        next_url = resp1.data['next']
        # The cursor URL is absolute; strip the host prefix for the test client
        if next_url:
            from urllib.parse import urlparse
            path = urlparse(next_url).path + '?' + urlparse(next_url).query
            resp2 = self.client.get(path)
            self.assertEqual(resp2.status_code, 200)
            ids_page2 = {r['id'] for r in resp2.data['results']}
            # No overlap with page 1
            self.assertEqual(ids_page1 & ids_page2, set())

    def test_default_is_page_number(self):
        """Without ?pagination=cursor, default page-number pagination is used."""
        resp = self.client.get('/api/listings/?page=1&page_size=2')
        self.assertEqual(resp.status_code, 200)
        # Page-number pagination includes 'count'
        self.assertIn('count', resp.data)


# ---------------------------------------------------------------------------
# 3. List payload excludes full image array
# ---------------------------------------------------------------------------

class ListPayloadTest(APITestCase):
    def setUp(self):
        self.imp = _create_importer()
        self.listing = _create_listing(self.imp)

    def test_list_has_primary_image_no_images_array(self):
        resp = self.client.get('/api/listings/')
        self.assertEqual(resp.status_code, 200)
        item = resp.data['results'][0]
        self.assertIn('primary_image', item)
        self.assertNotIn('images', item)
        # primary_image has thumb and card
        self.assertIn('thumb', item['primary_image'])
        self.assertIn('card', item['primary_image'])

    def test_detail_has_images_array_with_variants(self):
        resp = self.client.get(f'/api/listings/{self.listing.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('images', resp.data)
        self.assertIn('primary_image', resp.data)


# ---------------------------------------------------------------------------
# 4. N+1 query count pinning
# ---------------------------------------------------------------------------

class QueryCountTest(APITestCase):
    def setUp(self):
        self.imp = _create_importer()
        for i in range(5):
            _create_listing(self.imp, title=f'Car {i}', make=f'B{i}')

    def test_list_query_count_stable(self):
        """Listing 5 cars should use a bounded number of queries.
        Pin: 4 queries (count + listings w/ select_related + images prefetch + promotions prefetch)."""
        with self.assertNumQueries(4):
            self.client.get('/api/listings/?page_size=5')


# ---------------------------------------------------------------------------
# 5. Variant URLs are well-formed
# ---------------------------------------------------------------------------

class VariantUrlTest(TestCase):
    def test_all_variants_well_formed(self):
        url = 'https://res.cloudinary.com/dapw2jxzb/image/upload/v1/listings/demo/abc'
        v = image_variants(url)
        for name, variant_url in v.items():
            self.assertIn('cloudinary.com', variant_url, f'{name} variant missing cloudinary host')
            self.assertIn('/upload/', variant_url, f'{name} variant missing /upload/')
            self.assertTrue(variant_url.count('/upload/') == 1, f'{name} variant has double /upload/')

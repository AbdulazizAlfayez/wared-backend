"""
One shape for a listing's primary image, everywhere.

`primary_image` used to be a `{thumb, card}` dict on `/api/listings/`, a bare
full-size URL on the detail, `my`, `compare`, `featured` and the importer desk,
and `null` for a car with no photos — the same key with four types. The mobile
card read `primary_image.card` and crashed on the desk with "Cannot read
property 'card' of null".

These tests pin the contract at every endpoint that serves a listing, so the
next screen does not have to guess and the next serializer cannot quietly
disagree.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from cars.models import Listing, ListingImage
from cars.utils.cloudinary_urls import primary_image_payload

User = get_user_model()

#: Exactly the keys a client may rely on, and nothing else.
VARIANT_KEYS = {'thumb', 'card', 'full'}

def give_image(listing, *, public_id='listings/fixture', is_primary=True, order=0):
    """
    Attach a photo without uploading one.

    Assigning to a CloudinaryField calls the uploader on save — a real network
    request, which fails on fake bytes and would make these tests depend on
    Cloudinary being reachable. Writing the public_id straight to the column
    with `update()` skips `pre_save` entirely, and `.url` is derived from the
    public_id, so the delivery URL is exactly what production would build.
    """
    row = ListingImage.objects.create(listing=listing, is_primary=is_primary, order=order)
    ListingImage.objects.filter(pk=row.pk).update(image=public_id)
    row.refresh_from_db()
    return row



def assert_image_shape(case, value, *, where):
    """
    `{thumb, card, full}` of non-empty strings, or None. Never anything else.
    """
    if value is None:
        return
    case.assertIsInstance(
        value, dict,
        f'{where}: primary_image must be a dict or null, got {type(value).__name__}',
    )
    case.assertEqual(set(value), VARIANT_KEYS, f'{where}: wrong variant keys')
    for name, url in value.items():
        case.assertIsInstance(url, str, f'{where}: {name} must be a string')
        case.assertTrue(url, f'{where}: {name} must not be empty')


class ImageShapeHelperTests(TestCase):
    """The helper every serializer now calls."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            email='shape-owner@test.sa', password='Passw0rd!x', name='Owner',
            role='importer',
        )
        cls.with_image = Listing.objects.create(
            owner=cls.owner, make='Toyota', model='Camry', year=2023,
            price=120000, city='Riyadh', status='approved',
        )
        give_image(cls.with_image, public_id='listings/with-image')
        cls.no_image = Listing.objects.create(
            owner=cls.owner, make='Nissan', model='Patrol', year=2024,
            price=280000, city='Jeddah', status='approved',
        )

    def test_a_listing_with_a_photo_gets_all_three_variants(self):
        payload = primary_image_payload(self.with_image)
        self.assertEqual(set(payload), VARIANT_KEYS)
        self.assertIn('w_160,h_120', payload['thumb'])
        self.assertIn('w_640,h_480', payload['card'])
        self.assertIn('w_1600', payload['full'])

    def test_a_listing_with_no_photo_is_null_not_a_dict_of_nulls(self):
        """
        A caller asking "is there a photo" should not have to look inside to
        find out.
        """
        self.assertIsNone(primary_image_payload(self.no_image))

    def test_the_primary_photo_wins_over_the_first(self):
        give_image(self.no_image, public_id='listings/second', is_primary=False, order=1)
        give_image(self.no_image, public_id='listings/the-primary', is_primary=True, order=2)

        payload = primary_image_payload(self.no_image)
        self.assertIn('the-primary', payload['card'])
        self.assertNotIn('listings/second', payload['card'])

    def test_no_images_at_all_is_not_an_error(self):
        self.assertIsNone(primary_image_payload(None))


class ImageShapeEndpointTests(TestCase):
    """
    Every endpoint that serves a listing, with and without a photo.

    Each case asks for a car that HAS an image and one that does not, because
    the crash came from the second: the shape was right until it was null.
    """

    @classmethod
    def setUpTestData(cls):
        cls.importer = User.objects.create_user(
            email='shape-importer@test.sa', password='Passw0rd!x', name='Importer',
            role='importer', is_business_verified=True,
        )
        cls.buyer = User.objects.create_user(
            email='shape-buyer@test.sa', password='Passw0rd!x', name='Buyer', role='user',
        )
        cls.pictured = Listing.objects.create(
            owner=cls.importer, make='Toyota', model='Camry', year=2023,
            price=120000, city='Riyadh', status='approved', import_status='available',
        )
        give_image(cls.pictured, public_id='listings/pictured')
        # The crash case: an importer's own draft, saved before its photos.
        cls.bare = Listing.objects.create(
            owner=cls.importer, make='Nissan', model='Patrol', year=2024,
            price=280000, city='Jeddah', status='approved', import_status='available',
        )

    def setUp(self):
        self.client = APIClient()

    def rows_of(self, body):
        """Every listing-ish dict in a payload, whatever it is wrapped in."""
        if isinstance(body, dict):
            if 'results' in body:
                return body['results']
            if 'recent_listings' in body:
                return body['recent_listings']
            return [body]
        return body if isinstance(body, list) else []

    def check(self, path, *, user=None, key='primary_image'):
        if user is not None:
            self.client.force_authenticate(user=user)
        response = self.client.get(path)
        self.assertEqual(response.status_code, status.HTTP_200_OK, f'{path} → {response.status_code}')
        rows = self.rows_of(response.data)
        self.assertTrue(rows, f'{path} returned nothing to check')
        checked = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            target = row.get('car') or row.get('listing') or row
            if key in target:
                assert_image_shape(self, target[key], where=f'{path} (id={target.get("id")})')
                checked += 1
        self.assertTrue(checked, f'{path} carried no {key} to check')
        return response.data

    def test_listing_list(self):
        self.check('/api/listings/')

    def test_listing_detail_with_a_photo(self):
        self.check(f'/api/listings/{self.pictured.pk}/')

    def test_listing_detail_without_one(self):
        """The shape the desk crash came through."""
        self.check(f'/api/listings/{self.bare.pk}/')

    def test_my_listings(self):
        self.check('/api/listings/my/', user=self.importer)

    def test_featured(self):
        Listing.objects.filter(pk=self.pictured.pk).update(is_featured=True)
        self.check('/api/listings/featured/')

    def test_compare(self):
        self.check(f'/api/listings/compare/?ids={self.pictured.pk},{self.bare.pk}')

    def test_popular(self):
        self.check('/api/listings/popular/')

    def test_map_pins_carry_both_keys(self):
        Listing.objects.filter(pk=self.pictured.pk).update(latitude=24.7, longitude=46.6)
        data = self.check('/api/listings/map-pins/')
        rows = self.rows_of(data)
        self.assertIn('primary_image_url', rows[0])

    def test_importer_inventory(self):
        from importers.models import ImporterProfile

        profile, _ = ImporterProfile.objects.update_or_create(
            user=self.importer, defaults={'business_name': 'Shape Imports'},
        )
        self.check(f'/api/importers/{profile.pk}/inventory/')

    def test_the_importer_desk(self):
        """
        Where the crash actually happened: `recent_listings` includes the
        importer's newest cars, photographed or not.
        """
        data = self.check('/api/importers/me/desk/', user=self.importer)
        ids = {row['id'] for row in data['recent_listings']}
        self.assertIn(self.bare.pk, ids, 'the desk should include the unphotographed car')

    def test_favorites(self):
        from favorites.models import Favorite

        Favorite.objects.create(user=self.buyer, listing=self.bare)
        self.check('/api/favorites/', user=self.buyer)

    def test_orders_carry_the_same_shape_under_car(self):
        from orders.models import ImportOrder

        ImportOrder.objects.create(
            car=self.bare, buyer=self.buyer, importer=self.importer,
            status='confirmed', total_price=280000,
        )
        self.check('/api/orders/', user=self.buyer)

    def test_reservations_carry_both_keys(self):
        from orders.models import Reservation

        Reservation.objects.create(
            car=self.bare, buyer=self.buyer, importer=self.importer,
            status='pending_review',
        )
        data = self.check('/api/reservations/list/', user=self.buyer)
        rows = self.rows_of(data)
        self.assertIn('primary_image_url', rows[0]['car'])

    def test_the_importers_pending_queue(self):
        from orders.models import Reservation

        Reservation.objects.create(
            car=self.bare, buyer=self.buyer, importer=self.importer,
            status='pending_review',
        )
        data = self.check('/api/reservations/pending-for-me/', user=self.importer)
        rows = self.rows_of(data)
        self.assertIn('primary_image_url', rows[0]['car'])

    def test_conversations(self):
        from messaging.models import Conversation

        Conversation.objects.create(listing=self.bare, buyer=self.buyer, seller=self.importer)
        data = self.check('/api/conversations/', user=self.buyer)
        rows = self.rows_of(data)
        self.assertIn('primary_image_url', rows[0]['listing'])


class PublicPayloadUnchangedTests(TestCase):
    """The public listing payload gained a shape, not a field."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            email='shape-public@test.sa', password='Passw0rd!x', name='Owner',
            role='importer',
        )
        cls.listing = Listing.objects.create(
            owner=cls.owner, make='Toyota', model='Camry', year=2023,
            price=120000, city='Riyadh', status='approved', import_status='available',
        )

    def test_the_list_row_still_has_no_images_array(self):
        """
        The lean list row is lean on purpose — forty cards must not each carry
        a full image array.
        """
        response = APIClient().get('/api/listings/')
        row = next(r for r in response.data['results'] if r['id'] == self.listing.pk)
        self.assertIn('primary_image', row)
        self.assertNotIn('images', row)

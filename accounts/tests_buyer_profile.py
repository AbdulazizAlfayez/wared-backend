"""
`GET /api/users/{id}/public/` — the buyer behind a deal.

The access rule is the point of this endpoint, so most of these tests are about
who is refused. The rest check that nothing which could take a deal off the
platform appears in the payload.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from cars.models import Listing
from messaging.models import Conversation
from orders.models import ImportOrder, Reservation

User = get_user_model()


def url_for(user):
    return f'/api/users/{user.pk}/public/'


class BuyerProfileTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.buyer = User.objects.create_user(
            email='profile-buyer@test.sa', password='Passw0rd!x', name='Faisal Al Harbi',
            role='user', phone='+966500000001',
        )
        cls.importer = User.objects.create_user(
            email='profile-importer@test.sa', password='Passw0rd!x', name='Dealing Importer',
            role='importer',
        )
        cls.stranger = User.objects.create_user(
            email='profile-stranger@test.sa', password='Passw0rd!x', name='Other Importer',
            role='importer',
        )
        cls.admin = User.objects.create_user(
            email='profile-admin@test.sa', password='Passw0rd!x', name='Staffer',
            role='admin',
        )
        cls.car = Listing.objects.create(
            owner=cls.importer, make='Toyota', model='Land Cruiser', year=2023,
            price=300000, city='Riyadh', status='approved',
        )

    def setUp(self):
        self.client = APIClient()

    def as_(self, user):
        self.client.force_authenticate(user=user)
        return self.client

    def reservation(self, importer=None):
        return Reservation.objects.create(
            car=self.car, buyer=self.buyer, importer=importer or self.importer,
            status='pending_review',
        )

    def order(self, importer=None, order_status='confirmed'):
        return ImportOrder.objects.create(
            car=self.car, buyer=self.buyer, importer=importer or self.importer,
            status=order_status, total_price=300000,
        )

    def conversation(self, seller=None):
        return Conversation.objects.create(
            listing=self.car, buyer=self.buyer, seller=seller or self.importer,
        )


class BuyerProfileAccessTests(BuyerProfileTestBase):
    def test_an_importer_with_a_reservation_may_look(self):
        self.reservation()
        response = self.as_(self.importer).get(url_for(self.buyer))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], self.buyer.pk)

    def test_an_importer_with_an_order_may_look(self):
        self.order()
        self.assertEqual(
            self.as_(self.importer).get(url_for(self.buyer)).status_code,
            status.HTTP_200_OK,
        )

    def test_an_importer_with_a_conversation_may_look(self):
        self.conversation()
        self.assertEqual(
            self.as_(self.importer).get(url_for(self.buyer)).status_code,
            status.HTTP_200_OK,
        )

    def test_an_importer_who_shares_nothing_is_refused(self):
        self.reservation()
        response = self.as_(self.stranger).get(url_for(self.buyer))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn('full_name', response.data)

    def test_another_importers_deal_does_not_count(self):
        """Sharing a buyer with someone else is not sharing them with you."""
        self.reservation(importer=self.stranger)
        self.conversation(seller=self.stranger)
        self.assertEqual(
            self.as_(self.importer).get(url_for(self.buyer)).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_buyer_cannot_browse_other_buyers(self):
        other_buyer = User.objects.create_user(
            email='nosy@test.sa', password='Passw0rd!x', name='Nosy', role='user',
        )
        self.assertEqual(
            self.as_(other_buyer).get(url_for(self.buyer)).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_buyer_may_see_their_own(self):
        self.assertEqual(
            self.as_(self.buyer).get(url_for(self.buyer)).status_code,
            status.HTTP_200_OK,
        )

    def test_staff_may_look_without_sharing_anything(self):
        self.assertEqual(
            self.as_(self.admin).get(url_for(self.buyer)).status_code,
            status.HTTP_200_OK,
        )

    def test_anonymous_gets_403_not_401(self):
        """
        403 for every outsider, so an unauthenticated prod cannot tell an
        existing buyer id from a missing one.
        """
        response = self.client.get(url_for(self.buyer))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_missing_user_is_404_for_someone_allowed_to_ask(self):
        self.assertEqual(
            self.as_(self.admin).get('/api/users/99999999/public/').status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_a_cancelled_reservation_still_counts(self):
        """
        They dealt with each other; that is the question being asked. Closing
        the deal does not un-meet the buyer.
        """
        reservation = self.reservation()
        reservation.status = 'cancelled_by_buyer'
        reservation.save(update_fields=['status'])
        self.assertEqual(
            self.as_(self.importer).get(url_for(self.buyer)).status_code,
            status.HTTP_200_OK,
        )


class BuyerProfilePayloadTests(BuyerProfileTestBase):
    def payload(self):
        self.reservation()
        return self.as_(self.importer).get(url_for(self.buyer)).data

    def test_the_shape_is_exactly_what_was_promised(self):
        self.assertEqual(
            set(self.payload()),
            {'id', 'full_name', 'first_name', 'avatar_url', 'city',
             'member_since', 'reviews_received', 'completed_orders_count'},
        )

    def test_no_contact_details_anywhere(self):
        body = str(self.payload())
        self.assertNotIn('profile-buyer@test.sa', body)
        self.assertNotIn('966500000001', body)

    def test_contact_details_stay_out_even_when_the_buyer_opted_in(self):
        """
        `show_phone` / `show_email` govern the public profile, not this one. A
        deal's contact rule is the balance payment, and it lives elsewhere.
        """
        self.buyer.show_phone = True
        self.buyer.show_email = True
        self.buyer.save(update_fields=['show_phone', 'show_email'])
        payload = self.payload()
        self.assertNotIn('phone', payload)
        self.assertNotIn('email', payload)

    def test_names_are_both_forms(self):
        payload = self.payload()
        self.assertEqual(payload['full_name'], 'Faisal Al Harbi')
        self.assertEqual(payload['first_name'], 'Faisal')

    def test_first_name_matches_what_the_reservation_row_showed(self):
        from orders.serializers import person_brief

        self.assertEqual(
            self.payload()['first_name'], person_brief(self.buyer)['first_name'],
        )

    def test_city_is_the_city_name_not_an_empty_string(self):
        from locations.models import City, Region

        region = Region.objects.create(name_en='Riyadh Region', name_ar='الرياض')
        city = City.objects.create(name_en='Riyadh', name_ar='الرياض', region=region)
        self.buyer.city_obj = city
        self.buyer.save(update_fields=['city_obj'])
        self.assertEqual(self.payload()['city'], 'Riyadh')

    def test_member_since_is_iso(self):
        self.assertEqual(
            self.payload()['member_since'], self.buyer.date_joined.isoformat(),
        )

    def test_reviews_received_is_null_because_nothing_can_write_one(self):
        """
        `Review.review_type` has 'seller_to_buyer' but no path creates it, and
        the uniqueness constraints key on listing and order — both null on such
        a row — so an importer could write the same buyer down repeatedly.
        `null` says the platform does not rate buyers; `{count: 0}` would imply
        it does and nobody has.
        """
        self.assertIsNone(self.payload()['reviews_received'])

    def test_completed_orders_counts_only_completed(self):
        self.order(order_status='completed')
        self.order(order_status='delivered')
        self.order(order_status='cancelled')
        self.assertEqual(self.payload()['completed_orders_count'], 1)

    def test_no_orders_reads_zero(self):
        self.assertEqual(self.payload()['completed_orders_count'], 0)

    def test_another_buyers_orders_are_not_counted(self):
        other = User.objects.create_user(
            email='other-buyer@test.sa', password='Passw0rd!x', name='Other', role='user',
        )
        ImportOrder.objects.create(
            car=self.car, buyer=other, importer=self.importer,
            status='completed', total_price=1,
        )
        self.assertEqual(self.payload()['completed_orders_count'], 0)


class ProfileUrlIdTests(BuyerProfileTestBase):
    """Clients must be told which id opens a profile, not have to guess."""

    def test_reservation_rows_carry_it(self):
        from orders.serializers import person_brief

        brief = person_brief(self.buyer)
        self.assertEqual(brief['profile_url_id'], self.buyer.pk)

    def test_the_importers_pending_queue_carries_it(self):
        self.reservation()
        response = self.as_(self.importer).get('/api/reservations/pending-for-me/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data['results'] if 'results' in response.data else response.data
        self.assertEqual(rows[0]['buyer']['profile_url_id'], self.buyer.pk)

    def test_order_detail_carries_it(self):
        order = self.order()
        response = self.as_(self.importer).get(f'/api/orders/{order.pk}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['buyer_info']['profile_url_id'], self.buyer.pk)

    def test_order_detail_city_is_populated_not_blank(self):
        """
        `buyer_info['city']` read a `city_name` attribute User has never had,
        so it was always ''.
        """
        from locations.models import City, Region

        region = Region.objects.create(name_en='Makkah Region', name_ar='مكة')
        city = City.objects.create(name_en='Jeddah', name_ar='جدة', region=region)
        self.buyer.city_obj = city
        self.buyer.save(update_fields=['city_obj'])
        order = self.order()
        response = self.as_(self.importer).get(f'/api/orders/{order.pk}/')
        self.assertEqual(response.data['buyer_info']['city'], 'Jeddah')

    def test_the_conversation_header_carries_it_and_says_whose_it_is(self):
        self.conversation()
        response = self.as_(self.importer).get('/api/conversations/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data['results'] if 'results' in response.data else response.data
        other = rows[0]['other_party']
        self.assertEqual(other['profile_url_id'], self.buyer.pk)
        self.assertEqual(other['role'], 'user')

    def test_the_buyer_sees_the_importer_on_the_other_side(self):
        self.conversation()
        response = self.as_(self.buyer).get('/api/conversations/')
        rows = response.data['results'] if 'results' in response.data else response.data
        other = rows[0]['other_party']
        self.assertEqual(other['profile_url_id'], self.importer.pk)
        self.assertEqual(other['role'], 'importer')

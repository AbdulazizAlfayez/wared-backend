"""
Phase 5.3 — Reviews & Ratings System — 20 tests
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing, Showroom, Workshop
from leads.models import Lead
from .models import Review, ReviewReply


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, role='user', name='Test User'):
    return User.objects.create_user(email=email, password='pass1234', name=name, role=role)


def make_listing(owner, make='Toyota', model='Camry', year=2022,
                 price=50000, mileage=10000, city='Riyadh',
                 title='Test Listing', status='approved'):
    return Listing.objects.create(
        owner=owner, make=make, model=model, year=year,
        price=price, mileage=mileage, city=city,
        title=title, status=status,
    )


def make_lead(listing, buyer, dealer):
    return Lead.objects.create(
        listing=listing,
        buyer=buyer,
        dealer=dealer,
        message='I am interested',
    )


def make_review(reviewer, reviewed_user, listing=None, rating=5,
                review_type='buyer_to_seller', title='Great', comment='Excellent!',
                status='approved'):
    return Review.objects.create(
        reviewer=reviewer,
        reviewed_user=reviewed_user,
        listing=listing,
        rating=rating,
        review_type=review_type,
        title=title,
        comment=comment,
        status=status,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class ReviewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.buyer  = make_user('buyer@test.com', name='Buyer User')
        self.seller = make_user('seller@test.com', name='Seller User', role='importer')
        self.admin  = make_user('admin@test.com', name='Admin User', role='admin')
        self.admin.is_staff = True
        self.admin.save()

        self.listing = make_listing(owner=self.seller)
        self.lead = make_lead(self.listing, self.buyer, self.seller)

    # Test 1: Cannot review yourself
    def test_01_cannot_review_yourself(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/reviews/', {
            'reviewed_user': self.buyer.id,
            'listing':       self.listing.id,
            'review_type':   'buyer_to_seller',
            'rating':        5,
            'title':         'Good',
            'comment':       'Nice experience',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('cannot review yourself', str(resp.data).lower())

    # Test 2: Cannot review without lead interaction (buyer_to_seller)
    def test_02_no_review_without_lead(self):
        other_buyer = make_user('other@test.com', name='Other Buyer')
        self.client.force_authenticate(user=other_buyer)
        resp = self.client.post('/api/reviews/', {
            'reviewed_user': self.seller.id,
            'listing':       self.listing.id,
            'review_type':   'buyer_to_seller',
            'rating':        4,
            'title':         'Good',
            'comment':       'Nice',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('lead', str(resp.data).lower())

    # Test 3: Duplicate review on same listing blocked
    def test_03_duplicate_review_blocked(self):
        make_review(self.buyer, self.seller, listing=self.listing)
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/reviews/', {
            'reviewed_user': self.seller.id,
            'listing':       self.listing.id,
            'review_type':   'buyer_to_seller',
            'rating':        3,
            'title':         'Second',
            'comment':       'Try again',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already reviewed', str(resp.data).lower())

    # Test 4: Rating below 1 rejected
    def test_04_rating_below_1_rejected(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/reviews/', {
            'reviewed_user': self.seller.id,
            'listing':       self.listing.id,
            'review_type':   'buyer_to_seller',
            'rating':        0,
            'title':         'Bad',
            'comment':       'Very bad',
        })
        self.assertEqual(resp.status_code, 400)

    # Test 5: Rating above 5 rejected
    def test_05_rating_above_5_rejected(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/reviews/', {
            'reviewed_user': self.seller.id,
            'listing':       self.listing.id,
            'review_type':   'buyer_to_seller',
            'rating':        6,
            'title':         'Amazing',
            'comment':       'Perfect',
        })
        self.assertEqual(resp.status_code, 400)

    # Test 6: Valid review creates successfully with is_verified_purchase=True (when lead exists)
    def test_06_valid_review_creates_with_verified_purchase(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post('/api/reviews/', {
            'reviewed_user': self.seller.id,
            'listing':       self.listing.id,
            'review_type':   'buyer_to_seller',
            'rating':        5,
            'title':         'Great seller',
            'comment':       'Very smooth transaction',
        })
        self.assertEqual(resp.status_code, 201)
        review = Review.objects.get(id=resp.data['id'])
        self.assertTrue(review.is_verified_purchase)

    # Test 7: Only reviewed_user can reply
    def test_07_only_reviewed_user_can_reply(self):
        review = make_review(self.buyer, self.seller, listing=self.listing)
        other = make_user('other2@test.com', name='Other')
        self.client.force_authenticate(user=other)
        resp = self.client.post(f'/api/reviews/{review.id}/reply/', {'comment': 'Thanks!'})
        self.assertEqual(resp.status_code, 403)

    # Test 8: Only one reply per review
    def test_08_only_one_reply_per_review(self):
        review = make_review(self.buyer, self.seller, listing=self.listing)
        ReviewReply.objects.create(review=review, author=self.seller, comment='First reply')
        self.client.force_authenticate(user=self.seller)
        resp = self.client.post(f'/api/reviews/{review.id}/reply/', {'comment': 'Second reply'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already exists', str(resp.data).lower())

    # Test 9: Edit own review within 48h works
    def test_09_edit_own_review_within_48h(self):
        review = make_review(self.buyer, self.seller, listing=self.listing)
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.put(f'/api/reviews/{review.id}/', {
            'rating': 4,
            'title': 'Updated Title',
            'comment': 'Updated comment',
        })
        self.assertEqual(resp.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.rating, 4)

    # Test 10: Edit own review after 48h blocked
    def test_10_edit_own_review_after_48h_blocked(self):
        review = make_review(self.buyer, self.seller, listing=self.listing)
        # Force created_at to be older than 48h
        Review.objects.filter(pk=review.pk).update(
            created_at=timezone.now() - timedelta(hours=49)
        )
        review.refresh_from_db()
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.put(f'/api/reviews/{review.id}/', {
            'rating': 3,
            'title': 'Late edit',
            'comment': 'Should be blocked',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('48 hours', str(resp.data))

    # Test 11: Delete own review works
    def test_11_delete_own_review(self):
        review = make_review(self.buyer, self.seller, listing=self.listing)
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.delete(f'/api/reviews/{review.id}/')
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())

    # Test 12: Average rating recalculates on new review
    def test_12_average_rating_recalculates_on_new_review(self):
        make_review(self.buyer, self.seller, listing=self.listing, rating=4)
        self.seller.refresh_from_db()
        self.assertEqual(float(self.seller.average_rating), 4.0)
        self.assertEqual(self.seller.total_reviews, 1)

    # Test 13: Average rating recalculates on deletion
    def test_13_average_rating_recalculates_on_deletion(self):
        review = make_review(self.buyer, self.seller, listing=self.listing, rating=4)
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.total_reviews, 1)
        review.delete()
        self.seller.refresh_from_db()
        self.assertEqual(self.seller.total_reviews, 0)
        self.assertEqual(float(self.seller.average_rating), 0.0)

    # Test 14: Flagged/rejected reviews excluded from average
    def test_14_flagged_rejected_excluded_from_average(self):
        make_review(self.buyer, self.seller, listing=self.listing, rating=5, status='approved')
        buyer2 = make_user('buyer2@test.com', name='Buyer 2')
        listing2 = make_listing(owner=self.seller, title='Second Listing')
        make_review(buyer2, self.seller, listing=listing2, rating=1, status='flagged')
        self.seller.refresh_from_db()
        # Only the approved review (5) should count
        self.assertEqual(float(self.seller.average_rating), 5.0)
        self.assertEqual(self.seller.total_reviews, 1)

    # Test 15: Admin can approve/reject/flag
    def test_15_admin_can_moderate(self):
        review = make_review(self.buyer, self.seller, listing=self.listing, status='pending')
        self.client.force_authenticate(user=self.admin)
        resp = self.client.patch(f'/api/admin/reviews/{review.id}/', {
            'status': 'approved',
            'admin_notes': 'Looks legitimate',
        })
        self.assertEqual(resp.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.status, 'approved')
        self.assertEqual(review.admin_notes, 'Looks legitimate')

    # Test 16: Public endpoint only shows approved reviews
    def test_16_public_only_shows_approved(self):
        make_review(self.buyer, self.seller, listing=self.listing, status='approved')
        buyer2 = make_user('buyer3@test.com', name='Buyer 3')
        listing2 = make_listing(owner=self.seller, title='Third Listing')
        make_review(buyer2, self.seller, listing=listing2, status='flagged')
        resp = self.client.get(f'/api/reviews/?reviewed_user={self.seller.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['status'], 'approved')

    # Test 17: Showroom review works
    def test_17_showroom_review_works(self):
        self.seller.role = 'importer'
        self.seller.save()
        showroom = Showroom.objects.create(
            name='Test Showroom', owner=self.seller
        )
        review = Review.objects.create(
            reviewer=self.buyer,
            reviewed_user=self.seller,
            showroom=showroom,
            review_type='showroom',
            rating=4,
            title='Nice showroom',
            comment='Good experience',
            status='approved',
        )
        showroom.refresh_from_db()
        self.assertEqual(float(showroom.average_rating), 4.0)
        self.assertEqual(showroom.total_reviews, 1)

    # Test 18: Workshop review works
    def test_18_workshop_review_works(self):
        workshop = Workshop.objects.create(
            name='Test Workshop', city='Riyadh', owner=self.seller
        )
        review = Review.objects.create(
            reviewer=self.buyer,
            reviewed_user=self.seller,
            workshop=workshop,
            review_type='workshop',
            rating=3,
            title='OK workshop',
            comment='Decent service',
            status='approved',
        )
        workshop.refresh_from_db()
        self.assertEqual(float(workshop.average_rating), 3.0)
        self.assertEqual(workshop.total_reviews, 1)

    # Test 19: Review filters work (filter by reviewed_user)
    def test_19_review_filters_by_reviewed_user(self):
        other_seller = make_user('other_seller@test.com', name='Other Seller', role='importer')
        listing2 = make_listing(owner=other_seller, title='Other Listing')
        buyer2 = make_user('buyer4@test.com', name='Buyer 4')
        make_lead(listing2, buyer2, other_seller)

        make_review(self.buyer, self.seller, listing=self.listing, status='approved')
        make_review(buyer2, other_seller, listing=listing2, status='approved')

        resp = self.client.get(f'/api/reviews/?reviewed_user={self.seller.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['reviewed_user'], self.seller.id)

    # Test 20: Review reply shows in detail endpoint
    def test_20_reply_shows_in_detail(self):
        review = make_review(self.buyer, self.seller, listing=self.listing)
        ReviewReply.objects.create(review=review, author=self.seller, comment='Thank you!')
        resp = self.client.get(f'/api/reviews/{review.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.data['reply'])
        self.assertEqual(resp.data['reply']['comment'], 'Thank you!')
        self.assertEqual(resp.data['reply']['author_name'], 'Seller User')

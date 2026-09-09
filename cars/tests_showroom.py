"""
Phase 4.1 — Showroom Management Tests
Covers all 17 scenarios specified in the implementation spec.
"""
from django.test import override_settings, TestCase
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import (
    Listing, Showroom, ShowroomBranch, ShowroomReview, ShowroomWorkingHours,
)
from locations.models import City, Region


DUMMY_CACHE = {
    'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}
}


def make_user(email, role='user', name='Test User'):
    return User.objects.create_user(email=email, password='pw12345!', name=name, role=role)


def make_showroom(owner, name='Test Motors', **kwargs):
    return Showroom.objects.create(owner=owner, name=name, **kwargs)


@override_settings(CACHES=DUMMY_CACHE)
class ShowroomCRUDTests(TestCase):
    """Create / update / deactivate showrooms."""

    def setUp(self):
        self.client  = APIClient()
        self.dealer  = make_user('dealer@test.com', role='importer', name='Dealer')
        self.dealer2 = make_user('dealer2@test.com', role='importer', name='Dealer 2')
        self.buyer   = make_user('buyer@test.com',  role='user',   name='Buyer')
        self.admin   = make_user('admin@test.com',  role='admin',  name='Admin')

    # ---- helpers --------------------------------------------------------
    def auth(self, user):
        self.client.force_authenticate(user=user)

    # ---- 1. Dealer creates showroom → 201 --------------------------------
    def test_dealer_creates_showroom(self):
        self.auth(self.dealer)
        resp = self.client.post('/api/showrooms/', {
            'name': 'Gulf Motors',
            'description': 'Premium dealer',
            'phone': '0512345678',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(Showroom.objects.filter(owner=self.dealer).count(), 1)

    # ---- 2. Non-dealer cannot create → 403 --------------------------------
    def test_buyer_cannot_create_showroom(self):
        self.auth(self.buyer)
        resp = self.client.post('/api/showrooms/', {'name': 'My Showroom'}, format='json')
        self.assertEqual(resp.status_code, 403, resp.data)

    # ---- 3. Unauthenticated cannot create → 401 ---------------------------
    def test_unauthenticated_cannot_create(self):
        resp = self.client.post('/api/showrooms/', {'name': 'Anon Showroom'}, format='json')
        self.assertEqual(resp.status_code, 401, resp.data)

    # ---- 4. Non-owner cannot edit → 403 -----------------------------------
    def test_non_owner_cannot_edit(self):
        showroom = make_showroom(self.dealer)
        self.auth(self.dealer2)
        resp = self.client.patch(
            f'/api/showrooms/{showroom.pk}/', {'name': 'Hijack'}, format='json'
        )
        self.assertEqual(resp.status_code, 403, resp.data)

    # ---- 5. Owner can update own showroom ---------------------------------
    def test_owner_can_update_showroom(self):
        showroom = make_showroom(self.dealer)
        self.auth(self.dealer)
        resp = self.client.patch(
            f'/api/showrooms/{showroom.pk}/', {'name': 'Updated Motors'}, format='json'
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        showroom.refresh_from_db()
        self.assertEqual(showroom.name, 'Updated Motors')

    # ---- 6. Admin can update any showroom ---------------------------------
    def test_admin_can_update_any_showroom(self):
        showroom = make_showroom(self.dealer)
        self.auth(self.admin)
        resp = self.client.patch(
            f'/api/showrooms/{showroom.pk}/', {'description': 'Admin edit'}, format='json'
        )
        self.assertEqual(resp.status_code, 200, resp.data)

    # ---- 7. Delete → soft deactivate (is_active=False) -------------------
    def test_delete_deactivates_showroom(self):
        showroom = make_showroom(self.dealer)
        self.auth(self.dealer)
        resp = self.client.delete(f'/api/showrooms/{showroom.pk}/')
        self.assertEqual(resp.status_code, 204)
        showroom.refresh_from_db()
        self.assertFalse(showroom.is_active)


@override_settings(CACHES=DUMMY_CACHE)
class ShowroomDetailTests(TestCase):
    """Public detail view returns all fields including working hours."""

    def setUp(self):
        self.client  = APIClient()
        self.dealer  = make_user('dealer@d.com', role='importer')
        self.region  = Region.objects.create(name_en='Riyadh Region', name_ar='منطقة الرياض', slug='riyadh-region-detail')
        self.city    = City.objects.create(region=self.region, name_en='Riyadh', name_ar='الرياض', slug='riyadh-detail')
        self.showroom = make_showroom(
            self.dealer, name='Detail Motors',
            city_obj=self.city, description='Great place',
        )
        ShowroomWorkingHours.objects.create(
            showroom=self.showroom, day=0, opening_time='08:00', closing_time='22:00', is_closed=False
        )

    def test_public_can_view_detail(self):
        resp = self.client.get(f'/api/showrooms/{self.showroom.pk}/')
        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn('working_hours', data)
        self.assertIn('branches', data)
        self.assertIn('listings_preview', data)
        self.assertIn('is_open_now', data)
        self.assertEqual(len(data['working_hours']), 1)
        wh = data['working_hours'][0]
        self.assertEqual(wh['day'], 0)
        self.assertEqual(wh['day_display'], 'Sunday')

    def test_list_uses_list_serializer(self):
        resp = self.client.get('/api/showrooms/')
        self.assertEqual(resp.status_code, 200)
        result = resp.data['results'][0] if 'results' in resp.data else resp.data[0]
        # List serializer should NOT include working_hours or branches
        self.assertNotIn('working_hours', result)
        self.assertIn('total_listings', result)

    def test_bilingual_name_ar(self):
        self.showroom.name_ar = 'موتورز التفاصيل'
        self.showroom.save()
        resp = self.client.get(
            f'/api/showrooms/{self.showroom.pk}/',
            HTTP_ACCEPT_LANGUAGE='ar',
        )
        self.assertEqual(resp.data['name_display'], 'موتورز التفاصيل')


@override_settings(CACHES=DUMMY_CACHE)
class ShowroomWorkingHoursTests(TestCase):
    """Working hours PUT replaces full schedule."""

    def setUp(self):
        self.client  = APIClient()
        self.dealer  = make_user('dealer@wh.com', role='importer')
        self.showroom = make_showroom(self.dealer)

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def _seven_day_schedule(self):
        return [
            {'day': 0, 'opening_time': '09:00', 'closing_time': '22:00', 'is_closed': False},
            {'day': 1, 'opening_time': '09:00', 'closing_time': '22:00', 'is_closed': False},
            {'day': 2, 'opening_time': '09:00', 'closing_time': '22:00', 'is_closed': False},
            {'day': 3, 'opening_time': '09:00', 'closing_time': '22:00', 'is_closed': False},
            {'day': 4, 'opening_time': '09:00', 'closing_time': '22:00', 'is_closed': False},
            {'day': 5, 'is_closed': True, 'opening_time': None, 'closing_time': None},
            {'day': 6, 'opening_time': '10:00', 'closing_time': '20:00', 'is_closed': False},
        ]

    # ---- 8. Set 7-day schedule → 200, 7 rows created ---------------------
    def test_set_working_hours_full_week(self):
        self.auth(self.dealer)
        resp = self.client.put(
            f'/api/showrooms/{self.showroom.pk}/working-hours/',
            self._seven_day_schedule(),
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(len(resp.data), 7)
        self.assertEqual(
            ShowroomWorkingHours.objects.filter(showroom=self.showroom).count(), 7
        )

    # ---- 9. PUT replaces (idempotent) ------------------------------------
    def test_put_working_hours_replaces(self):
        self.auth(self.dealer)
        self.client.put(
            f'/api/showrooms/{self.showroom.pk}/working-hours/',
            self._seven_day_schedule(), format='json',
        )
        resp = self.client.put(
            f'/api/showrooms/{self.showroom.pk}/working-hours/',
            self._seven_day_schedule(), format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            ShowroomWorkingHours.objects.filter(showroom=self.showroom).count(), 7
        )

    # ---- 10. Non-owner cannot set working hours --------------------------
    def test_non_owner_cannot_set_working_hours(self):
        other = make_user('other@wh.com', role='importer')
        self.auth(other)
        resp = self.client.put(
            f'/api/showrooms/{self.showroom.pk}/working-hours/',
            self._seven_day_schedule(), format='json',
        )
        self.assertEqual(resp.status_code, 403, resp.data)

    # ---- 11. Public GET working hours ------------------------------------
    def test_public_get_working_hours(self):
        ShowroomWorkingHours.objects.create(
            showroom=self.showroom, day=1,
            opening_time='09:00', closing_time='20:00', is_closed=False,
        )
        resp = self.client.get(f'/api/showrooms/{self.showroom.pk}/working-hours/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    # ---- 12. Validation: opening >= closing → 400 ------------------------
    def test_invalid_hours_opening_after_closing(self):
        self.auth(self.dealer)
        resp = self.client.put(
            f'/api/showrooms/{self.showroom.pk}/working-hours/',
            [{'day': 0, 'opening_time': '22:00', 'closing_time': '09:00', 'is_closed': False}],
            format='json',
        )
        self.assertEqual(resp.status_code, 400)


@override_settings(CACHES=DUMMY_CACHE)
class ShowroomBranchTests(TestCase):
    """Branch creation, max-10, update, delete."""

    def setUp(self):
        self.client   = APIClient()
        self.dealer   = make_user('dealer@br.com', role='importer')
        self.showroom = make_showroom(self.dealer)

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def _post_branch(self, name='Branch A'):
        return self.client.post(
            f'/api/showrooms/{self.showroom.pk}/branches/',
            {'name': name, 'address': '123 King St'},
            format='json',
        )

    # ---- 13. Add branch → 201 -------------------------------------------
    def test_add_branch(self):
        self.auth(self.dealer)
        resp = self._post_branch()
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(ShowroomBranch.objects.filter(showroom=self.showroom).count(), 1)

    # ---- 14. Max 10 branches enforced -----------------------------------
    def test_max_10_branches(self):
        self.auth(self.dealer)
        for i in range(10):
            ShowroomBranch.objects.create(showroom=self.showroom, name=f'Branch {i}')
        resp = self._post_branch(name='Branch 11')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Maximum 10', resp.data['detail'])

    # ---- 15. Non-owner cannot add branch ---------------------------------
    def test_non_owner_cannot_add_branch(self):
        other = make_user('other@br.com', role='importer')
        self.auth(other)
        resp = self._post_branch()
        self.assertEqual(resp.status_code, 403, resp.data)

    # ---- 16. Unauthenticated cannot add branch ---------------------------
    def test_unauthenticated_cannot_add_branch(self):
        resp = self._post_branch()
        self.assertEqual(resp.status_code, 401, resp.data)

    # ---- 17. Public GET branches ----------------------------------------
    def test_public_get_branches(self):
        ShowroomBranch.objects.create(showroom=self.showroom, name='Main', is_main=True)
        resp = self.client.get(f'/api/showrooms/{self.showroom.pk}/branches/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    # ---- 18. Single is_main enforced in model ----------------------------
    def test_single_main_branch(self):
        b1 = ShowroomBranch.objects.create(showroom=self.showroom, name='B1', is_main=True)
        b2 = ShowroomBranch.objects.create(showroom=self.showroom, name='B2', is_main=True)
        b1.refresh_from_db()
        self.assertFalse(b1.is_main)
        self.assertTrue(b2.is_main)

    # ---- 19. PATCH branch via API ---------------------------------------
    def test_patch_branch(self):
        self.auth(self.dealer)
        branch = ShowroomBranch.objects.create(showroom=self.showroom, name='Old Name')
        resp = self.client.patch(
            f'/api/showrooms/{self.showroom.pk}/branches/{branch.pk}/',
            {'name': 'New Name'}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        branch.refresh_from_db()
        self.assertEqual(branch.name, 'New Name')

    # ---- 20. DELETE branch via API --------------------------------------
    def test_delete_branch(self):
        self.auth(self.dealer)
        branch = ShowroomBranch.objects.create(showroom=self.showroom, name='To Delete')
        resp = self.client.delete(
            f'/api/showrooms/{self.showroom.pk}/branches/{branch.pk}/'
        )
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(ShowroomBranch.objects.filter(pk=branch.pk).exists())


@override_settings(CACHES=DUMMY_CACHE)
class ShowroomReviewTests(TestCase):
    """Reviews: create, duplicate guard, owner guard, rating recalculation."""

    def setUp(self):
        self.client   = APIClient()
        self.dealer   = make_user('dealer@rev.com', role='importer', name='Dealer Rev')
        self.user1    = make_user('u1@rev.com', role='user', name='User One')
        self.user2    = make_user('u2@rev.com', role='user', name='User Two')
        self.admin    = make_user('admin@rev.com', role='admin', name='Admin Rev')
        self.showroom = make_showroom(self.dealer)

    def auth(self, user):
        self.client.force_authenticate(user=user)

    def _post_review(self, rating=5, comment='Great!'):
        return self.client.post(
            f'/api/showrooms/{self.showroom.pk}/reviews/',
            {'rating': rating, 'title': 'Good', 'comment': comment},
            format='json',
        )

    # ---- 21. User leaves review → 201, average_rating updated -----------
    def test_user_review_updates_average_rating(self):
        self.auth(self.user1)
        resp = self._post_review(rating=4)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.showroom.refresh_from_db()
        self.assertEqual(self.showroom.total_reviews, 1)
        self.assertAlmostEqual(float(self.showroom.average_rating), 4.0)

    # ---- 22. Two reviews average correctly ------------------------------
    def test_two_reviews_average(self):
        self.auth(self.user1)
        self._post_review(rating=4)
        self.auth(self.user2)
        self._post_review(rating=2)
        self.showroom.refresh_from_db()
        self.assertEqual(self.showroom.total_reviews, 2)
        self.assertAlmostEqual(float(self.showroom.average_rating), 3.0)

    # ---- 23. User cannot review twice → 400 -----------------------------
    def test_cannot_review_twice(self):
        self.auth(self.user1)
        self._post_review(rating=5)
        resp = self._post_review(rating=3)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('already reviewed', resp.data['detail'])

    # ---- 24. Owner cannot review own showroom → 400 ----------------------
    def test_owner_cannot_review_own_showroom(self):
        self.auth(self.dealer)
        resp = self._post_review(rating=5)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('own showroom', resp.data['detail'])

    # ---- 25. Unauthenticated cannot post review → 401 -------------------
    def test_unauthenticated_cannot_review(self):
        resp = self._post_review(rating=5)
        self.assertEqual(resp.status_code, 401)

    # ---- 26. GET reviews → paginated list --------------------------------
    def test_get_reviews_paginated(self):
        ShowroomReview.objects.create(
            showroom=self.showroom, user=self.user1, rating=4, is_approved=True
        )
        ShowroomReview.objects.create(
            showroom=self.showroom, user=self.user2, rating=5, is_approved=True
        )
        resp = self.client.get(f'/api/showrooms/{self.showroom.pk}/reviews/')
        self.assertEqual(resp.status_code, 200)
        # paginated response has count/results OR is a plain list
        count = resp.data.get('count', len(resp.data))
        self.assertEqual(count, 2)

    # ---- 27. Only approved reviews returned to public -------------------
    def test_unapproved_reviews_hidden(self):
        ShowroomReview.objects.create(
            showroom=self.showroom, user=self.user1, rating=1, is_approved=False
        )
        resp = self.client.get(f'/api/showrooms/{self.showroom.pk}/reviews/')
        self.assertEqual(resp.status_code, 200)
        count = resp.data.get('count', len(resp.data))
        self.assertEqual(count, 0)

    # ---- 28. User can update own review ---------------------------------
    def test_user_can_patch_own_review(self):
        self.auth(self.user1)
        self._post_review(rating=3)
        review = ShowroomReview.objects.get(showroom=self.showroom, user=self.user1)
        resp = self.client.patch(
            f'/api/showrooms/{self.showroom.pk}/reviews/{review.pk}/',
            {'rating': 5, 'comment': 'Changed mind'}, format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        review.refresh_from_db()
        self.assertEqual(review.rating, 5)

    # ---- 29. User can delete own review ---------------------------------
    def test_user_can_delete_own_review(self):
        self.auth(self.user1)
        self._post_review(rating=5)
        review = ShowroomReview.objects.get(showroom=self.showroom, user=self.user1)
        resp = self.client.delete(
            f'/api/showrooms/{self.showroom.pk}/reviews/{review.pk}/'
        )
        self.assertEqual(resp.status_code, 204)
        self.showroom.refresh_from_db()
        self.assertEqual(self.showroom.total_reviews, 0)

    # ---- 30. Other user cannot delete someone else's review → 403 --------
    def test_cannot_delete_others_review(self):
        review = ShowroomReview.objects.create(
            showroom=self.showroom, user=self.user1, rating=4, is_approved=True
        )
        self.auth(self.user2)
        resp = self.client.delete(
            f'/api/showrooms/{self.showroom.pk}/reviews/{review.pk}/'
        )
        self.assertEqual(resp.status_code, 403)


@override_settings(CACHES=DUMMY_CACHE)
class ShowroomListingsTests(TestCase):
    """GET /api/showrooms/{id}/listings/ returns approved listings only."""

    def setUp(self):
        self.client   = APIClient()
        self.dealer   = make_user('dealer@lst.com', role='importer')
        self.showroom = make_showroom(self.dealer)

    def _make_listing(self, status='approved', is_active=True):
        return Listing.objects.create(
            owner=self.dealer,
            showroom=self.showroom,
            title='Test Car',
            make='Toyota', model='Camry',
            year=2022, price=80000, mileage=30000,
            status=status, is_active=is_active,
        )

    # ---- 31. Returns only approved active listings ----------------------
    def test_returns_approved_listings_only(self):
        self._make_listing(status='approved')
        self._make_listing(status='pending')
        self._make_listing(status='approved', is_active=False)
        resp = self.client.get(f'/api/showrooms/{self.showroom.pk}/listings/')
        self.assertEqual(resp.status_code, 200)
        count = resp.data.get('count', len(resp.data.get('results', resp.data)))
        self.assertEqual(count, 1)


@override_settings(CACHES=DUMMY_CACHE)
class ShowroomVerifyTests(TestCase):
    """Admin verifies showroom → is_verified=True."""

    def setUp(self):
        self.client   = APIClient()
        self.dealer   = make_user('dealer@ver.com', role='importer')
        self.admin    = make_user('admin@ver.com',  role='admin')
        self.user     = make_user('user@ver.com',   role='user')
        self.showroom = make_showroom(self.dealer)

    def auth(self, user):
        self.client.force_authenticate(user=user)

    # ---- 32. Admin verifies showroom → 200, is_verified=True ------------
    def test_admin_verifies_showroom(self):
        self.auth(self.admin)
        resp = self.client.post(f'/api/showrooms/{self.showroom.pk}/verify/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.showroom.refresh_from_db()
        self.assertTrue(self.showroom.is_verified)
        self.assertTrue(self.showroom.verified)   # legacy flag
        self.assertIsNotNone(self.showroom.verified_at)

    # ---- 33. Non-admin cannot verify → 403 ------------------------------
    def test_non_admin_cannot_verify(self):
        self.auth(self.dealer)
        resp = self.client.post(f'/api/showrooms/{self.showroom.pk}/verify/')
        self.assertEqual(resp.status_code, 403)

    # ---- 34. Unauthenticated cannot verify → 401 ------------------------
    def test_unauthenticated_cannot_verify(self):
        resp = self.client.post(f'/api/showrooms/{self.showroom.pk}/verify/')
        self.assertEqual(resp.status_code, 401)


@override_settings(CACHES=DUMMY_CACHE)
class ShowroomFilterTests(TestCase):
    """Filter by city, region, is_verified; search by name."""

    def setUp(self):
        self.client  = APIClient()
        self.dealer  = make_user('dealer@flt.com', role='importer')
        self.region1 = Region.objects.create(name_en='Riyadh Region', name_ar='الرياض',  slug='riyadh-region')
        self.region2 = Region.objects.create(name_en='Makkah Region', name_ar='مكة',    slug='makkah-region')
        self.city1   = City.objects.create(region=self.region1, name_en='Riyadh', name_ar='الرياض', slug='riyadh-city')
        self.city2   = City.objects.create(region=self.region2, name_en='Jeddah', name_ar='جدة',    slug='jeddah-city')
        self.s1 = make_showroom(self.dealer, name='Alpha Motors',  city_obj=self.city1)
        self.s2 = make_showroom(self.dealer, name='Beta Autos',    city_obj=self.city2)
        self.s3 = make_showroom(self.dealer, name='Gamma Dealers', city_obj=self.city1)
        # s3 verified
        self.s3.is_verified = True
        self.s3.save()

    # ---- 35. Filter by city_obj -----------------------------------------
    def test_filter_by_city_obj(self):
        resp = self.client.get(f'/api/showrooms/?city_obj={self.city1.pk}')
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data.get('results', resp.data)}
        self.assertIn(self.s1.pk, ids)
        self.assertNotIn(self.s2.pk, ids)

    # ---- 36. Filter by city_obj__region ---------------------------------
    def test_filter_by_region(self):
        resp = self.client.get(f'/api/showrooms/?city_obj__region={self.region2.pk}')
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data.get('results', resp.data)}
        self.assertEqual(ids, {self.s2.pk})

    # ---- 37. Filter by is_verified --------------------------------------
    def test_filter_by_is_verified(self):
        resp = self.client.get('/api/showrooms/?is_verified=true')
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data.get('results', resp.data)}
        self.assertIn(self.s3.pk, ids)
        self.assertNotIn(self.s1.pk, ids)

    # ---- 38. Search by name --------------------------------------------
    def test_search_by_name(self):
        resp = self.client.get('/api/showrooms/?search=Alpha')
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data.get('results', resp.data)}
        self.assertIn(self.s1.pk, ids)
        self.assertNotIn(self.s2.pk, ids)

    # ---- 39. Filter by specializations ----------------------------------
    def test_filter_by_specializations(self):
        self.s1.specializations = ['luxury', 'suv']
        self.s1.save()
        self.s2.specializations = ['economy']
        self.s2.save()
        resp = self.client.get('/api/showrooms/?specializations=luxury')
        self.assertEqual(resp.status_code, 200)
        ids = {r['id'] for r in resp.data.get('results', resp.data)}
        self.assertIn(self.s1.pk, ids)
        self.assertNotIn(self.s2.pk, ids)

"""
Phase 4.3 — Dealer Subscription Plans: comprehensive test suite.

DEPRECATED: the subscription system is disabled (WARED uses a commission-only
model) and its URLs are not registered in car_marketplace/urls.py, so these
endpoint tests are skipped. Remove the skip if the app is ever re-enabled.
"""
import unittest

raise unittest.SkipTest("Subscription system deprecated — commission-only model (URLs not registered)")

import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing
from subscriptions.models import DealerSubscription, SubscriptionHistory, SubscriptionPlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_plans():
    """Idempotently create the three default plans."""
    free, _ = SubscriptionPlan.objects.get_or_create(
        slug='free', defaults={
            'name': 'Free', 'monthly_price': 0, 'annual_price': 0,
            'max_listings': 5, 'max_images_per_listing': 5,
            'max_featured_listings': 0, 'display_order': 0,
        }
    )
    basic, _ = SubscriptionPlan.objects.get_or_create(
        slug='basic', defaults={
            'name': 'Basic', 'monthly_price': 99, 'annual_price': 990,
            'max_listings': 50, 'max_images_per_listing': 15,
            'max_featured_listings': 3, 'can_access_analytics': True,
            'can_have_showroom': True, 'can_have_workshop': True,
            'badge_type': 'basic', 'display_order': 1,
        }
    )
    pro, _ = SubscriptionPlan.objects.get_or_create(
        slug='pro', defaults={
            'name': 'Pro', 'monthly_price': 299, 'annual_price': 2990,
            'max_listings': 0, 'max_images_per_listing': 20,
            'max_featured_listings': 10, 'can_access_analytics': True,
            'can_bulk_upload': True, 'can_have_showroom': True,
            'can_have_workshop': True, 'priority_support': True,
            'badge_type': 'pro', 'display_order': 2,
        }
    )
    return free, basic, pro


def make_user(email, role='user', password='pass1234!'):
    u = User.objects.create_user(email=email, password=password, name=email.split('@')[0])
    u.role = role
    u.save()
    return u


def make_dealer(email='dealer@test.com'):
    return make_user(email, role='importer')


def make_admin(email='admin@test.com'):
    u = make_user(email, role='admin')
    u.is_staff = True
    u.is_superuser = True
    u.save()
    return u


def get_plans():
    return create_plans()


def auth_client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def dealer_with_free_sub(email='d@test.com'):
    """
    Create a dealer and ensure they have a Free subscription.
    Calls create_plans() first so the signal can fire correctly,
    then uses get_or_create to avoid duplicate-key errors if the
    signal already created the subscription.
    """
    create_plans()
    dealer = make_dealer(email)
    free   = SubscriptionPlan.objects.get(slug='free')
    DealerSubscription.objects.get_or_create(
        dealer=dealer,
        defaults={
            'plan': free, 'billing_cycle': 'monthly', 'status': 'active',
            'expires_at': timezone.now() + timedelta(days=3650),
        },
    )
    return dealer


def make_listing(owner):
    return Listing.objects.create(
        title='Test Car', make='Toyota', model='Camry', year=2022,
        price=50000, mileage=10000, fuel_type='gasoline',
        transmission='automatic', body_type='sedan',
        condition='used', city='Riyadh', status='approved',
        is_active=True, owner=owner,
    )


# ===========================================================================
# 1. Plans endpoint — public
# ===========================================================================
class SubscriptionPlanListTest(TestCase):
    def setUp(self):
        create_plans()

    def test_returns_3_plans(self):
        res = APIClient().get('/api/subscription-plans/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 3)

    def test_plan_names(self):
        res = APIClient().get('/api/subscription-plans/')
        names = {p['name'] for p in res.data}
        self.assertSetEqual(names, {'Free', 'Basic', 'Pro'})

    def test_plan_has_features(self):
        res = APIClient().get('/api/subscription-plans/')
        for plan in res.data:
            self.assertIn('features', plan)
            self.assertIsInstance(plan['features'], list)
            self.assertGreater(len(plan['features']), 0)

    def test_plan_detail_by_slug(self):
        res = APIClient().get('/api/subscription-plans/basic/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['name'], 'Basic')

    def test_unauthenticated_can_view_plans(self):
        res = APIClient().get('/api/subscription-plans/')
        self.assertEqual(res.status_code, 200)

    def test_free_plan_price_is_zero(self):
        res = APIClient().get('/api/subscription-plans/free/')
        self.assertEqual(float(res.data['monthly_price']), 0.0)

    def test_pro_plan_unlimited_listings(self):
        res = APIClient().get('/api/subscription-plans/pro/')
        self.assertEqual(res.data['max_listings'], 0)
        self.assertIn("Unlimited listings", res.data['features'])


# ===========================================================================
# 2. Auto-assign Free plan via signal
# ===========================================================================
class AutoAssignFreePlanTest(TestCase):
    def setUp(self):
        create_plans()

    def test_signal_assigns_free_plan_on_importer_role(self):
        user = make_user('newsignaldealer@test.com', role='user')
        self.assertFalse(DealerSubscription.objects.filter(dealer=user).exists())
        user.role = 'importer'
        user.save()
        self.assertTrue(DealerSubscription.objects.filter(dealer=user).exists())
        sub = DealerSubscription.objects.get(dealer=user)
        self.assertEqual(sub.plan.slug, 'free')
        self.assertEqual(sub.status, 'active')

    def test_signal_does_not_duplicate_subscription(self):
        """Saving dealer user twice shouldn't create a second subscription."""
        dealer = dealer_with_free_sub('dup@test.com')
        dealer.name = 'Updated Name'
        dealer.save()
        self.assertEqual(DealerSubscription.objects.filter(dealer=dealer).count(), 1)


# ===========================================================================
# 3. GET /api/my-subscription/ — dealer views their subscription
# ===========================================================================
class GetMySubscriptionTest(TestCase):
    def setUp(self):
        create_plans()

    def test_importer_sees_free_subscription(self):
        dealer = dealer_with_free_sub('getme@test.com')
        res = auth_client(dealer).get('/api/my-subscription/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['plan']['slug'], 'free')

    def test_unauthenticated_returns_401(self):
        res = APIClient().get('/api/my-subscription/')
        self.assertEqual(res.status_code, 401)

    def test_non_importer_returns_403(self):
        user = make_user('rando@test.com', role='user')
        res = auth_client(user).get('/api/my-subscription/')
        self.assertEqual(res.status_code, 403)

    def test_importer_with_no_sub_returns_404(self):
        # dealer created without going through signal (manual)
        dealer = make_user('nosub@test.com', role='importer')
        DealerSubscription.objects.filter(dealer=dealer).delete()
        res = auth_client(dealer).get('/api/my-subscription/')
        self.assertEqual(res.status_code, 404)


# ===========================================================================
# 4. POST /api/my-subscription/subscribe/ — subscribe / upgrade / downgrade
# ===========================================================================
class SubscribeTest(TestCase):
    def setUp(self):
        self.dealer = dealer_with_free_sub('sub@test.com')
        self.client = auth_client(self.dealer)
        self.free, self.basic, self.pro = get_plans()

    def test_upgrade_to_basic(self):
        res = self.client.post('/api/my-subscription/subscribe/', {
            'plan_id': self.basic.pk, 'billing_cycle': 'monthly',
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['plan']['slug'], 'basic')
        self.assertEqual(res.data['status'], 'active')

    def test_history_logged_on_upgrade(self):
        self.client.post('/api/my-subscription/subscribe/', {
            'plan_id': self.basic.pk, 'billing_cycle': 'monthly',
        })
        self.assertTrue(
            SubscriptionHistory.objects.filter(dealer=self.dealer, action='upgraded').exists()
        )

    def test_upgrade_to_pro(self):
        self.client.post('/api/my-subscription/subscribe/', {'plan_id': self.basic.pk, 'billing_cycle': 'monthly'})
        res = self.client.post('/api/my-subscription/subscribe/', {'plan_id': self.pro.pk, 'billing_cycle': 'annual'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['plan']['slug'], 'pro')

    def test_downgrade_logged(self):
        self.client.post('/api/my-subscription/subscribe/', {'plan_id': self.pro.pk, 'billing_cycle': 'monthly'})
        self.client.post('/api/my-subscription/subscribe/', {'plan_id': self.basic.pk, 'billing_cycle': 'monthly'})
        self.assertTrue(
            SubscriptionHistory.objects.filter(dealer=self.dealer, action='downgraded').exists()
        )

    def test_renew_same_plan_logged(self):
        self.client.post('/api/my-subscription/subscribe/', {'plan_id': self.basic.pk, 'billing_cycle': 'monthly'})
        self.client.post('/api/my-subscription/subscribe/', {'plan_id': self.basic.pk, 'billing_cycle': 'monthly'})
        self.assertTrue(
            SubscriptionHistory.objects.filter(dealer=self.dealer, action='renewed').exists()
        )

    def test_monthly_expiry_is_30_days(self):
        res = self.client.post('/api/my-subscription/subscribe/', {
            'plan_id': self.basic.pk, 'billing_cycle': 'monthly',
        })
        sub = DealerSubscription.objects.get(dealer=self.dealer)
        expected = timezone.now() + timedelta(days=30)
        diff = abs((sub.expires_at - expected).total_seconds())
        self.assertLess(diff, 5)

    def test_annual_expiry_is_365_days(self):
        self.client.post('/api/my-subscription/subscribe/', {
            'plan_id': self.pro.pk, 'billing_cycle': 'annual',
        })
        sub = DealerSubscription.objects.get(dealer=self.dealer)
        expected = timezone.now() + timedelta(days=365)
        diff = abs((sub.expires_at - expected).total_seconds())
        self.assertLess(diff, 5)

    def test_invalid_plan_id_returns_400(self):
        res = self.client.post('/api/my-subscription/subscribe/', {
            'plan_id': 99999, 'billing_cycle': 'monthly',
        })
        self.assertEqual(res.status_code, 400)

    def test_non_importer_cannot_subscribe(self):
        user = make_user('notdealer@test.com', role='user')
        res = auth_client(user).post('/api/my-subscription/subscribe/', {
            'plan_id': self.basic.pk, 'billing_cycle': 'monthly',
        })
        self.assertEqual(res.status_code, 403)

    def test_unauthenticated_cannot_subscribe(self):
        res = APIClient().post('/api/my-subscription/subscribe/', {
            'plan_id': self.basic.pk, 'billing_cycle': 'monthly',
        })
        self.assertEqual(res.status_code, 401)


# ===========================================================================
# 5. Listing limit enforcement
# ===========================================================================
class ListingLimitTest(TestCase):
    def setUp(self):
        self.free, self.basic, self.pro = get_plans()
        self.dealer = dealer_with_free_sub('lim@test.com')
        self.client = auth_client(self.dealer)

    def _create_listing_payload(self):
        return {
            'title': 'Car', 'make': 'Toyota', 'model': 'Camry', 'year': 2022,
            'price': 50000, 'mileage': 10000, 'fuel_type': 'petrol',
            'transmission': 'automatic', 'body_type': 'sedan',
            'condition': 'used', 'city': 'Riyadh',
        }

    def test_free_plan_allows_up_to_5_listings(self):
        for i in range(5):
            make_listing(self.dealer)
        # all 5 present and is_active=True
        count = Listing.objects.filter(owner=self.dealer, is_active=True).exclude(status='sold').count()
        self.assertEqual(count, 5)

    def test_6th_listing_on_free_plan_returns_403(self):
        for _ in range(5):
            make_listing(self.dealer)
        res = self.client.post('/api/listings/', self._create_listing_payload(), format='json')
        self.assertEqual(res.status_code, 403)
        # Check error message contains limit info
        body = res.data
        self.assertIn('5', str(body))

    def test_upgrade_to_basic_allows_more_listings(self):
        for _ in range(5):
            make_listing(self.dealer)
        # Upgrade to Basic (50 listings)
        auth_client(self.dealer).post('/api/my-subscription/subscribe/', {
            'plan_id': self.basic.pk, 'billing_cycle': 'monthly',
        })
        res = self.client.post('/api/listings/', self._create_listing_payload(), format='json')
        # Should succeed (201 or 200, not 403)
        self.assertNotEqual(res.status_code, 403)

    def test_pro_plan_allows_unlimited_listings(self):
        auth_client(self.dealer).post('/api/my-subscription/subscribe/', {
            'plan_id': self.pro.pk, 'billing_cycle': 'monthly',
        })
        for _ in range(6):
            make_listing(self.dealer)
        # 7th listing attempt — should not hit a limit
        res = self.client.post('/api/listings/', self._create_listing_payload(), format='json')
        self.assertNotEqual(res.status_code, 403)


# ===========================================================================
# 6. Cancel subscription
# ===========================================================================
class CancelSubscriptionTest(TestCase):
    def setUp(self):
        self.dealer = dealer_with_free_sub('cancel@test.com')
        self.client = auth_client(self.dealer)
        _, basic, _ = get_plans()
        self.client.post('/api/my-subscription/subscribe/', {
            'plan_id': basic.pk, 'billing_cycle': 'monthly',
        })

    def test_cancel_sets_status_cancelled(self):
        res = self.client.post('/api/my-subscription/cancel/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['status'], 'cancelled')

    def test_cancelled_at_is_set(self):
        self.client.post('/api/my-subscription/cancel/')
        sub = DealerSubscription.objects.get(dealer=self.dealer)
        self.assertIsNotNone(sub.cancelled_at)

    def test_cancelling_again_returns_400(self):
        self.client.post('/api/my-subscription/cancel/')
        res = self.client.post('/api/my-subscription/cancel/')
        self.assertEqual(res.status_code, 400)

    def test_history_logged_on_cancel(self):
        self.client.post('/api/my-subscription/cancel/')
        self.assertTrue(
            SubscriptionHistory.objects.filter(dealer=self.dealer, action='cancelled').exists()
        )


# ===========================================================================
# 7. Subscription history
# ===========================================================================
class SubscriptionHistoryTest(TestCase):
    def test_history_endpoint_returns_records(self):
        dealer = dealer_with_free_sub('hist@test.com')
        _, basic, _ = get_plans()
        auth_client(dealer).post('/api/my-subscription/subscribe/', {
            'plan_id': basic.pk, 'billing_cycle': 'monthly',
        })
        res = auth_client(dealer).get('/api/my-subscription/history/')
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.data), 0)
        first = res.data[0]
        self.assertIn('plan_name', first)
        self.assertIn('action', first)

    def test_non_importer_cannot_view_history(self):
        user = make_user('nobody@test.com', role='user')
        res = auth_client(user).get('/api/my-subscription/history/')
        self.assertEqual(res.status_code, 403)


# ===========================================================================
# 8. Admin endpoints
# ===========================================================================
class AdminSubscriptionTest(TestCase):
    def setUp(self):
        create_plans()
        self.admin  = make_admin('adm@test.com')
        self.dealer = dealer_with_free_sub('admdeal@test.com')

    def test_admin_can_list_subscriptions(self):
        res = auth_client(self.admin).get('/api/admin/subscriptions/')
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(len(res.data), 1)

    def test_non_admin_cannot_list(self):
        res = auth_client(self.dealer).get('/api/admin/subscriptions/')
        self.assertEqual(res.status_code, 403)

    def test_admin_stats_endpoint(self):
        res = auth_client(self.admin).get('/api/admin/subscriptions/stats/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('by_plan', res.data)
        self.assertIn('revenue_this_month', res.data)
        self.assertIn('new_subscribers_this_week', res.data)

    def test_admin_can_patch_subscription(self):
        sub = DealerSubscription.objects.get(dealer=self.dealer)
        res = auth_client(self.admin).patch(
            f'/api/admin/subscriptions/{sub.pk}/',
            {'status': 'past_due'},
            format='json',
        )
        self.assertEqual(res.status_code, 200)

    def test_unauthenticated_cannot_access_admin(self):
        res = APIClient().get('/api/admin/subscriptions/')
        self.assertEqual(res.status_code, 401)


# ===========================================================================
# 9. Celery tasks
# ===========================================================================
class CeleryTaskTest(TestCase):
    def setUp(self):
        self.free, self.basic, _ = get_plans()

    def test_check_expired_subscriptions_expires_and_downgrades(self):
        dealer = dealer_with_free_sub('expire@test.com')
        sub    = DealerSubscription.objects.get(dealer=dealer)
        # Force onto Basic with past expiry
        sub.plan       = self.basic
        sub.status     = 'active'
        sub.expires_at = timezone.now() - timedelta(days=1)
        sub.save()

        from subscriptions.tasks import check_expired_subscriptions
        result = check_expired_subscriptions()
        self.assertIn('1', result)

        sub.refresh_from_db()
        self.assertEqual(sub.status, 'expired')
        self.assertEqual(sub.plan.slug, 'free')
        self.assertTrue(
            SubscriptionHistory.objects.filter(dealer=dealer, action='expired').exists()
        )

    def test_check_expired_leaves_active_untouched(self):
        dealer = dealer_with_free_sub('notexp@test.com')
        from subscriptions.tasks import check_expired_subscriptions
        check_expired_subscriptions()
        sub = DealerSubscription.objects.get(dealer=dealer)
        self.assertEqual(sub.status, 'active')

    def test_send_expiry_reminders_sends_for_7_days(self):
        dealer = dealer_with_free_sub('remind@test.com')
        sub    = DealerSubscription.objects.get(dealer=dealer)
        sub.plan       = self.basic
        sub.status     = 'active'
        sub.expires_at = timezone.now() + timedelta(days=7)
        sub.save()

        from subscriptions.tasks import send_expiry_reminders
        result = send_expiry_reminders()
        self.assertIn('1', result)


# ===========================================================================
# 10. DealerSubscription model properties
# ===========================================================================
class ModelPropertyTest(TestCase):
    def setUp(self):
        create_plans()
        self.dealer = dealer_with_free_sub('prop@test.com')
        self.sub    = DealerSubscription.objects.get(dealer=self.dealer)

    def test_days_remaining_positive_for_active(self):
        self.assertGreater(self.sub.days_remaining, 0)

    def test_is_expired_false_for_active(self):
        self.assertFalse(self.sub.is_expired)

    def test_is_expired_true_for_past_expiry(self):
        self.sub.expires_at = timezone.now() - timedelta(days=1)
        self.sub.save()
        self.assertTrue(self.sub.is_expired)

    def test_is_trial_false_by_default(self):
        self.assertFalse(self.sub.is_trial)

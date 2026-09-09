"""
Phase 5.4 — Fraud Prevention Tests (20 tests)
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from cars.models import Listing
from fraud.models import FraudFlag, IPLog, ListingLimit
from fraud.utils import _get_user_daily_limit, check_and_enforce_limit, increment_listing_count, log_ip_action


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(email, role='user', **kwargs):
    return User.objects.create_user(email=email, password='testpass123', name='Test User', role=role, **kwargs)


def _make_admin(email='admin@test.com'):
    return User.objects.create_user(
        email=email, password='testpass123', name='Admin', role='admin', is_staff=True,
    )


def _make_listing(owner, price=50000, vin=None, title='Test Car', description='', year=2020, make='Toyota', model='Camry', status='pending'):
    return Listing.objects.create(
        title=title,
        make=make,
        model=model,
        year=year,
        price=price,
        mileage=50000,
        city='Riyadh',
        description=description,
        owner=owner,
        status=status,
        vin=vin,
    )


# ---------------------------------------------------------------------------
# Test 1 — Duplicate VIN detected and flagged
# ---------------------------------------------------------------------------

class DuplicateVINTest(TestCase):
    def test_duplicate_vin_creates_fraud_flag(self):
        """Duplicate VIN should create a FraudFlag with severity=critical."""
        from fraud.tasks import check_duplicate_vin
        from unittest.mock import patch, MagicMock

        user = _make_user('seller1@test.com')
        listing1 = _make_listing(user, vin='1HGBH41JXMN109186')
        listing2 = _make_listing(user, vin=None, title='Duplicate Car')

        # Simulate that there IS a duplicate by patching the queryset check
        dup_qs = Listing.objects.filter(pk=listing2.pk)
        with patch('cars.models.Listing.objects') as mock_mgr:
            # .get() returns listing1
            mock_mgr.get.return_value = listing1
            # .filter(...).exclude(...) returns dup_qs (non-empty)
            mock_filter = MagicMock()
            mock_filter.exclude.return_value = dup_qs
            mock_mgr.filter.return_value = mock_filter

            check_duplicate_vin(listing1.pk)

        flag = FraudFlag.objects.filter(listing=listing1, flag_type='duplicate_vin').first()
        self.assertIsNotNone(flag)
        self.assertEqual(flag.severity, 'critical')
        self.assertEqual(flag.details['vin'], listing1.vin)


# ---------------------------------------------------------------------------
# Test 2 — Suspicious low price (below 20% of market)
# ---------------------------------------------------------------------------

class SuspiciousPriceTest(TestCase):
    def test_price_below_20pct_of_market_flagged(self):
        """Price below 20% of market average should create suspicious_price flag."""
        from fraud.tasks import check_suspicious_pricing

        user = _make_user('seller2@test.com')
        # Create 3 comparables at 100,000 SAR
        for i in range(3):
            _make_listing(user, price=100000, year=2020, make='Toyota', model='Camry',
                          vin=None, title=f'Comparable {i}', status='approved')

        # Target listing at 5,000 SAR (5% of market)
        target = _make_listing(user, price=5000, year=2020, make='Toyota', model='Camry')
        check_suspicious_pricing(target.pk)

        flag = FraudFlag.objects.filter(listing=target, flag_type='suspicious_price').first()
        self.assertIsNotNone(flag)
        self.assertEqual(flag.severity, 'high')
        self.assertIn('price_below_20pct_of_market', flag.details['reason'])


# ---------------------------------------------------------------------------
# Test 3 — Suspicious high price (above 300% of market)
# ---------------------------------------------------------------------------

    def test_price_above_300pct_of_market_flagged(self):
        """Price above 300% of market average should create price_manipulation flag."""
        from fraud.tasks import check_suspicious_pricing

        user = _make_user('seller3@test.com')
        for i in range(3):
            _make_listing(user, price=50000, year=2021, make='Honda', model='Civic',
                          vin=None, title=f'Comp {i}', status='approved')

        target = _make_listing(user, price=500000, year=2021, make='Honda', model='Civic')
        check_suspicious_pricing(target.pk)

        flag = FraudFlag.objects.filter(listing=target, flag_type='price_manipulation').first()
        self.assertIsNotNone(flag)
        self.assertEqual(flag.severity, 'medium')


# ---------------------------------------------------------------------------
# Test 4 — Price below 1000 SAR flagged
# ---------------------------------------------------------------------------

    def test_price_below_1000_sar_flagged(self):
        """Price below 1000 SAR should immediately create suspicious_price flag."""
        from fraud.tasks import check_suspicious_pricing

        user = _make_user('seller4@test.com')
        target = _make_listing(user, price=500)
        check_suspicious_pricing(target.pk)

        flag = FraudFlag.objects.filter(listing=target, flag_type='suspicious_price').first()
        self.assertIsNotNone(flag)
        self.assertEqual(flag.details['reason'], 'below_minimum_1000_sar')


# ---------------------------------------------------------------------------
# Test 5 — Rapid posting (10+ in 1 hour) flagged
# ---------------------------------------------------------------------------

class RapidPostingTest(TestCase):
    def test_rapid_posting_10_per_hour_flagged(self):
        """10+ listings in 1 hour should create rapid_posting flag."""
        from fraud.tasks import check_rapid_posting

        user = _make_user('rapid@test.com')
        now = timezone.now()
        for i in range(10):
            listing = _make_listing(user, vin=None, title=f'Car {i}')
            Listing.objects.filter(pk=listing.pk).update(created_at=now - timedelta(minutes=i * 5))

        check_rapid_posting(user.pk)

        flag = FraudFlag.objects.filter(user=user, flag_type='rapid_posting').first()
        self.assertIsNotNone(flag)
        self.assertEqual(flag.severity, 'high')


# ---------------------------------------------------------------------------
# Test 6 — Rapid posting (30+ in 24h) auto-suspends user
# ---------------------------------------------------------------------------

    def test_excessive_listings_auto_suspends_user(self):
        """30+ listings in 24 hours should auto-suspend the user."""
        from fraud.tasks import check_rapid_posting

        user = _make_user('excessive@test.com')
        now = timezone.now()
        for i in range(31):
            listing = _make_listing(user, vin=None, title=f'Car {i}')
            Listing.objects.filter(pk=listing.pk).update(created_at=now - timedelta(minutes=i * 10))

        check_rapid_posting(user.pk)

        user.refresh_from_db()
        self.assertTrue(user.is_suspended)
        self.assertIn('excessive listing creation', user.suspension_reason)

        flag = FraudFlag.objects.filter(user=user, flag_type='excessive_listings').first()
        self.assertIsNotNone(flag)
        self.assertEqual(flag.severity, 'critical')


# ---------------------------------------------------------------------------
# Test 7 — Daily listing limit enforced via API
# ---------------------------------------------------------------------------

class DailyListingLimitTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        # Listing creation is importer-only, so the throttle test needs one
        self.user = _make_user('limituser@test.com', role='importer')
        self.client.force_authenticate(user=self.user)

    @patch('fraud.tasks.check_duplicate_vin.delay')
    @patch('fraud.tasks.check_suspicious_pricing.delay')
    @patch('fraud.tasks.check_rapid_posting.delay')
    @patch('fraud.tasks.check_banned_keywords.delay')
    def test_daily_limit_returns_429_when_exceeded(self, mock_kw, mock_rapid, mock_price, mock_vin):
        """API should return 429 Throttled when daily limit is exhausted."""
        # Exhaust the daily limit (importers get 20/day — daily_limit is
        # recomputed from role on every check, so exhaust against 20)
        ListingLimit.objects.create(
            user=self.user,
            daily_limit=20,
            listings_today=20,
            last_reset=timezone.now().date(),
        )
        data = {
            'title': 'Test Car', 'make': 'Toyota', 'model': 'Camry',
            'year': 2020, 'price': 50000, 'mileage': 30000, 'city': 'Riyadh',
        }
        response = self.client.post('/api/listings/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


# ---------------------------------------------------------------------------
# Test 8 — Daily limit resets when last_reset is yesterday
# ---------------------------------------------------------------------------

class DailyLimitResetTest(TestCase):
    def test_limit_resets_when_last_reset_is_yesterday(self):
        """check_and_enforce_limit should reset counter if last_reset < today."""
        user = _make_user('resetuser@test.com')
        yesterday = timezone.now().date() - timedelta(days=1)
        # auto_now_add prevents setting last_reset via create(), so use update() after creation
        limit_obj = ListingLimit.objects.create(user=user, daily_limit=5, listings_today=5)
        ListingLimit.objects.filter(pk=limit_obj.pk).update(last_reset=yesterday)

        allowed, limit, used = check_and_enforce_limit(user)
        self.assertTrue(allowed)
        self.assertEqual(used, 0)


# ---------------------------------------------------------------------------
# Test 9 — Banned keyword in title flagged
# ---------------------------------------------------------------------------

class BannedKeywordTest(TestCase):
    def test_banned_keyword_in_title_creates_flag(self):
        """Listing with 'western union' in title should be flagged."""
        from fraud.tasks import check_banned_keywords

        user = _make_user('kw1@test.com')
        listing = _make_listing(user, title='Buy Car via Western Union Payment')
        check_banned_keywords(listing.pk)

        flag = FraudFlag.objects.filter(listing=listing, flag_type='banned_keywords').first()
        self.assertIsNotNone(flag)
        self.assertIn('western union', flag.details['matched_keywords'])


# ---------------------------------------------------------------------------
# Test 10 — Banned keyword in description flagged
# ---------------------------------------------------------------------------

    def test_banned_keyword_in_description_creates_flag(self):
        """Listing with 'no inspection' in description should be flagged."""
        from fraud.tasks import check_banned_keywords

        user = _make_user('kw2@test.com')
        listing = _make_listing(user, description='Sold as-is only, no inspection required.')
        check_banned_keywords(listing.pk)

        flag = FraudFlag.objects.filter(listing=listing, flag_type='banned_keywords').first()
        self.assertIsNotNone(flag)
        matched = flag.details['matched_keywords']
        self.assertTrue(any('no inspection' in kw or 'as-is only' in kw for kw in matched))


# ---------------------------------------------------------------------------
# Test 11 — IP logged on listing creation
# ---------------------------------------------------------------------------

class IPLogTest(TestCase):
    def test_ip_logged_on_listing_creation(self):
        """log_ip_action should create an IPLog entry."""
        user = _make_user('iplog@test.com')
        log_ip_action(user, '192.168.1.1', 'create_listing', 'TestAgent')

        log = IPLog.objects.filter(user=user, action='create_listing').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.ip_address, '192.168.1.1')
        self.assertEqual(log.user_agent, 'TestAgent')


# ---------------------------------------------------------------------------
# Test 12 — IP logged on login
# ---------------------------------------------------------------------------

    def test_ip_logged_on_login(self):
        """log_ip_action should create an IPLog entry for login action."""
        user = _make_user('loginlog@test.com')
        log_ip_action(user, '10.0.0.1', 'login', 'Mozilla/5.0')

        log = IPLog.objects.filter(user=user, action='login').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.ip_address, '10.0.0.1')


# ---------------------------------------------------------------------------
# Test 13 — Multiple accounts on same IP (check_ip_abuse logic)
# ---------------------------------------------------------------------------

class IPAbuseTest(TestCase):
    def test_multiple_accounts_on_same_ip_detected(self):
        """check_ip_abuse should flag an IP with 5+ distinct users."""
        from fraud.tasks import check_ip_abuse

        ip = '203.0.113.1'
        for i in range(5):
            user = _make_user(f'ipabuse{i}@test.com')
            IPLog.objects.create(user=user, ip_address=ip, action='login')

        check_ip_abuse()

        flag = FraudFlag.objects.filter(flag_type='ip_abuse').first()
        self.assertIsNotNone(flag)
        self.assertEqual(flag.details['ip_address'], ip)


# ---------------------------------------------------------------------------
# Test 14 — Admin can view all fraud flags
# ---------------------------------------------------------------------------

class AdminFraudViewTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = _make_admin()
        self.client.force_authenticate(user=self.admin)

    def test_admin_can_list_fraud_flags(self):
        """GET /api/admin/fraud/ should return all fraud flags for admin."""
        user = _make_user('listed@test.com')
        listing = _make_listing(user)
        FraudFlag.objects.create(
            listing=listing, user=user, flag_type='duplicate_vin', severity='critical',
        )
        response = self.client.get('/api/admin/fraud/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # May be paginated or list — either way data should contain results
        data = response.data
        results = data.get('results', data) if isinstance(data, dict) else data
        self.assertGreaterEqual(len(results), 1)


# ---------------------------------------------------------------------------
# Test 15 — Admin can resolve fraud flag
# ---------------------------------------------------------------------------

    def test_admin_can_resolve_fraud_flag(self):
        """PATCH /api/admin/fraud/{id}/ should mark flag as resolved."""
        user = _make_user('resolve@test.com')
        listing = _make_listing(user)
        flag = FraudFlag.objects.create(
            listing=listing, user=user, flag_type='banned_keywords', severity='medium',
        )
        response = self.client.patch(
            f'/api/admin/fraud/{flag.pk}/',
            {'resolution_notes': 'False positive — reviewed and cleared.'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        flag.refresh_from_db()
        self.assertTrue(flag.is_resolved)
        self.assertEqual(flag.resolved_by, self.admin)


# ---------------------------------------------------------------------------
# Test 16 — Admin can filter flags by type
# ---------------------------------------------------------------------------

    def test_admin_can_filter_flags_by_type(self):
        """GET /api/admin/fraud/?flag_type=duplicate_vin should only return that type."""
        user = _make_user('filter@test.com')
        listing = _make_listing(user)
        FraudFlag.objects.create(listing=listing, user=user, flag_type='duplicate_vin', severity='critical')
        FraudFlag.objects.create(listing=listing, user=user, flag_type='banned_keywords', severity='medium')

        response = self.client.get('/api/admin/fraud/?flag_type=duplicate_vin')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        results = data.get('results', data) if isinstance(data, dict) else data
        for item in results:
            self.assertEqual(item['flag_type'], 'duplicate_vin')


# ---------------------------------------------------------------------------
# Test 17 — Fraud stats endpoint returns correct counts
# ---------------------------------------------------------------------------

    def test_fraud_stats_returns_correct_counts(self):
        """GET /api/admin/fraud/stats/ should return accurate aggregate counts."""
        user = _make_user('stats@test.com')
        listing = _make_listing(user)
        FraudFlag.objects.create(listing=listing, user=user, flag_type='duplicate_vin', severity='critical', is_resolved=False)
        FraudFlag.objects.create(listing=listing, user=user, flag_type='suspicious_price', severity='high', is_resolved=True)

        response = self.client.get('/api/admin/fraud/stats/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['unresolved'], 1)
        self.assertEqual(data['critical'], 1)
        self.assertIn('duplicate_vin', data['by_type'])


# ---------------------------------------------------------------------------
# Test 18 — Manual scan endpoint triggers tasks
# ---------------------------------------------------------------------------

    @patch('fraud.tasks.check_duplicate_vin.delay')
    @patch('fraud.tasks.check_suspicious_pricing.delay')
    @patch('fraud.tasks.check_banned_keywords.delay')
    @patch('fraud.tasks.check_rapid_posting.delay')
    def test_manual_scan_triggers_tasks(self, mock_rapid, mock_kw, mock_price, mock_vin):
        """POST /api/admin/fraud/scan/ should trigger all relevant celery tasks."""
        user = _make_user('scan@test.com')
        listing = _make_listing(user)

        response = self.client.post(
            '/api/admin/fraud/scan/',
            {'listing_id': listing.pk, 'user_id': user.pk},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_vin.assert_called_once_with(listing.pk)
        mock_price.assert_called_once_with(listing.pk)
        mock_kw.assert_called_once_with(listing.pk)
        mock_rapid.assert_called_once_with(user.pk)


# ---------------------------------------------------------------------------
# Test 19 — Importer daily limit is higher than regular user
# ---------------------------------------------------------------------------

class DailyLimitRoleTest(TestCase):
    def test_importer_daily_limit_higher_than_regular_user(self):
        """Importer should have a higher daily limit than a regular user."""
        regular_user = _make_user('regular@test.com', role='user')
        importer = _make_user('importer@test.com', role='importer')

        regular_limit = _get_user_daily_limit(regular_user)
        dealer_limit  = _get_user_daily_limit(importer)

        self.assertGreater(dealer_limit, regular_limit)
        self.assertEqual(regular_limit, 5)
        self.assertEqual(dealer_limit, 20)


# ---------------------------------------------------------------------------
# Test 20 — FraudFlag created with correct severity for duplicate_vin
# ---------------------------------------------------------------------------

class FraudFlagSeverityTest(TestCase):
    def test_duplicate_vin_flag_has_critical_severity(self):
        """Duplicate VIN fraud flag must always have critical severity."""
        from fraud.tasks import check_duplicate_vin
        from unittest.mock import patch, MagicMock

        user = _make_user('vincheck@test.com')
        listing1 = _make_listing(user, vin='ABCDEF12345678901')
        listing2 = _make_listing(user, vin=None, title='Dup Car')

        # Simulate duplicate VIN via mock (DB unique constraint prevents real duplicates)
        dup_qs = Listing.objects.filter(pk=listing2.pk)
        with patch('cars.models.Listing.objects') as mock_mgr:
            mock_mgr.get.return_value = listing1
            mock_filter = MagicMock()
            mock_filter.exclude.return_value = dup_qs
            mock_mgr.filter.return_value = mock_filter

            check_duplicate_vin(listing1.pk)

        flag = FraudFlag.objects.filter(listing=listing1, flag_type='duplicate_vin').first()
        self.assertIsNotNone(flag, 'FraudFlag should have been created for duplicate VIN')
        self.assertEqual(flag.severity, 'critical')
        self.assertTrue(flag.auto_detected)

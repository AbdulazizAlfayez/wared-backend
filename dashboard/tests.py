"""
Phase 4.4 — Dealer Analytics Tests

Covers all 7 analytics endpoints and the underlying analytics.py functions.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

User = get_user_model()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, role='importer', name='Dealer User'):
    return User.objects.create_user(
        email=email, password='testpass123', name=name, role=role,
    )


def make_listing(owner, status='approved', make='Toyota', model='Camry',
                 year=2022, price=85000, view_count=0, unique_view_count=0):
    from cars.models import Listing
    return Listing.objects.create(
        title=f'{year} {make} {model}',
        make=make, model=model, year=year, price=price,
        mileage=30000, city='Riyadh',
        owner=owner, status=status,
        is_active=True, fuel_type='petrol', transmission='automatic',
        view_count=view_count, unique_view_count=unique_view_count,
    )


def make_viewlog(listing, user=None, source='direct', ip='1.2.3.4'):
    from cars.models import ViewLog
    return ViewLog.objects.create(
        listing=listing, user=user, ip_address=ip,
        source=source, user_agent='test-agent',
    )


def make_lead(listing, buyer, dealer):
    from leads.models import Lead
    return Lead.objects.create(
        listing=listing, buyer=buyer, dealer=dealer,
        message='I am interested', status='new',
    )


def make_appointment(listing, buyer, seller, appt_date=None):
    from bookings.models import Appointment
    from django.utils import timezone
    import datetime
    if appt_date is None:
        appt_date = timezone.now().date() + datetime.timedelta(days=5)
    return Appointment.objects.create(
        listing=listing, buyer=buyer, seller=seller,
        appointment_date=appt_date,
        appointment_time=datetime.time(10, 0),
        status='pending',
    )


# ---------------------------------------------------------------------------
# Base class — sets up a dealer, a buyer, a listing, an admin
# ---------------------------------------------------------------------------

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
)
class AnalyticsBaseTestCase(TestCase):
    def setUp(self):
        self.client   = APIClient()
        self.dealer   = make_user('dealer@test.com', role='importer', name='Dealer')
        self.buyer    = make_user('buyer@test.com',  role='buyer',  name='Buyer')
        self.admin    = make_user('admin@test.com',  role='admin',  name='Admin')
        self.listing  = make_listing(self.dealer, status='approved', view_count=10, unique_view_count=8)
        self.listing2 = make_listing(self.dealer, status='approved', make='Honda',
                                     model='Civic', view_count=5, unique_view_count=4)

    def auth(self, user):
        self.client.force_authenticate(user=user)


# ===========================================================================
# 1. DealerAnalyticsOverviewView
# ===========================================================================

class DealerAnalyticsOverviewViewTests(AnalyticsBaseTestCase):

    def test_dealer_gets_overview(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('summary', data)
        self.assertIn('conversion_rates', data)
        self.assertIn('trends', data)
        self.assertIn('views_by_source', data)
        self.assertIn('peak_hours', data)

    def test_summary_totals(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/')
        s = resp.json()['summary']
        self.assertEqual(s['total_listings'], 2)
        self.assertEqual(s['total_views'], 15)    # 10 + 5

    def test_conversion_rates_keys(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/')
        cr = resp.json()['conversion_rates']
        for key in ('view_to_lead', 'lead_to_contacted', 'lead_to_closed', 'view_to_favorite'):
            self.assertIn(key, cr)

    def test_trends_include_7_and_30_days(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/')
        trends = resp.json()['trends']
        self.assertEqual(len(trends['views_last_7_days']),  7)
        self.assertEqual(len(trends['views_last_30_days']), 30)
        self.assertEqual(len(trends['leads_last_7_days']),  7)
        self.assertEqual(len(trends['leads_last_30_days']), 30)

    def test_peak_hours_has_24_entries(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/')
        self.assertEqual(len(resp.json()['peak_hours']), 24)

    def test_buyer_forbidden(self):
        self.auth(self.buyer)
        resp = self.client.get('/api/dashboard/dealer/analytics/')
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_forbidden(self):
        resp = self.client.get('/api/dashboard/dealer/analytics/')
        self.assertIn(resp.status_code, (401, 403))

    def test_admin_can_access(self):
        self.auth(self.admin)
        resp = self.client.get('/api/dashboard/dealer/analytics/')
        self.assertEqual(resp.status_code, 200)

    def test_viewlog_increases_views_by_source(self):
        make_viewlog(self.listing, source='search')
        make_viewlog(self.listing, source='search', ip='2.3.4.5')
        make_viewlog(self.listing, source='direct')
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/')
        sources = {item['source']: item['count'] for item in resp.json()['views_by_source']}
        self.assertEqual(sources.get('search', 0), 2)
        self.assertEqual(sources.get('direct', 0), 1)


# ===========================================================================
# 2. DealerListingAnalyticsView
# ===========================================================================

class DealerListingAnalyticsViewTests(AnalyticsBaseTestCase):

    def test_returns_paginated_results(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/listings/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('count', data)
        self.assertIn('results', data)
        self.assertEqual(data['count'], 2)

    def test_each_result_has_expected_keys(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/listings/')
        row = resp.json()['results'][0]
        for key in ('id', 'make', 'model', 'year', 'price', 'status', 'created_at', 'stats', 'trends'):
            self.assertIn(key, row)
        for key in ('view_count', 'lead_count', 'conversion_rate', 'days_listed', 'views_per_day'):
            self.assertIn(key, row['stats'])

    def test_ordering_by_leads(self):
        make_lead(self.listing, self.buyer, self.dealer)
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/listings/?ordering=-leads')
        first = resp.json()['results'][0]
        self.assertEqual(first['id'], self.listing.pk)

    def test_filter_by_status(self):
        make_listing(self.dealer, status='draft')
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/listings/?status=draft')
        self.assertEqual(resp.json()['count'], 1)

    def test_buyer_forbidden(self):
        self.auth(self.buyer)
        resp = self.client.get('/api/dashboard/dealer/analytics/listings/')
        self.assertEqual(resp.status_code, 403)


# ===========================================================================
# 3. SingleListingAnalyticsView
# ===========================================================================

class SingleListingAnalyticsViewTests(AnalyticsBaseTestCase):

    def test_returns_deep_analytics(self):
        self.auth(self.dealer)
        resp = self.client.get(f'/api/dashboard/dealer/analytics/listings/{self.listing.pk}/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ('listing', 'lifetime_stats', 'daily_views', 'views_by_source',
                    'views_by_hour', 'lead_timeline'):
            self.assertIn(key, data)

    def test_daily_views_has_30_entries(self):
        self.auth(self.dealer)
        resp = self.client.get(f'/api/dashboard/dealer/analytics/listings/{self.listing.pk}/')
        self.assertEqual(len(resp.json()['daily_views']), 30)

    def test_views_by_hour_has_24_entries(self):
        self.auth(self.dealer)
        resp = self.client.get(f'/api/dashboard/dealer/analytics/listings/{self.listing.pk}/')
        self.assertEqual(len(resp.json()['views_by_hour']), 24)

    def test_lead_timeline_populated(self):
        make_lead(self.listing, self.buyer, self.dealer)
        self.auth(self.dealer)
        resp = self.client.get(f'/api/dashboard/dealer/analytics/listings/{self.listing.pk}/')
        timeline = resp.json()['lead_timeline']
        self.assertEqual(len(timeline), 1)
        self.assertIn('date', timeline[0])
        self.assertIn('status', timeline[0])

    def test_dealer_cannot_access_other_dealers_listing(self):
        other_dealer = make_user('other@test.com', role='importer')
        other_listing = make_listing(other_dealer, status='approved')
        self.auth(self.dealer)
        resp = self.client.get(f'/api/dashboard/dealer/analytics/listings/{other_listing.pk}/')
        self.assertEqual(resp.status_code, 403)

    def test_invalid_listing_returns_404(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/listings/99999/')
        self.assertEqual(resp.status_code, 404)

    def test_admin_can_access_any_listing(self):
        other_dealer = make_user('other2@test.com', role='importer')
        other_listing = make_listing(other_dealer, status='approved')
        self.auth(self.admin)
        resp = self.client.get(f'/api/dashboard/dealer/analytics/listings/{other_listing.pk}/')
        self.assertEqual(resp.status_code, 200)


# ===========================================================================
# 4. DealerComparativeAnalyticsView
# ===========================================================================

class DealerComparativeAnalyticsViewTests(AnalyticsBaseTestCase):

    def test_returns_comparative_data(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/compare/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in (
            'dealer_avg_views_per_listing', 'platform_avg_views_per_listing',
            'dealer_avg_leads_per_listing', 'platform_avg_leads_per_listing',
            'dealer_avg_conversion_rate', 'platform_avg_conversion_rate',
            'performance_score',
        ):
            self.assertIn(key, data)

    def test_performance_score_valid_values(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/compare/')
        score = resp.json()['performance_score']
        self.assertIn(score, ('top_performer', 'above_average', 'below_average', 'average'))

    def test_dealer_avg_views_correct(self):
        # dealer has 2 listings: view_count 10 + 5 = 15, avg = 7.5
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/compare/')
        avg = resp.json()['dealer_avg_views_per_listing']
        self.assertAlmostEqual(avg, 7.5, places=1)

    def test_buyer_forbidden(self):
        self.auth(self.buyer)
        resp = self.client.get('/api/dashboard/dealer/analytics/compare/')
        self.assertEqual(resp.status_code, 403)


# ===========================================================================
# 5. DealerAnalyticsExportView
# ===========================================================================

class DealerAnalyticsExportViewTests(AnalyticsBaseTestCase):
    """
    Use export_format= (not format=) to avoid DRF content-negotiation.
    streaming_content consumed via hasattr guard for both streaming and buffered responses.
    """

    def _csv_content(self):
        """Return CSV body as str, works for both StreamingHttpResponse and regular Response."""
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/export/?export_format=csv')
        if hasattr(resp, 'streaming_content'):
            return b''.join(resp.streaming_content).decode('utf-8')
        return resp.content.decode('utf-8')

    def test_csv_export_returns_streaming_response(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/export/?export_format=csv')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        self.assertIn('analytics.csv', resp['Content-Disposition'])

    def test_csv_has_header_row(self):
        content = self._csv_content()
        self.assertIn('view_count', content)
        self.assertIn('lead_count', content)
        self.assertIn('conversion_rate', content)

    def test_csv_has_data_rows(self):
        content = self._csv_content()
        lines = content.strip().splitlines()
        # Header + 2 data rows
        self.assertEqual(len(lines), 3)

    def test_json_export(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/export/?export_format=json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('application/json', resp['Content-Type'])
        import json
        data = json.loads(resp.content)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)

    def test_buyer_forbidden(self):
        self.auth(self.buyer)
        resp = self.client.get('/api/dashboard/dealer/analytics/export/')
        self.assertEqual(resp.status_code, 403)


# ===========================================================================
# 6. DealerTopListingsView
# ===========================================================================

class DealerTopListingsViewTests(AnalyticsBaseTestCase):

    def test_returns_top_categories(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/top/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ('most_viewed', 'most_leads', 'best_conversion', 'needs_attention'):
            self.assertIn(key, data)

    def test_most_viewed_sorted_correctly(self):
        # listing has 10 views, listing2 has 5 — listing should be first
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/top/')
        most_viewed = resp.json()['most_viewed']
        if len(most_viewed) >= 2:
            self.assertGreaterEqual(most_viewed[0]['view_count'], most_viewed[1]['view_count'])

    def test_needs_attention_excludes_no_views(self):
        # listing with 0 views and 0 leads should not appear in needs_attention
        zero_listing = make_listing(self.dealer, status='approved', view_count=0)
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/dealer/analytics/top/')
        ids = [l['id'] for l in resp.json()['needs_attention']]
        self.assertNotIn(zero_listing.pk, ids)

    def test_buyer_forbidden(self):
        self.auth(self.buyer)
        resp = self.client.get('/api/dashboard/dealer/analytics/top/')
        self.assertEqual(resp.status_code, 403)


# ===========================================================================
# 7. AdminPlatformAnalyticsView
# ===========================================================================

class AdminPlatformAnalyticsViewTests(AnalyticsBaseTestCase):

    def test_admin_gets_platform_analytics(self):
        self.auth(self.admin)
        resp = self.client.get('/api/dashboard/admin/analytics/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ('platform_summary', 'growth', 'top_makes', 'top_cities', 'lead_funnel'):
            self.assertIn(key, data)

    def test_platform_summary_keys(self):
        self.auth(self.admin)
        resp = self.client.get('/api/dashboard/admin/analytics/')
        s = resp.json()['platform_summary']
        for key in ('total_users', 'total_importers', 'total_listings',
                    'total_active_listings', 'total_leads',
                    'total_views_today', 'total_views_this_week', 'total_views_this_month'):
            self.assertIn(key, s)

    def test_growth_trends_have_7_days(self):
        self.auth(self.admin)
        resp = self.client.get('/api/dashboard/admin/analytics/')
        g = resp.json()['growth']
        self.assertEqual(len(g['new_users_last_7_days']),    7)
        self.assertEqual(len(g['new_listings_last_7_days']), 7)
        self.assertEqual(len(g['new_leads_last_7_days']),    7)

    def test_lead_funnel_contains_total(self):
        make_lead(self.listing, self.buyer, self.dealer)
        self.auth(self.admin)
        resp = self.client.get('/api/dashboard/admin/analytics/')
        funnel = resp.json()['lead_funnel']
        self.assertGreaterEqual(funnel['total'], 1)

    def test_top_makes_present(self):
        self.auth(self.admin)
        resp = self.client.get('/api/dashboard/admin/analytics/')
        makes = resp.json()['top_makes']
        self.assertIsInstance(makes, list)
        if makes:
            self.assertIn('make', makes[0])
            self.assertIn('count', makes[0])

    def test_dealer_forbidden(self):
        self.auth(self.dealer)
        resp = self.client.get('/api/dashboard/admin/analytics/')
        self.assertEqual(resp.status_code, 403)

    def test_buyer_forbidden(self):
        self.auth(self.buyer)
        resp = self.client.get('/api/dashboard/admin/analytics/')
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_forbidden(self):
        resp = self.client.get('/api/dashboard/admin/analytics/')
        self.assertIn(resp.status_code, (401, 403))


# ===========================================================================
# 8. analytics.py unit tests
# ===========================================================================

@override_settings(
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}},
)
class AnalyticsFunctionTests(TestCase):

    def setUp(self):
        self.dealer  = make_user('d@test.com', role='importer')
        self.buyer   = make_user('b@test.com', role='buyer')
        self.listing = make_listing(self.dealer, view_count=20, unique_view_count=15)

    def test_safe_rate_zero_denominator(self):
        from dashboard.analytics import _safe_rate
        self.assertEqual(_safe_rate(10, 0), 0.0)

    def test_safe_rate_normal(self):
        from dashboard.analytics import _safe_rate
        self.assertAlmostEqual(_safe_rate(1, 4), 25.0, places=2)

    def test_fill_date_gaps_length(self):
        from dashboard.analytics import _fill_date_gaps
        result = _fill_date_gaps({}, 7)
        self.assertEqual(len(result), 7)

    def test_fill_date_gaps_fills_zeros(self):
        from dashboard.analytics import _fill_date_gaps
        result = _fill_date_gaps({}, 3)
        for entry in result:
            self.assertEqual(entry['count'], 0)

    def test_get_dealer_analytics_overview_returns_dict(self):
        from dashboard.analytics import get_dealer_analytics_overview
        result = get_dealer_analytics_overview(self.dealer)
        self.assertIsInstance(result, dict)
        self.assertIn('summary', result)

    def test_serialize_listing_analytics_keys(self):
        from dashboard.analytics import get_dealer_listing_analytics, serialize_listing_analytics
        qs = get_dealer_listing_analytics(self.dealer)
        row = serialize_listing_analytics(qs.first())
        self.assertIn('stats', row)
        self.assertIn('trends', row)
        self.assertGreater(row['stats']['days_listed'], 0)

    def test_get_comparative_analytics_structure(self):
        from dashboard.analytics import get_comparative_analytics
        result = get_comparative_analytics(self.dealer)
        self.assertIn('performance_score', result)
        self.assertIn('dealer_avg_views_per_listing', result)

    def test_get_top_performing_listings_structure(self):
        from dashboard.analytics import get_top_performing_listings
        result = get_top_performing_listings(self.dealer)
        self.assertIn('most_viewed', result)
        self.assertIn('needs_attention', result)

    def test_get_single_listing_analytics_structure(self):
        from dashboard.analytics import get_single_listing_analytics
        result = get_single_listing_analytics(self.listing)
        self.assertIn('lifetime_stats', result)
        self.assertEqual(len(result['daily_views']), 30)

    def test_get_admin_platform_analytics_structure(self):
        from dashboard.analytics import get_admin_platform_analytics
        result = get_admin_platform_analytics()
        self.assertIn('platform_summary', result)
        self.assertIn('lead_funnel', result)

    def test_viewlog_shows_in_source_breakdown(self):
        make_viewlog(self.listing, source='search')
        from dashboard.analytics import get_dealer_analytics_overview
        result = get_dealer_analytics_overview(self.dealer)
        sources = {item['source']: item['count'] for item in result['views_by_source']}
        self.assertEqual(sources.get('search', 0), 1)

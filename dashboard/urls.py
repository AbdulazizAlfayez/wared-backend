from django.urls import path
from .views import (
    DealerDashboardView,
    AdminDashboardView,
    DealerLeadStatsView,
    # Phase 4.4 analytics
    DealerAnalyticsOverviewView,
    DealerListingAnalyticsView,
    SingleListingAnalyticsView,
    DealerComparativeAnalyticsView,
    DealerAnalyticsExportView,
    DealerTopListingsView,
    AdminPlatformAnalyticsView,
)

urlpatterns = [
    path('dealer/', DealerDashboardView.as_view(), name='dashboard-dealer'),
    path('admin/', AdminDashboardView.as_view(), name='dashboard-admin'),
    path('dealer/leads/', DealerLeadStatsView.as_view(), name='dashboard-dealer-leads'),
    # Phase 4.4
    path('dealer/analytics/', DealerAnalyticsOverviewView.as_view(), name='dealer-analytics-overview'),
    path('dealer/analytics/listings/', DealerListingAnalyticsView.as_view(), name='dealer-analytics-listings'),
    path('dealer/analytics/listings/<int:pk>/', SingleListingAnalyticsView.as_view(), name='dealer-analytics-listing-detail'),
    path('dealer/analytics/compare/', DealerComparativeAnalyticsView.as_view(), name='dealer-analytics-compare'),
    path('dealer/analytics/export/', DealerAnalyticsExportView.as_view(), name='dealer-analytics-export'),
    path('dealer/analytics/top/', DealerTopListingsView.as_view(), name='dealer-analytics-top'),
    path('admin/analytics/', AdminPlatformAnalyticsView.as_view(), name='admin-platform-analytics'),
]

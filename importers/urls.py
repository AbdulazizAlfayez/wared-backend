from django.urls import path
from . import views, dashboard_views
from .views import (
    CRSubmitRenewalView, CRStatusView,
    AdminCRReviewsView, AdminCRApproveView, AdminCRRejectView, AdminCRDashboardView,
)

urlpatterns = [
    path('importers/',                    views.ImporterProfileListView.as_view(),   name='importer-list'),
    path('importers/me/',                 views.MyImporterProfileView.as_view(),     name='importer-me'),
    path('importers/<int:pk>/',           views.ImporterProfileDetailView.as_view(), name='importer-detail'),
    path('importers/<int:pk>/inventory/', views.ImporterInventoryView.as_view(),     name='importer-inventory'),
    path('importers/<int:pk>/reviews/',   views.ImporterReviewsView.as_view(),       name='importer-reviews'),
    # Dashboard
    path('dashboard/importer/',          dashboard_views.ImporterDashboardView.as_view(),  name='importer-dashboard'),
    path('dashboard/importer/pipeline/', dashboard_views.ImporterPipelineView.as_view(),   name='importer-pipeline'),
    path('dashboard/importer/orders/',   dashboard_views.ImporterOrdersView.as_view(),     name='importer-orders-dashboard'),
    path('dashboard/buyer/',             dashboard_views.BuyerDashboardView.as_view(),     name='buyer-dashboard'),
    # Phase B — CR Lifecycle
    path('importer/cr/submit-renewal/',         CRSubmitRenewalView.as_view(),    name='cr-submit-renewal'),
    path('importer/cr/status/',                 CRStatusView.as_view(),           name='cr-status'),
    path('admin/cr-reviews/',                   AdminCRReviewsView.as_view(),     name='admin-cr-reviews'),
    path('admin/cr-reviews/<int:pk>/approve/',  AdminCRApproveView.as_view(),     name='admin-cr-approve'),
    path('admin/cr-reviews/<int:pk>/reject/',   AdminCRRejectView.as_view(),      name='admin-cr-reject'),
    path('admin/cr-dashboard/',                 AdminCRDashboardView.as_view(),   name='admin-cr-dashboard'),
]

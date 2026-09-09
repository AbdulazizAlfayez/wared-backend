from django.urls import path
from . import views

urlpatterns = [
    path('admin/fraud/',              views.AdminFraudListView.as_view(),  name='fraud-list'),
    path('admin/fraud/stats/',        views.AdminFraudStatsView.as_view(), name='fraud-stats'),
    path('admin/fraud/ip-report/',    views.AdminIPReportView.as_view(),   name='fraud-ip-report'),
    path('admin/fraud/scan/',         views.AdminFraudScanView.as_view(),  name='fraud-scan'),
    path('admin/fraud/<int:pk>/',     views.AdminFraudDetailView.as_view(), name='fraud-detail'),
    path('listings/limit/',           views.UserListingLimitView.as_view(), name='listing-limit'),
]

from django.urls import path
from . import views

urlpatterns = [
    # Reporter-facing
    path('reports/',       views.ReportCreateView.as_view(),  name='report-create'),
    path('reports/mine/',  views.MyReportsView.as_view(),     name='report-list'),

    # Admin — reports
    path('admin/reports/',          views.AdminReportListView.as_view(),   name='admin-report-list'),
    path('admin/reports/stats/',    views.AdminReportStatsView.as_view(),  name='admin-report-stats'),
    path('admin/reports/<int:pk>/', views.AdminReportDetailView.as_view(), name='admin-report-detail'),
    path('admin/reports/<int:pk>/action/', views.AdminReportActionView.as_view(), name='admin-report-action'),

    # Admin — user moderation
    path('admin/moderation/users/',          views.UserModerationListCreateView.as_view(), name='admin-moderation-list'),
    path('admin/moderation/users/<int:pk>/', views.UserModerationDetailView.as_view(),     name='admin-moderation-detail'),
]

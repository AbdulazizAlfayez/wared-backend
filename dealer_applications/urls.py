from django.urls import path

from .views import (
    ImporterApplicationCreateView,
    ImporterApplicationAdminListView,
    ImporterApplicationAdminDetailView,
)

urlpatterns = [
    # Applicant-facing
    path('importer-applications/', ImporterApplicationCreateView.as_view(), name='importer-application-create'),
    # Admin-facing
    path('importer-applications/admin/', ImporterApplicationAdminListView.as_view(), name='importer-application-admin-list'),
    path('importer-applications/admin/<int:pk>/', ImporterApplicationAdminDetailView.as_view(), name='importer-application-admin-detail'),
]

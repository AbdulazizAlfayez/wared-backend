from django.urls import path
from .views import (
    CookieConsentView,
    LegalAcceptanceStatusView,
    LegalAcceptView,
    LegalDocumentHistoryView,
    LegalDocumentView,
)

urlpatterns = [
    path('legal/documents/<str:document_type>/', LegalDocumentView.as_view(), name='legal-document'),
    path('legal/documents/<str:document_type>/history/', LegalDocumentHistoryView.as_view(), name='legal-document-history'),
    path('legal/accept/', LegalAcceptView.as_view(), name='legal-accept'),
    path('legal/acceptance-status/', LegalAcceptanceStatusView.as_view(), name='legal-acceptance-status'),
    path('legal/cookie-consent/', CookieConsentView.as_view(), name='cookie-consent'),
]

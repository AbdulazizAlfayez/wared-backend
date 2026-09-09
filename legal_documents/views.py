from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from auditlog.utils import get_client_ip

from .models import CookieConsent, LegalDocument, UserLegalAcceptance
from .serializers import (
    CookieConsentSerializer,
    LegalAcceptanceStatusSerializer,
    LegalDocumentSerializer,
)


class LegalDocumentView(APIView):
    """GET /api/legal/documents/{type}/ — returns active document for given type."""
    permission_classes = [AllowAny]

    def get(self, request, document_type):
        valid_types = dict(LegalDocument.DOCUMENT_TYPES)
        if document_type not in valid_types:
            return Response({'error': f'Invalid document type. Choose from: {", ".join(valid_types)}'}, status=status.HTTP_400_BAD_REQUEST)

        doc = LegalDocument.objects.filter(document_type=document_type, is_active=True).first()
        if not doc:
            return Response({'error': 'No active document found for this type.'}, status=status.HTTP_404_NOT_FOUND)

        return Response(LegalDocumentSerializer(doc).data)


class LegalDocumentHistoryView(APIView):
    """GET /api/legal/documents/{type}/history/ — admin only, all versions."""
    permission_classes = [IsAuthenticated]

    def get(self, request, document_type):
        if request.user.role != 'admin':
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        docs = LegalDocument.objects.filter(document_type=document_type)
        return Response(LegalDocumentSerializer(docs, many=True).data)


class LegalAcceptView(APIView):
    """POST /api/legal/accept/ — accept a legal document."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        document_type = request.data.get('document_type')
        version = request.data.get('version')

        if not document_type or not version:
            return Response({'error': 'document_type and version are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            doc = LegalDocument.objects.get(document_type=document_type, version=version, is_active=True)
        except LegalDocument.DoesNotExist:
            return Response({'error': 'Document not found or not the current active version.'}, status=status.HTTP_400_BAD_REQUEST)

        acceptance, created = UserLegalAcceptance.objects.update_or_create(
            user=request.user,
            document=doc,
            defaults={
                'ip_address': get_client_ip(request),
                'user_agent': request.META.get('HTTP_USER_AGENT', ''),
            },
        )

        return Response({'detail': 'Accepted successfully.', 'document': document_type, 'version': version})


class LegalAcceptanceStatusView(APIView):
    """GET /api/legal/acceptance-status/ — check if user needs to accept new docs."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        active_docs = LegalDocument.objects.filter(is_active=True).exclude(document_type='cookie_policy')
        user_acceptances = UserLegalAcceptance.objects.filter(user=request.user).values_list('document_id', flat=True)

        needs_reacceptance = False
        doc_status = {}
        for doc in active_docs:
            accepted = doc.pk in user_acceptances
            doc_status[doc.document_type] = accepted
            if not accepted:
                needs_reacceptance = True

        return Response({
            'terms_accepted': doc_status.get('terms_of_service', False),
            'privacy_accepted': doc_status.get('privacy_policy', False),
            'needs_reacceptance': needs_reacceptance,
            'documents': [
                {
                    'document_type': doc.document_type,
                    'version': doc.version,
                    'accepted': doc.pk in user_acceptances,
                }
                for doc in active_docs
            ],
        })


class CookieConsentView(APIView):
    """POST/GET /api/legal/cookie-consent/"""
    permission_classes = [AllowAny]

    def get(self, request):
        if request.user.is_authenticated:
            consent = CookieConsent.objects.filter(user=request.user).order_by('-accepted_at').first()
        else:
            session_key = request.session.session_key
            consent = CookieConsent.objects.filter(session_key=session_key).order_by('-accepted_at').first() if session_key else None

        if not consent:
            return Response(None)
        return Response(CookieConsentSerializer(consent).data)

    def post(self, request):
        ip = get_client_ip(request)
        data = {
            'essential': True,
            'analytics': request.data.get('analytics', False),
            'marketing': request.data.get('marketing', False),
            'ip_address': ip,
        }

        if request.user.is_authenticated:
            data['user'] = request.user
        else:
            if not request.session.session_key:
                request.session.create()
            data['session_key'] = request.session.session_key

        consent = CookieConsent.objects.create(**data)
        return Response(CookieConsentSerializer(consent).data, status=status.HTTP_201_CREATED)

import re

from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

from .models import LegalDocument, UserLegalAcceptance


# Paths that are exempt from legal acceptance checks
EXEMPT_PATTERNS = [
    r'^/api/legal/',
    r'^/api/auth/',
    r'^/api/account/deletion/',
    r'^/admin/',
    r'^/health/',
]

COMPILED_EXEMPT = [re.compile(p) for p in EXEMPT_PATTERNS]


class RequireLegalAcceptanceMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return None

        # Skip exempt paths
        for pattern in COMPILED_EXEMPT:
            if pattern.match(request.path):
                return None

        # Check if there are active docs the user hasn't accepted
        active_docs = LegalDocument.objects.filter(is_active=True).exclude(document_type='cookie_policy')
        if not active_docs.exists():
            return None

        accepted_doc_ids = set(
            UserLegalAcceptance.objects.filter(user=request.user).values_list('document_id', flat=True)
        )
        unaccepted = [
            {'document_type': doc.document_type, 'version': doc.version}
            for doc in active_docs
            if doc.pk not in accepted_doc_ids
        ]

        if unaccepted:
            return JsonResponse(
                {
                    'error': 'legal_acceptance_required',
                    'message': 'You must accept the latest terms and privacy policy to continue.',
                    'documents': unaccepted,
                },
                status=403,
            )

        return None

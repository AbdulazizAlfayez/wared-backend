from rest_framework import serializers
from .models import CookieConsent, LegalDocument, UserLegalAcceptance


class LegalDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalDocument
        fields = ['id', 'document_type', 'version', 'content_en', 'content_ar', 'effective_date', 'is_active', 'created_at']


class UserLegalAcceptanceSerializer(serializers.ModelSerializer):
    document_type = serializers.CharField(source='document.document_type', read_only=True)
    version = serializers.CharField(source='document.version', read_only=True)

    class Meta:
        model = UserLegalAcceptance
        fields = ['id', 'document_type', 'version', 'accepted_at']


class LegalAcceptanceStatusSerializer(serializers.Serializer):
    terms_accepted = serializers.BooleanField()
    privacy_accepted = serializers.BooleanField()
    needs_reacceptance = serializers.BooleanField()


class CookieConsentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CookieConsent
        fields = ['id', 'essential', 'analytics', 'marketing', 'accepted_at']

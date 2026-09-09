from django.contrib import admin
from .models import CookieConsent, LegalDocument, UserLegalAcceptance


@admin.register(LegalDocument)
class LegalDocumentAdmin(admin.ModelAdmin):
    list_display = ['document_type', 'version', 'is_active', 'effective_date', 'created_at', 'acceptance_count']
    list_filter = ['document_type', 'is_active']
    search_fields = ['version']
    readonly_fields = ['created_at']

    def acceptance_count(self, obj):
        return obj.acceptances.count()
    acceptance_count.short_description = 'Acceptances'


@admin.register(UserLegalAcceptance)
class UserLegalAcceptanceAdmin(admin.ModelAdmin):
    list_display = ['user', 'document', 'accepted_at', 'ip_address']
    list_filter = ['document__document_type']
    raw_id_fields = ['user']
    readonly_fields = ['accepted_at']


@admin.register(CookieConsent)
class CookieConsentAdmin(admin.ModelAdmin):
    list_display = ['user', 'session_key', 'essential', 'analytics', 'marketing', 'accepted_at']
    list_filter = ['analytics', 'marketing']
    readonly_fields = ['accepted_at']

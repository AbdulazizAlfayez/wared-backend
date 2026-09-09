from django.contrib import admin
from .models import CRVerificationHistory, ImporterProfile


class CRVerificationHistoryInline(admin.TabularInline):
    model = CRVerificationHistory
    extra = 0
    readonly_fields = ('action', 'old_status', 'new_status', 'cr_expiry_date', 'performed_by', 'notes', 'created_at')
    ordering = ['-created_at']


@admin.register(ImporterProfile)
class ImporterProfileAdmin(admin.ModelAdmin):
    list_display = ('business_name', 'user', 'is_verified', 'cr_verification_status', 'cr_expiry_date', 'source_countries', 'average_rating', 'total_cars_imported', 'created_at')
    list_filter = ('is_verified', 'cr_verification_status', 'city')
    search_fields = ('business_name', 'user__email')
    readonly_fields = ('created_at', 'updated_at', 'average_rating', 'total_reviews', 'cr_suspended_at', 'cr_reactivated_at')
    list_editable = ('is_verified',)
    inlines = [CRVerificationHistoryInline]


@admin.register(CRVerificationHistory)
class CRVerificationHistoryAdmin(admin.ModelAdmin):
    list_display = ('importer_profile', 'action', 'old_status', 'new_status', 'cr_expiry_date', 'performed_by', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('importer_profile__business_name',)
    readonly_fields = ('created_at',)

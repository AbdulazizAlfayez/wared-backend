from django.contrib import admin

from .models import ImporterApplication


@admin.register(ImporterApplication)
class ImporterApplicationAdmin(admin.ModelAdmin):
    list_display  = ('applicant', 'business_name', 'business_type', 'city', 'status', 'created_at')
    list_filter   = ('status', 'business_type')
    search_fields = ('applicant__email', 'business_name', 'city')
    readonly_fields = ('applicant', 'business_name', 'business_type', 'city', 'phone',
                       'email', 'address', 'description', 'expected_listings', 'created_at', 'updated_at')
    fieldsets = (
        ('Applicant', {
            'fields': ('applicant', 'created_at', 'updated_at'),
        }),
        ('Business Info', {
            'fields': ('business_name', 'business_type', 'city', 'phone', 'email', 'address'),
        }),
        ('Details', {
            'fields': ('description', 'expected_listings'),
        }),
        ('Review', {
            'fields': ('status', 'admin_notes'),
        }),
    )

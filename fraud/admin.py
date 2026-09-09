from django.contrib import admin
from .models import FraudFlag, IPLog, ListingLimit


@admin.register(FraudFlag)
class FraudFlagAdmin(admin.ModelAdmin):
    list_display  = ('flag_type', 'severity', 'user', 'listing', 'is_resolved', 'auto_detected', 'created_at')
    list_filter   = ('flag_type', 'severity', 'is_resolved', 'auto_detected')
    search_fields = ('user__email', 'listing__title', 'details')
    readonly_fields = ('created_at',)


@admin.register(IPLog)
class IPLogAdmin(admin.ModelAdmin):
    list_display  = ('ip_address', 'action', 'user', 'created_at')
    list_filter   = ('action',)
    search_fields = ('ip_address', 'user__email')
    readonly_fields = ('created_at',)


@admin.register(ListingLimit)
class ListingLimitAdmin(admin.ModelAdmin):
    list_display  = ('user', 'daily_limit', 'listings_today', 'last_reset')
    search_fields = ('user__email',)

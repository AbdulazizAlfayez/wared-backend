from django.contrib import admin

from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display   = ('id', 'buyer', 'dealer', 'listing', 'status', 'source', 'created_at')
    list_filter    = ('status', 'source', 'created_at')
    search_fields  = ('buyer__email', 'buyer__name', 'listing__title')
    readonly_fields = ('buyer', 'dealer', 'listing', 'created_at', 'updated_at')

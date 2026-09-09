from django.contrib import admin

from .models import DealerSubscription, SubscriptionHistory, SubscriptionPlan


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'slug', 'monthly_price', 'annual_price',
        'max_listings', 'max_images_per_listing', 'max_featured_listings',
        'badge_type', 'is_active', 'display_order',
    ]
    list_filter  = ['is_active', 'badge_type']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    ordering = ['display_order']


@admin.register(DealerSubscription)
class DealerSubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        'dealer', 'plan', 'billing_cycle', 'status',
        'expires_at', 'auto_renew', 'created_at',
    ]
    list_filter  = ['status', 'billing_cycle', 'plan']
    search_fields = ['dealer__email', 'dealer__name']
    raw_id_fields = ['dealer']
    readonly_fields = ['started_at', 'created_at', 'updated_at']
    ordering = ['-created_at']


@admin.register(SubscriptionHistory)
class SubscriptionHistoryAdmin(admin.ModelAdmin):
    list_display  = ['dealer', 'action', 'plan', 'old_plan', 'amount_paid', 'billing_cycle', 'created_at']
    list_filter   = ['action', 'plan', 'billing_cycle']
    search_fields = ['dealer__email', 'dealer__name']
    raw_id_fields = ['dealer']
    readonly_fields = ['created_at']
    ordering = ['-created_at']

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from .models import ExchangeRate


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ('currency', 'rate_to_sar', 'previous_rate', 'source', 'updated_at', 'last_api_error_short')
    list_editable = ('rate_to_sar',)
    list_filter = ('source',)
    search_fields = ('currency',)
    readonly_fields = ('updated_at', 'last_api_attempt_at', 'previous_rate')
    ordering = ('currency',)

    change_list_template = 'admin/calculator/exchangerate_changelist.html'

    def last_api_error_short(self, obj):
        if not obj.last_api_error:
            return '—'
        return obj.last_api_error[:60] + ('...' if len(obj.last_api_error) > 60 else '')
    last_api_error_short.short_description = 'Last error'

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path('refresh-now/', self.admin_site.admin_view(self.refresh_rates_view), name='calculator_exchangerate_refresh'),
        ]
        return custom + urls

    def refresh_rates_view(self, request):
        try:
            from .tasks import update_exchange_rates
            update_exchange_rates.delay()
        except Exception:
            from .tasks import update_exchange_rates
            update_exchange_rates()
        messages.success(request, 'Exchange rate refresh queued. Check back in a few seconds.')
        return redirect('admin:calculator_exchangerate_changelist')

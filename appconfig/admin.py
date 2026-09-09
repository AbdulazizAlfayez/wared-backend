from django.contrib import admin
from .models import AppConfig


@admin.register(AppConfig)
class AppConfigAdmin(admin.ModelAdmin):
    list_display = ('min_supported_version', 'latest_version', 'maintenance_mode', 'updated_at')

    def has_add_permission(self, request):
        # Only one row allowed
        return not AppConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

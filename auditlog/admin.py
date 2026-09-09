from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'model_name', 'object_id', 'ip_address', 'timestamp')
    list_filter = ('action', 'model_name')
    search_fields = ('model_name', 'user__email')
    ordering = ('-timestamp',)
    readonly_fields = (
        'user', 'action', 'model_name', 'object_id',
        'old_value', 'new_value', 'ip_address', 'timestamp',
    )

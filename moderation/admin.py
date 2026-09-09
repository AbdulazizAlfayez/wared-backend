from django.contrib import admin
from .models import Report, UserModeration


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display  = ('id', 'report_type', 'reason', 'priority', 'status', 'reporter', 'created_at')
    list_filter   = ('status', 'priority', 'report_type', 'reason', 'action_taken')
    search_fields = ('reporter__email', 'description', 'listing__title')
    readonly_fields = ('created_at', 'updated_at', 'priority')
    ordering = ('-created_at',)
    raw_id_fields = ('reporter', 'reported_user', 'listing', 'resolved_by')

    fieldsets = (
        ('Report', {
            'fields': (
                'reporter', 'report_type', 'reason', 'description', 'evidence',
                'listing', 'reported_user', 'reported_showroom', 'reported_workshop',
            )
        }),
        ('Status', {
            'fields': ('status', 'priority', 'action_taken', 'admin_notes'),
        }),
        ('Resolution', {
            'fields': ('resolved_by', 'resolved_at'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(UserModeration)
class UserModerationAdmin(admin.ModelAdmin):
    list_display  = ('id', 'user', 'action', 'issued_by', 'is_active', 'expires_at', 'created_at')
    list_filter   = ('action', 'is_active')
    search_fields = ('user__email', 'reason')
    readonly_fields = ('created_at',)
    raw_id_fields  = ('user', 'issued_by', 'related_report')

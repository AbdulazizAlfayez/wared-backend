from django.contrib import admin
from django.db.models import Count

from auditlog.utils import get_client_ip, log_action

from .models import CommentReport, ListingComment


@admin.register(ListingComment)
class ListingCommentAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'listing', 'user', 'short_body',
        'is_hidden', 'is_deleted', 'report_total', 'created_at',
    )
    list_filter = ('is_hidden', 'is_deleted')
    search_fields = ('body', 'user__email')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-created_at',)
    raw_id_fields = ('user', 'listing', 'parent')
    actions = ('hide_selected', 'unhide_selected')

    def get_queryset(self, request):
        # Annotated so the column costs one query rather than one per row.
        return super().get_queryset(request).annotate(report_total=Count('reports'))

    @admin.display(description='Body')
    def short_body(self, obj):
        body = obj.body or ''
        return body if len(body) <= 60 else f'{body[:57]}…'

    @admin.display(description='Reports', ordering='report_total')
    def report_total(self, obj):
        return getattr(obj, 'report_total', 0)

    @admin.action(description='Hide selected comments')
    def hide_selected(self, request, queryset):
        self._set_hidden(request, queryset, True)

    @admin.action(description='Unhide selected comments')
    def unhide_selected(self, request, queryset):
        self._set_hidden(request, queryset, False)

    def _set_hidden(self, request, queryset, hidden):
        """
        Writes one audit row per comment rather than one for the batch: the log
        is read per object, and a bulk entry would be invisible from a comment.
        """
        changed = 0
        for comment in queryset:
            if comment.is_hidden == hidden:
                continue
            comment.is_hidden = hidden
            comment.save(update_fields=['is_hidden', 'updated_at'])
            log_action(
                user=request.user,
                action='status_change',
                model_name='ListingComment',
                object_id=comment.pk,
                old_value={'is_hidden': not hidden},
                new_value={'is_hidden': hidden, 'source': 'admin'},
                ip_address=get_client_ip(request),
            )
            changed += 1
        self.message_user(
            request,
            f'{changed} comment(s) {"hidden" if hidden else "unhidden"}.',
        )


@admin.register(CommentReport)
class CommentReportAdmin(admin.ModelAdmin):
    """Read-only: reports are evidence, and editing them would destroy it."""

    list_display = ('id', 'comment', 'reporter', 'reason', 'created_at')
    list_filter = ('reason',)
    search_fields = ('comment__body', 'reporter__email', 'note')
    ordering = ('-created_at',)
    raw_id_fields = ('comment', 'reporter')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

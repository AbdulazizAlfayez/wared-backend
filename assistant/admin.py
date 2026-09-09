from django.contrib import admin
from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('role', 'content', 'created_at')
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'updated_at', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('user__email', 'user__name', 'title')
    readonly_fields = ('user', 'title', 'created_at', 'updated_at')
    inlines = [MessageInline]

    def has_add_permission(self, request):
        return False


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'role', 'short_content', 'created_at')
    list_filter = ('role', 'created_at')
    readonly_fields = ('conversation', 'role', 'content', 'created_at')

    def short_content(self, obj):
        return obj.content[:80]
    short_content.short_description = 'Content'

    def has_add_permission(self, request):
        return False

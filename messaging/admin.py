from django.contrib import admin

from .models import BlockedUser, Conversation, Message


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display  = ('id', 'listing', 'buyer', 'seller', 'is_active', 'last_message_at', 'unread_count_buyer', 'unread_count_importer', 'updated_at')
    list_filter   = ('is_active', 'buyer_archived', 'seller_archived')
    search_fields = ('buyer__email', 'seller__email', 'listing__title')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display  = ('id', 'sender', 'conversation', 'contains_contact_attempt', 'message_type', 'is_read', 'is_system', 'created_at')
    list_filter   = ('contains_contact_attempt', 'is_read', 'is_system', 'message_type')
    search_fields = ('sender__email', 'content')
    readonly_fields = ('created_at', 'read_at')


@admin.register(BlockedUser)
class BlockedUserAdmin(admin.ModelAdmin):
    list_display  = ('id', 'blocker', 'blocked', 'reason', 'created_at')
    search_fields = ('blocker__email', 'blocked__email')
    readonly_fields = ('created_at',)

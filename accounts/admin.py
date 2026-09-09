from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, VerificationRequest


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin interface for User model."""
    list_display = ('email', 'name', 'role', 'is_active', 'is_staff', 'date_joined')
    list_filter = ('role', 'is_active', 'is_staff', 'date_joined')
    search_fields = ('email', 'name', 'phone')
    ordering = ('-date_joined',)
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('name', 'phone', 'role')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'name', 'password1', 'password2', 'role'),
        }),
    )


@admin.register(VerificationRequest)
class VerificationRequestAdmin(admin.ModelAdmin):
    """Admin interface for VerificationRequest model."""
    list_display = ('user', 'verification_type', 'document_number', 'status', 'created_at', 'reviewed_at')
    list_filter = ('status', 'verification_type', 'created_at')
    search_fields = ('user__email', 'user__name', 'document_number')
    ordering = ('-created_at',)
    raw_id_fields = ('user', 'reviewed_by')
    readonly_fields = ('created_at', 'reviewed_at')
    fieldsets = (
        (None, {'fields': ('user', 'verification_type', 'document_number', 'document_file')}),
        ('Status', {'fields': ('status', 'rejection_reason', 'reviewed_by', 'reviewed_at')}),
        ('Notes', {'fields': ('notes',)}),
        ('Timestamps', {'fields': ('created_at',)}),
    )



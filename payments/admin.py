from django.contrib import admin
from .models import PaymentTransaction


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'amount', 'payment_type', 'method', 'status', 'provider', 'created_at')
    list_filter = ('status', 'method', 'payment_type', 'provider')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('user', 'reservation', 'order')

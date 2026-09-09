from django.contrib import admin
from django.db.models import Sum

from .models import ImportOrder, ImportTimeline, OrderDocument, Reservation


@admin.register(ImportOrder)
class ImportOrderAdmin(admin.ModelAdmin):
    list_display  = [
        'order_number', 'car', 'buyer', 'importer',
        'status', 'total_price', 'deposit_paid', 'created_at',
    ]
    list_filter   = ['status', 'delivery_method', 'deposit_paid', 'created_at']
    search_fields = [
        'order_number', 'buyer__email', 'buyer__name',
        'importer__email', 'importer__name',
        'car__title', 'car__make', 'car__model',
    ]
    readonly_fields = ['order_number', 'created_at', 'updated_at']
    ordering        = ['-created_at']


@admin.register(ImportTimeline)
class ImportTimelineAdmin(admin.ModelAdmin):
    list_display  = ['order', 'event_type', 'title', 'is_public', 'date', 'created_by']
    list_filter   = ['event_type', 'is_public', 'date']
    search_fields = ['order__order_number', 'title', 'description']
    ordering      = ['-date']


@admin.register(OrderDocument)
class OrderDocumentAdmin(admin.ModelAdmin):
    list_display  = ['order', 'document_type', 'title', 'uploaded_by', 'is_buyer_visible', 'created_at']
    list_filter   = ['document_type', 'is_buyer_visible', 'created_at']
    search_fields = ['order__order_number', 'title', 'notes']
    ordering      = ['-created_at']


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display  = [
        'reservation_number', 'car', 'buyer', 'importer',
        'status', 'payment_status', 'platform_fee_sar', 'paid_at', 'created_at',
    ]
    list_filter   = ['status', 'payment_status', 'payment_method', 'created_at']
    search_fields = [
        'reservation_number', 'buyer__email', 'buyer__name',
        'importer__email', 'importer__name',
        'car__title', 'car__make', 'car__model',
    ]
    readonly_fields = ['reservation_number', 'payment_reference', 'created_at', 'updated_at']
    ordering        = ['-created_at']

    def changelist_view(self, request, extra_context=None):
        total_revenue = Reservation.objects.filter(
            payment_status='succeeded'
        ).aggregate(total=Sum('platform_fee_sar'))['total'] or 0
        extra_context = extra_context or {}
        extra_context['total_platform_revenue'] = f'{total_revenue:,.2f} SAR'
        return super().changelist_view(request, extra_context=extra_context)

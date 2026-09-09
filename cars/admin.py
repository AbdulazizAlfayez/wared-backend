from django.contrib import admin
from .models import (
    Car, CarImage, Listing, Showroom, ShowroomWorkingHours, ShowroomBranch, ShowroomReview,
    Workshop, WorkshopWorkingHours, WorkshopService, WorkshopReview,
    BulkUpload,
)


class CarImageInline(admin.TabularInline):
    """Inline admin for CarImage."""
    model = CarImage
    extra = 1


@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    """Admin interface for Car model."""
    list_display = ('title', 'make', 'model', 'year', 'price', 'status', 'seller', 'created_at')
    list_filter = ('status', 'condition', 'fuel_type', 'transmission', 'make', 'year', 'created_at')
    search_fields = ('title', 'make', 'model', 'description', 'location')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [CarImageInline]
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('title', 'description', 'seller')
        }),
        ('Car Details', {
            'fields': ('make', 'model', 'year', 'price', 'mileage', 'color')
        }),
        ('Technical Details', {
            'fields': ('fuel_type', 'transmission', 'condition')
        }),
        ('Location & Status', {
            'fields': ('location', 'status')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(CarImage)
class CarImageAdmin(admin.ModelAdmin):
    """Admin interface for CarImage model."""
    list_display = ('car', 'image', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('car__title', 'car__make', 'car__model')


@admin.register(Showroom)
class ShowroomAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'verified', 'owner', 'created_at')
    list_filter = ('verified', 'city')
    search_fields = ('name', 'city', 'address')


@admin.register(ShowroomWorkingHours)
class ShowroomWorkingHoursAdmin(admin.ModelAdmin):
    list_display  = ('showroom', 'day', 'opening_time', 'closing_time', 'is_closed')
    list_filter   = ('is_closed', 'day')
    search_fields = ('showroom__name',)


@admin.register(ShowroomBranch)
class ShowroomBranchAdmin(admin.ModelAdmin):
    list_display  = ('showroom', 'name', 'city_obj', 'is_main', 'is_active', 'created_at')
    list_filter   = ('is_main', 'is_active')
    search_fields = ('showroom__name', 'name', 'address')


@admin.register(ShowroomReview)
class ShowroomReviewAdmin(admin.ModelAdmin):
    list_display  = ('showroom', 'user', 'rating', 'is_approved', 'created_at')
    list_filter   = ('is_approved', 'rating')
    search_fields = ('showroom__name', 'user__email', 'title')


@admin.register(Workshop)
class WorkshopAdmin(admin.ModelAdmin):
    list_display  = ('name', 'city', 'is_verified', 'is_active', 'average_rating', 'owner', 'created_at')
    list_filter   = ('is_verified', 'is_active', 'city')
    search_fields = ('name', 'city', 'description')


@admin.register(WorkshopWorkingHours)
class WorkshopWorkingHoursAdmin(admin.ModelAdmin):
    list_display  = ('workshop', 'day', 'opening_time', 'closing_time', 'is_closed')
    list_filter   = ('is_closed', 'day')
    search_fields = ('workshop__name',)


@admin.register(WorkshopService)
class WorkshopServiceAdmin(admin.ModelAdmin):
    list_display  = ('workshop', 'name', 'category', 'price', 'price_type', 'is_active', 'order')
    list_filter   = ('category', 'is_active', 'price_type')
    search_fields = ('workshop__name', 'name', 'description')


@admin.register(WorkshopReview)
class WorkshopReviewAdmin(admin.ModelAdmin):
    list_display  = ('workshop', 'user', 'rating', 'is_approved', 'created_at')
    list_filter   = ('is_approved', 'rating')
    search_fields = ('workshop__name', 'user__email', 'title')


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    """Admin interface for Listing model."""
    list_display = (
        'title', 'make', 'model', 'year', 'price', 'status',
        'import_status', 'source_country', 'gcc_specs',
        'owner', 'showroom', 'workshop', 'created_at',
    )
    list_filter = (
        'status', 'make', 'year', 'created_at',
        'import_status', 'source_country', 'spec_origin', 'gcc_specs', 'has_salvage_title',
    )
    search_fields = ('title', 'make', 'model', 'city', 'description')
    readonly_fields = ('created_at',)
    fieldsets = (
        ('Basic Information', {
            'fields': (
                'title', 'make', 'model', 'year', 'price', 'mileage',
                'city', 'description', 'vin', 'color', 'status', 'owner', 'showroom', 'workshop',
            ),
        }),
        ('Specs', {
            'classes': ('collapse',),
            'fields': (
                'body_type', 'drive_type', 'fuel_type', 'transmission', 'condition',
                'engine_size', 'horsepower', 'cylinders', 'seats', 'doors', 'color_interior',
            ),
        }),
        ('Saudi-specific', {
            'classes': ('collapse',),
            'fields': (
                'imported_from', 'customs_cleared', 'accident_history', 'accident_description',
                'warranty_remaining', 'service_history', 'negotiable',
            ),
        }),
        ('Location', {
            'classes': ('collapse',),
            'fields': ('city_obj', 'latitude', 'longitude'),
        }),
        ('Import Information', {
            'classes': ('collapse',),
            'fields': (
                'source_country', 'source_city', 'auction_source', 'auction_lot_number',
                'original_listing_url', 'source_price', 'source_currency', 'import_status',
                'vessel_name', 'shipping_line', 'bill_of_lading_number', 'container_number',
                'port_of_origin', 'port_of_entry', 'estimated_arrival_date', 'actual_arrival_date',
                'customs_declaration_number', 'customs_clearance_date', 'conformity_certificate_number',
                'vehicle_inspection_result', 'vehicle_inspection_date',
                'gcc_specs', 'spec_origin', 'odometer_verified', 'emissions_compliant',
                'carfax_url', 'autocheck_url', 'has_salvage_title', 'has_flood_damage',
                'has_frame_damage', 'damage_description',
            ),
        }),
        ('Cost Breakdown (SAR)', {
            'classes': ('collapse',),
            'fields': (
                'shipping_cost', 'customs_duty_amount', 'vat_amount',
                'inspection_fee', 'transportation_cost', 'total_landed_cost', 'final_price_sar',
            ),
        }),
        ('Status Tracking', {
            'classes': ('collapse',),
            'fields': ('rejection_reason', 'admin_notes', 'status_changed_at', 'status_changed_by'),
        }),
        ('Promotion', {
            'classes': ('collapse',),
            'fields': (
                'is_featured', 'is_highlighted', 'is_top_search', 'is_homepage',
                'promotion_priority', 'promotion_expires_at',
            ),
        }),
        ('Timestamps', {
            'classes': ('collapse',),
            'fields': ('created_at', 'approved_at', 'approved_by'),
        }),
    )


@admin.register(BulkUpload)
class BulkUploadAdmin(admin.ModelAdmin):
    list_display  = ('id', 'dealer', 'file_name', 'status', 'total_rows', 'successful_rows', 'failed_rows', 'created_at', 'completed_at')
    list_filter   = ('status', 'created_at')
    search_fields = ('dealer__email', 'file_name')
    readonly_fields = ('created_at', 'completed_at', 'errors')

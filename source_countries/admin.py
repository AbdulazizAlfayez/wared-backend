from django.contrib import admin

from .models import SourceCountry


@admin.register(SourceCountry)
class SourceCountryAdmin(admin.ModelAdmin):
    list_display = [
        "flag_emoji",
        "code",
        "name_en",
        "name_ar",
        "iso_code",
        "avg_shipping_days",
        "avg_shipping_cost_sar",
        "is_active",
        "display_order",
    ]
    list_filter = ["is_active"]
    search_fields = ["code", "name_en", "name_ar", "iso_code"]
    list_editable = ["is_active", "display_order"]
    ordering = ["display_order", "name_en"]

    fieldsets = (
        (
            "Identity",
            {"fields": ("code", "name_en", "name_ar", "iso_code", "flag_emoji")},
        ),
        (
            "Geographic",
            {"fields": ("latitude", "longitude")},
        ),
        (
            "Shipping",
            {"fields": ("avg_shipping_cost_sar", "avg_shipping_days")},
        ),
        (
            "Content",
            {"fields": ("description", "description_ar")},
        ),
        (
            "Admin Controls",
            {"fields": ("is_active", "display_order")},
        ),
    )

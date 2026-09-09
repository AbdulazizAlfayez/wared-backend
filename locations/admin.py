from django.contrib import admin
from django.db.models import Count

from .models import City, Region


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display   = ('name_en', 'name_ar', 'slug', 'cities_count')
    search_fields  = ('name_en', 'name_ar', 'slug')
    prepopulated_fields = {'slug': ('name_en',)}

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_cities=Count('cities'))

    @admin.display(description='Cities', ordering='_cities')
    def cities_count(self, obj):
        return obj._cities


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display   = ('name_en', 'name_ar', 'region', 'latitude', 'longitude')
    list_filter    = ('region',)
    search_fields  = ('name_en', 'name_ar', 'slug')
    prepopulated_fields = {'slug': ('name_en',)}

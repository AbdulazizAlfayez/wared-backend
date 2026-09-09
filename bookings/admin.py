from django.contrib import admin

from .models import Appointment, ServiceBooking


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display  = ('id', 'listing', 'buyer', 'seller', 'appointment_date', 'appointment_time', 'status')
    list_filter   = ('status', 'appointment_date')
    search_fields = ('buyer__email', 'seller__email', 'listing__title')
    readonly_fields = ('created_at', 'updated_at', 'buyer', 'seller', 'listing')
    ordering      = ('-appointment_date', '-appointment_time')


@admin.register(ServiceBooking)
class ServiceBookingAdmin(admin.ModelAdmin):
    list_display  = ('id', 'workshop', 'service', 'customer', 'booking_date', 'booking_time', 'status')
    list_filter   = ('status', 'booking_date')
    search_fields = ('customer__email', 'workshop__name', 'vehicle_make', 'vehicle_model')
    readonly_fields = ('created_at', 'updated_at')
    ordering      = ('-booking_date', '-booking_time')

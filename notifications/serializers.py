from rest_framework import serializers

from .models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    listing_summary = serializers.SerializerMethodField(read_only=True)
    icon_hint = serializers.SerializerMethodField(read_only=True)
    related_object_id = serializers.SerializerMethodField(read_only=True)
    related_object_type = serializers.SerializerMethodField(read_only=True)

    # Map notification_type to mobile icon_hint values
    ICON_HINT_MAP = {
        'listing_approved': 'reservation_accepted',
        'listing_rejected': 'reservation_rejected',
        'appointment_confirmed': 'reservation_accepted',
        'appointment_rejected': 'reservation_rejected',
        'appointment_cancelled': 'reservation_expired',
        'new_message': 'new_message',
        'conversation_started': 'new_message',
        'new_lead': 'order_status_update',
        'lead_status_changed': 'order_status_update',
        'price_drop': 'price_drop',
        'listing_expiring': 'new_listing',
        'verification_approved': 'reservation_accepted',
        'verification_rejected': 'reservation_rejected',
        'admin_alert': 'system',
        'user_blocked': 'system',
        'system': 'system',
    }

    class Meta:
        model  = Notification
        fields = (
            'id', 'notification_type', 'title', 'message', 'is_read',
            'icon_hint', 'related_object_id', 'related_object_type',
            'listing_id', 'lead_id', 'appointment_id',
            'listing_summary', 'metadata', 'created_at',
        )
        read_only_fields = (
            'id', 'notification_type', 'title', 'message',
            'icon_hint', 'related_object_id', 'related_object_type',
            'listing_id', 'lead_id', 'appointment_id',
            'listing_summary', 'metadata', 'created_at',
        )

    def get_icon_hint(self, obj):
        return self.ICON_HINT_MAP.get(obj.notification_type, 'system')

    def get_related_object_id(self, obj):
        return obj.listing_id or obj.lead_id or obj.appointment_id or None

    def get_related_object_type(self, obj):
        if obj.listing_id:
            return 'listing'
        if obj.lead_id:
            return 'reservation'
        if obj.appointment_id:
            return 'reservation'
        if obj.notification_type in ('new_message', 'conversation_started'):
            return 'conversation'
        return 'system'

    def get_listing_summary(self, obj):
        if not obj.listing_id:
            return None
        listing = obj.listing
        if not listing:
            return None
        return {
            'id':    listing.id,
            'make':  listing.make,
            'model': listing.model,
            'year':  listing.year,
        }


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """
    Serializer for GET/PATCH /api/notifications/preferences/
    All boolean toggle fields are writable; user and id are read-only.
    """

    class Meta:
        model  = NotificationPreference
        fields = (
            'id',
            # In-app toggles
            'listing_approved', 'listing_rejected',
            'new_lead', 'lead_status_changed',
            'new_message', 'conversation_started',
            'appointment_confirmed', 'appointment_rejected', 'appointment_cancelled',
            'price_drop', 'listing_expiring', 'system',
            # Channel toggles
            'email_notifications', 'sms_notifications',
        )
        read_only_fields = ('id',)

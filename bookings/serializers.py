from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from cars.models import Listing
from .models import Appointment, _APPOINTMENT_VALID_TRANSITIONS

User = get_user_model()


# ---------------------------------------------------------------------------
# Nested brief serializers (used inside AppointmentListSerializer)
# ---------------------------------------------------------------------------

class UserBriefSerializer(serializers.ModelSerializer):
    """Minimal user representation for embedded buyer/seller fields."""

    class Meta:
        model = User
        fields = ('id', 'name', 'email')
        read_only_fields = fields


class ListingBriefSerializer(serializers.ModelSerializer):
    """Minimal listing representation with primary image URL."""

    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = ('id', 'make', 'model', 'year', 'price', 'city', 'primary_image')

    def get_primary_image(self, obj):
        # Use prefetch cache — avoid extra queries on lists.
        images = list(obj.images.all())
        primary = next((img for img in images if img.is_primary), None)
        if primary is None and images:
            primary = images[0]
        if primary and primary.image:
            return primary.image.url
        return None


# ---------------------------------------------------------------------------
# Create serializer (buyer-facing)
# ---------------------------------------------------------------------------

class AppointmentCreateSerializer(serializers.ModelSerializer):
    """
    POST /api/appointments/ — buyer creates an appointment.
    buyer and seller are auto-set in the view.
    """

    class Meta:
        model = Appointment
        fields = (
            'listing',
            'appointment_date',
            'appointment_time',
            'end_time',
            'location',
            'notes',
        )

    def validate_listing(self, value):
        if not value.is_active:
            raise serializers.ValidationError('This listing is not active.')
        if value.status != 'approved':
            raise serializers.ValidationError(
                'Appointments can only be booked for approved listings.'
            )
        return value

    def validate_appointment_date(self, value):
        if value < timezone.now().date():
            raise serializers.ValidationError('Appointment date cannot be in the past.')
        return value

    def validate(self, attrs):
        request = self.context.get('request')
        listing = attrs.get('listing')
        if request and listing:
            buyer = request.user
            # Cannot book own listing
            if listing.owner_id == buyer.pk:
                raise serializers.ValidationError(
                    {'listing': 'You cannot book an appointment for your own listing.'}
                )
            # Max 3 pending per buyer per listing
            pending_count = Appointment.objects.filter(
                buyer=buyer,
                listing=listing,
                status='pending',
            ).count()
            if pending_count >= 3:
                raise serializers.ValidationError(
                    'Maximum 3 pending appointments per listing. '
                    'Please wait for existing ones to be resolved.'
                )
        return attrs


# ---------------------------------------------------------------------------
# List / retrieve serializer (read-only, nested data)
# ---------------------------------------------------------------------------

class AppointmentListSerializer(serializers.ModelSerializer):
    """
    Full read-only representation returned on list, retrieve, and after update.
    """

    listing = ListingBriefSerializer(read_only=True)
    buyer   = UserBriefSerializer(read_only=True)
    seller  = UserBriefSerializer(read_only=True)

    class Meta:
        model = Appointment
        fields = (
            'id',
            'listing',
            'buyer',
            'seller',
            'appointment_date',
            'appointment_time',
            'end_time',
            'location',
            'notes',
            'seller_notes',
            'status',
            'cancellation_reason',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Update serializer (status transitions + notes)
# ---------------------------------------------------------------------------

class AppointmentUpdateSerializer(serializers.ModelSerializer):
    """
    PATCH /api/appointments/{id}/ — status changes and notes.
    Role-based field restrictions are enforced in the view.
    """

    class Meta:
        model = Appointment
        fields = ('status', 'seller_notes', 'cancellation_reason')

    def validate_status(self, value):
        instance = self.instance
        if not instance or value == instance.status:
            return value

        allowed = _APPOINTMENT_VALID_TRANSITIONS.get(instance.status, set())
        if value not in allowed:
            if not allowed:
                raise serializers.ValidationError(
                    f"Status '{instance.status}' is terminal — no further changes allowed."
                )
            raise serializers.ValidationError(
                f"Invalid transition from '{instance.status}' to '{value}'. "
                f"Allowed: {sorted(allowed)}."
            )
        return value

from rest_framework import serializers

from .models import ImporterApplication


class ImporterApplicationCreateSerializer(serializers.ModelSerializer):
    """Serializer for the applicant-facing POST /api/importer-applications/ endpoint."""

    class Meta:
        model  = ImporterApplication
        fields = (
            'id',
            'business_name',
            'business_type',
            'city',
            'phone',
            'email',
            'address',
            'description',
            'expected_listings',
            'status',
            'created_at',
        )
        read_only_fields = ('id', 'status', 'created_at')

    def validate_business_type(self, value):
        allowed = {'individual', 'dealership', 'showroom'}
        if value not in allowed:
            raise serializers.ValidationError(
                f"business_type must be one of: {sorted(allowed)}."
            )
        return value


class ImporterApplicationAdminSerializer(serializers.ModelSerializer):
    """Full serializer for admin list / detail / update."""

    applicant_email = serializers.EmailField(source='applicant.email', read_only=True)
    applicant_name  = serializers.CharField(source='applicant.name',  read_only=True)

    class Meta:
        model  = ImporterApplication
        fields = (
            'id',
            'applicant', 'applicant_email', 'applicant_name',
            'business_name', 'business_type',
            'city', 'phone', 'email', 'address', 'description', 'expected_listings',
            'status', 'admin_notes',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'applicant', 'applicant_email', 'applicant_name',
            'business_name', 'business_type',
            'city', 'phone', 'email', 'address', 'description', 'expected_listings',
            'created_at', 'updated_at',
        )


# Backwards-compatible aliases (used in dashboard/views.py import)
DealerApplicationCreateSerializer = ImporterApplicationCreateSerializer
DealerApplicationAdminSerializer = ImporterApplicationAdminSerializer

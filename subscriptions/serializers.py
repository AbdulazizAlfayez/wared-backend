from rest_framework import serializers

from .models import DealerSubscription, SubscriptionHistory, SubscriptionPlan


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    features = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPlan
        fields = [
            'id', 'name', 'slug', 'description', 'description_ar',
            'monthly_price', 'annual_price',
            'max_listings', 'max_images_per_listing', 'max_featured_listings',
            'features', 'is_active', 'badge_type', 'display_order',
        ]

    def get_features(self, obj):
        features = []
        if obj.max_listings == 0:
            features.append("Unlimited listings")
        else:
            features.append(f"Up to {obj.max_listings} listings")
        features.append(f"Up to {obj.max_images_per_listing} images per listing")
        if obj.max_featured_listings > 0:
            features.append(f"{obj.max_featured_listings} featured listing{'s' if obj.max_featured_listings != 1 else ''}")
        if obj.can_access_analytics:
            features.append("Analytics dashboard")
        if obj.can_bulk_upload:
            features.append("Bulk upload")
        if obj.can_have_showroom:
            features.append("Showroom page")
        if obj.can_have_workshop:
            features.append("Workshop page")
        if obj.priority_support:
            features.append("Priority support")
        return features


class DealerSubscriptionSerializer(serializers.ModelSerializer):
    plan          = SubscriptionPlanSerializer(read_only=True)
    days_remaining = serializers.IntegerField(read_only=True)
    is_expired    = serializers.BooleanField(read_only=True)
    is_trial      = serializers.BooleanField(read_only=True)

    class Meta:
        model = DealerSubscription
        fields = [
            'id', 'plan', 'billing_cycle', 'status',
            'started_at', 'expires_at', 'cancelled_at',
            'auto_renew', 'trial_ends_at',
            'days_remaining', 'is_expired', 'is_trial',
            'created_at', 'updated_at',
        ]


class SubscriptionHistorySerializer(serializers.ModelSerializer):
    plan_name     = serializers.CharField(source='plan.name', read_only=True)
    old_plan_name = serializers.CharField(source='old_plan.name', read_only=True, allow_null=True)

    class Meta:
        model = SubscriptionHistory
        fields = [
            'id', 'plan_name', 'action', 'old_plan_name',
            'amount_paid', 'billing_cycle', 'notes', 'created_at',
        ]


class SubscribeSerializer(serializers.Serializer):
    plan_id       = serializers.IntegerField()
    billing_cycle = serializers.ChoiceField(choices=['monthly', 'annual'], default='monthly')

    def validate_plan_id(self, value):
        try:
            plan = SubscriptionPlan.objects.get(pk=value, is_active=True)
        except SubscriptionPlan.DoesNotExist:
            raise serializers.ValidationError("Plan not found or is not active.")
        self.context['plan'] = plan
        return value


class AdminSubscriptionSerializer(serializers.ModelSerializer):
    plan        = SubscriptionPlanSerializer(read_only=True)
    plan_id     = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionPlan.objects.filter(is_active=True),
        source='plan', write_only=True, required=False,
    )
    dealer_email = serializers.EmailField(source='dealer.email', read_only=True)
    dealer_name  = serializers.CharField(source='dealer.name', read_only=True)

    class Meta:
        model = DealerSubscription
        fields = [
            'id', 'dealer_email', 'dealer_name',
            'plan', 'plan_id', 'billing_cycle', 'status',
            'started_at', 'expires_at', 'cancelled_at',
            'auto_renew', 'days_remaining', 'is_expired',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['started_at', 'created_at', 'updated_at']

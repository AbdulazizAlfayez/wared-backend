from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from .models import Report, UserModeration


# ---------------------------------------------------------------------------
# Nested helpers
# ---------------------------------------------------------------------------

class ListingBriefSerializer(serializers.Serializer):
    id    = serializers.IntegerField()
    make  = serializers.CharField()
    model = serializers.CharField()
    year  = serializers.IntegerField()
    title = serializers.CharField()


class UserBriefSerializer(serializers.Serializer):
    id    = serializers.IntegerField()
    email = serializers.EmailField()
    name  = serializers.CharField()


# ---------------------------------------------------------------------------
# Reporter-facing
# ---------------------------------------------------------------------------

class ReportCreateSerializer(serializers.Serializer):
    report_type          = serializers.ChoiceField(choices=Report.REPORT_TYPE_CHOICES)
    reason               = serializers.ChoiceField(choices=Report.REASON_CHOICES)
    description          = serializers.CharField(max_length=1000)
    listing_id           = serializers.IntegerField(required=False, allow_null=True)
    reported_user_id     = serializers.IntegerField(required=False, allow_null=True)
    reported_showroom_id = serializers.IntegerField(required=False, allow_null=True)
    reported_workshop_id = serializers.IntegerField(required=False, allow_null=True)
    evidence             = serializers.FileField(required=False, allow_null=True)

    def validate(self, data):
        reporter     = self.context['request'].user
        report_type  = data.get('report_type')
        reason       = data.get('reason')
        listing_id   = data.get('listing_id')
        reported_uid = data.get('reported_user_id')

        # Target requirements by type
        if report_type == 'listing' and not listing_id:
            raise serializers.ValidationError({"listing_id": "Required for listing reports."})
        if report_type == 'user' and not reported_uid:
            raise serializers.ValidationError({"reported_user_id": "Required for user reports."})

        # Cannot report yourself
        if reported_uid and reported_uid == reporter.id:
            raise serializers.ValidationError("You cannot report yourself.")

        # Resolve listing + own-listing check
        if listing_id:
            from cars.models import Listing
            try:
                listing = Listing.objects.select_related('owner').get(pk=listing_id)
            except Listing.DoesNotExist:
                raise serializers.ValidationError({"listing_id": "Listing not found."})
            if listing.owner_id == reporter.id:
                raise serializers.ValidationError("You cannot report your own listing.")
            data['_listing'] = listing

        # Duplicate report within 24 h
        cutoff = timezone.now() - timedelta(hours=24)
        qs = Report.objects.filter(reporter=reporter, reason=reason, created_at__gte=cutoff)
        if listing_id:
            qs = qs.filter(listing_id=listing_id)
        elif reported_uid:
            qs = qs.filter(reported_user_id=reported_uid)
        if qs.exists():
            raise serializers.ValidationError(
                "You have already submitted a similar report within the last 24 hours."
            )

        return data

    def create(self, validated_data):
        listing              = validated_data.pop('_listing', None)
        listing_id           = validated_data.pop('listing_id', None)
        reported_user_id     = validated_data.pop('reported_user_id', None)
        reported_showroom_id = validated_data.pop('reported_showroom_id', None)
        reported_workshop_id = validated_data.pop('reported_workshop_id', None)

        return Report.objects.create(
            reporter=self.context['request'].user,
            listing=listing,
            reported_user_id=reported_user_id,
            reported_showroom_id=reported_showroom_id,
            reported_workshop_id=reported_workshop_id,
            **validated_data,
        )


class ReportListSerializer(serializers.ModelSerializer):
    listing      = serializers.SerializerMethodField()
    reported_user = serializers.SerializerMethodField()

    class Meta:
        model  = Report
        fields = [
            'id', 'report_type', 'reason', 'description',
            'status', 'priority', 'action_taken',
            'listing', 'reported_user',
            'created_at',
        ]

    def get_listing(self, obj):
        if obj.listing:
            return {
                'id':    obj.listing.id,
                'make':  obj.listing.make,
                'model': obj.listing.model,
                'year':  obj.listing.year,
                'title': obj.listing.title,
            }
        return None

    def get_reported_user(self, obj):
        if obj.reported_user:
            return {
                'id':    obj.reported_user.id,
                'email': obj.reported_user.email,
                'name':  obj.reported_user.name,
            }
        return None


# ---------------------------------------------------------------------------
# Admin-facing
# ---------------------------------------------------------------------------

class AdminReportSerializer(serializers.ModelSerializer):
    listing       = serializers.SerializerMethodField()
    reported_user = serializers.SerializerMethodField()
    reporter      = serializers.SerializerMethodField()
    resolved_by   = serializers.SerializerMethodField()
    evidence_url  = serializers.SerializerMethodField()

    class Meta:
        model  = Report
        fields = [
            'id', 'report_type', 'reason', 'description',
            'status', 'priority', 'admin_notes', 'action_taken',
            'listing', 'reported_user', 'reported_showroom_id', 'reported_workshop_id',
            'reporter', 'evidence_url',
            'resolved_by', 'resolved_at',
            'created_at', 'updated_at',
        ]

    def get_listing(self, obj):
        if obj.listing:
            return {
                'id':    obj.listing.id,
                'make':  obj.listing.make,
                'model': obj.listing.model,
                'year':  obj.listing.year,
                'title': obj.listing.title,
                'owner_id': obj.listing.owner_id,
            }
        return None

    def get_reported_user(self, obj):
        if obj.reported_user:
            return {
                'id':             obj.reported_user.id,
                'email':          obj.reported_user.email,
                'name':           obj.reported_user.name,
                'is_banned':      obj.reported_user.is_banned,
                'is_suspended':   obj.reported_user.is_suspended,
                'warning_count':  obj.reported_user.warning_count,
            }
        return None

    def get_reporter(self, obj):
        return {
            'id':    obj.reporter.id,
            'email': obj.reporter.email,
            'name':  obj.reporter.name,
        }

    def get_resolved_by(self, obj):
        if obj.resolved_by:
            return {'id': obj.resolved_by.id, 'email': obj.resolved_by.email}
        return None

    def get_evidence_url(self, obj):
        if obj.evidence:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.evidence.url)
            return obj.evidence.url
        return None


class AdminReportActionSerializer(serializers.Serializer):
    STATUS_TRANSITIONS = {
        'pending':       {'investigating', 'resolved', 'dismissed'},
        'investigating': {'resolved', 'dismissed'},
        'resolved':      set(),
        'dismissed':     set(),
    }

    status       = serializers.ChoiceField(choices=Report.STATUS_CHOICES, required=False)
    action_taken = serializers.ChoiceField(choices=Report.ACTION_CHOICES, required=False)
    admin_notes  = serializers.CharField(required=False, allow_blank=True)
    suspended_until = serializers.DateTimeField(required=False, allow_null=True)  # for user_suspended

    def validate(self, data):
        report = self.context['report']
        new_status = data.get('status')

        if new_status and new_status != report.status:
            allowed = self.STATUS_TRANSITIONS.get(report.status, set())
            if new_status not in allowed:
                raise serializers.ValidationError(
                    f"Cannot transition from '{report.status}' to '{new_status}'."
                )

        if data.get('action_taken') == 'user_suspended' and not data.get('suspended_until'):
            raise serializers.ValidationError(
                {"suspended_until": "Required when action_taken is 'user_suspended'."}
            )

        return data


# ---------------------------------------------------------------------------
# UserModeration
# ---------------------------------------------------------------------------

class UserModerationSerializer(serializers.ModelSerializer):
    issued_by   = serializers.SerializerMethodField()
    user_email  = serializers.SerializerMethodField()

    class Meta:
        model  = UserModeration
        fields = [
            'id', 'user', 'user_email', 'action', 'reason',
            'related_report', 'issued_by',
            'expires_at', 'is_active', 'created_at',
        ]
        read_only_fields = ['issued_by', 'is_active', 'created_at']

    def get_issued_by(self, obj):
        if obj.issued_by:
            return {'id': obj.issued_by.id, 'email': obj.issued_by.email}
        return None

    def get_user_email(self, obj):
        return obj.user.email if obj.user else None

    def validate(self, data):
        action     = data.get('action')
        expires_at = data.get('expires_at')
        if action == 'suspension' and not expires_at:
            raise serializers.ValidationError(
                {"expires_at": "Required for temporary suspensions."}
            )
        return data

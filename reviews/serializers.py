from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from .models import Review, ReviewReply

# Statuses that allow a buyer to leave a review
REVIEWABLE_ORDER_STATUSES = {'delivered', 'completed'}

BANNED_WORDS = ['spam', 'fake', 'scam']  # extend as needed


class ReviewReplySerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model  = ReviewReply
        fields = ['id', 'author_name', 'comment', 'created_at', 'updated_at']

    def get_author_name(self, obj):
        return obj.author.name if obj.author else ''


class ReviewSerializer(serializers.ModelSerializer):
    reviewer_name = serializers.SerializerMethodField()
    reply         = ReviewReplySerializer(read_only=True)
    can_edit      = serializers.SerializerMethodField()
    listing_title = serializers.SerializerMethodField()
    order_number  = serializers.SerializerMethodField()

    class Meta:
        model  = Review
        fields = [
            'id', 'reviewer_name', 'reviewed_user', 'listing', 'listing_title',
            'order', 'order_number',
            'review_type', 'rating',
            'communication_rating', 'accuracy_rating',
            'delivery_speed_rating', 'overall_rating',
            'title', 'comment',
            'is_verified_purchase', 'status',
            'reply', 'can_edit',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['reviewer_name', 'is_verified_purchase', 'status', 'reply', 'can_edit']

    def get_reviewer_name(self, obj):
        return obj.reviewer.name if obj.reviewer else ''

    def get_can_edit(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        return (
            obj.reviewer_id == request.user.id and
            (timezone.now() - obj.created_at) < timedelta(hours=48)
        )

    def get_listing_title(self, obj):
        if obj.listing:
            return f"{obj.listing.year} {obj.listing.make} {obj.listing.model}"
        return None

    def get_order_number(self, obj):
        if obj.order_id:
            return obj.order.order_number
        return None


class ReviewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Review
        fields = ['reviewed_user', 'listing', 'showroom', 'workshop', 'review_type', 'rating', 'title', 'comment']

    def validate(self, data):
        request       = self.context['request']
        reviewer      = request.user
        reviewed_user = data.get('reviewed_user')
        listing       = data.get('listing')
        review_type   = data.get('review_type')

        # Cannot review yourself
        if reviewed_user and reviewed_user.id == reviewer.id:
            raise serializers.ValidationError("You cannot review yourself.")

        # For buyer_to_seller: must have a lead interaction
        if review_type == 'buyer_to_seller':
            from leads.models import Lead
            if not listing:
                raise serializers.ValidationError({"listing": "A listing is required for buyer-to-seller reviews."})
            has_lead = Lead.objects.filter(
                listing=listing,
                buyer=reviewer,
            ).exists()
            if not has_lead:
                raise serializers.ValidationError(
                    "You must have submitted a lead on this listing before reviewing."
                )

        # Duplicate check
        if listing and Review.objects.filter(reviewer=reviewer, listing=listing).exists():
            raise serializers.ValidationError("You have already reviewed this listing.")

        return data

    def create(self, validated_data):
        request  = self.context['request']
        reviewer = request.user
        listing  = validated_data.get('listing')

        # Auto-set is_verified_purchase
        is_verified = False
        if listing:
            from leads.models import Lead
            is_verified = Lead.objects.filter(listing=listing, buyer=reviewer).exists()

        # Auto-flag if banned words present
        comment = validated_data.get('comment', '')
        title   = validated_data.get('title', '')
        text    = (comment + ' ' + title).lower()
        status  = 'flagged' if any(w in text for w in BANNED_WORDS) else 'approved'

        return Review.objects.create(
            reviewer=reviewer,
            is_verified_purchase=is_verified,
            status=status,
            **validated_data,
        )


class ReviewUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Review
        fields = ['rating', 'title', 'comment']

    def validate(self, data):
        instance = self.instance
        if (timezone.now() - instance.created_at) >= timedelta(hours=48):
            raise serializers.ValidationError("Reviews can only be edited within 48 hours of submission.")
        return data


class AdminReviewSerializer(serializers.ModelSerializer):
    reviewer_name      = serializers.SerializerMethodField()
    reviewed_user_name = serializers.SerializerMethodField()
    reply              = ReviewReplySerializer(read_only=True)

    class Meta:
        model  = Review
        fields = [
            'id', 'reviewer_name', 'reviewed_user', 'reviewed_user_name',
            'listing', 'review_type', 'rating', 'title', 'comment',
            'is_verified_purchase', 'status', 'admin_notes',
            'reply', 'created_at', 'updated_at',
        ]

    def get_reviewer_name(self, obj):
        return obj.reviewer.name if obj.reviewer else ''

    def get_reviewed_user_name(self, obj):
        return obj.reviewed_user.name if obj.reviewed_user else ''


class AdminReviewModerateSerializer(serializers.Serializer):
    status      = serializers.ChoiceField(choices=Review.STATUS_CHOICES)
    admin_notes = serializers.CharField(required=False, allow_blank=True)


class ImporterOrderReviewSerializer(serializers.ModelSerializer):
    """
    Serializer for POST /api/orders/{order_id}/review/
    Enforces:
      - reviewer must be the order buyer
      - order must be in delivered/completed status
      - one review per order (DB + serializer level)
      - all 4 dimension ratings required
    """
    class Meta:
        model  = Review
        fields = [
            'id',
            'communication_rating',
            'accuracy_rating',
            'delivery_speed_rating',
            'overall_rating',
            'title',
            'comment',
            # read-only output fields
            'rating',
            'is_verified_purchase',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'rating', 'is_verified_purchase', 'status', 'created_at', 'updated_at']

    def _rating_field(self, name):
        return serializers.IntegerField(min_value=1, max_value=5)

    communication_rating  = serializers.IntegerField(min_value=1, max_value=5)
    accuracy_rating       = serializers.IntegerField(min_value=1, max_value=5)
    delivery_speed_rating = serializers.IntegerField(min_value=1, max_value=5)
    overall_rating        = serializers.IntegerField(min_value=1, max_value=5)

    def validate(self, data):
        request = self.context['request']
        order   = self.context['order']

        # 1. Reviewer must be the order buyer
        if order.buyer_id != request.user.id:
            raise serializers.ValidationError(
                "Only the order buyer can review this order."
            )

        # 2. Order must be delivered or completed
        if order.status not in REVIEWABLE_ORDER_STATUSES:
            raise serializers.ValidationError(
                f"Reviews can only be submitted for delivered or completed orders "
                f"(current status: '{order.status}')."
            )

        # 3. One review per order
        if Review.objects.filter(order=order).exists():
            raise serializers.ValidationError(
                "You have already submitted a review for this order."
            )

        return data

    def create(self, validated_data):
        request = self.context['request']
        order   = self.context['order']

        comment = validated_data.get('comment', '')
        title   = validated_data.get('title', '')
        text    = (comment + ' ' + title).lower()
        review_status = 'flagged' if any(w in text for w in BANNED_WORDS) else 'approved'

        # Compute composite rating from 4 dimensions
        dims = [
            validated_data['communication_rating'],
            validated_data['accuracy_rating'],
            validated_data['delivery_speed_rating'],
            validated_data['overall_rating'],
        ]
        composite = round(sum(dims) / len(dims))

        return Review.objects.create(
            reviewer=request.user,
            reviewed_user=order.importer,
            order=order,
            review_type='importer_order',
            rating=composite,
            is_verified_purchase=True,
            status=review_status,
            **validated_data,
        )

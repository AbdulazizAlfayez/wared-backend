import re as _re

from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import User, VerificationRequest


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """JWT serializer that accepts 'email' instead of 'username'."""
    username_field = User.USERNAME_FIELD  # 'email'


class UserRegistrationSerializer(serializers.ModelSerializer):
    """POST /api/auth/register: email, name, password; optional phone. Role defaults to user."""
    # Declare email explicitly to remove the auto-generated UniqueValidator.
    # Uniqueness among verified accounts is enforced in validate_email below.
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
    )
    password2 = serializers.CharField(write_only=True, required=True)
    phone     = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = User
        fields = ('email', 'name', 'password', 'password2', 'phone')

    def validate_email(self, value):
        normalized = value.strip().lower()
        if User.objects.filter(email__iexact=normalized, is_email_verified=True).exists():
            raise serializers.ValidationError(
                'A user with this email is already registered. Please sign in.'
            )
        return normalized

    def validate_phone(self, value):
        if not value:
            return value
        import re
        pattern = r'^(\+9665\d{8}|05\d{8})$'
        if not re.match(pattern, value):
            raise serializers.ValidationError(
                'Phone must be Saudi format: +9665XXXXXXXX or 05XXXXXXXX.'
            )
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Passwords do not match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password2')
        password = validated_data.pop('password')
        validated_data.setdefault('role', 'user')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

       
class UserSerializer(serializers.ModelSerializer):
    """Serializer for user details."""
    class Meta:
        model = User
        fields = (
            'id', 'username', 'email', 'name', 'phone', 'role', 'date_joined',
            'is_identity_verified', 'is_business_verified', 'verification_level',
        )
        read_only_fields = (
            'id', 'date_joined',
            'is_identity_verified', 'is_business_verified', 'verification_level',
        )


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for /api/me: full own-user profile."""
    avatar_url = serializers.SerializerMethodField()
    city_name  = serializers.SerializerMethodField()
    has_accepted_current_terms = serializers.SerializerMethodField()

    email_verified = serializers.BooleanField(source='is_email_verified', read_only=True)
    phone_verified = serializers.BooleanField(source='is_phone_verified', read_only=True)

    class Meta:
        model = User
        fields = (
            'id', 'name', 'email', 'phone', 'role',
            'avatar_url', 'bio', 'city_obj', 'city_name',
            'show_phone', 'show_email', 'date_joined',
            'is_email_verified', 'is_phone_verified',
            # The unprefixed aliases the verification-status endpoint already
            # answers with. Both names, one source, so a client written against
            # either reads the same truth.
            'email_verified', 'phone_verified',
            'is_identity_verified', 'is_business_verified', 'verification_level',
            'has_accepted_current_terms',
        )
        read_only_fields = fields

    def get_avatar_url(self, obj):
        return obj.avatar.url if obj.avatar else None

    def get_city_name(self, obj):
        return obj.city_obj.name_en if obj.city_obj else None

    def get_has_accepted_current_terms(self, obj):
        from legal_documents.models import LegalDocument, UserLegalAcceptance
        for doc_type in ('terms_of_service', 'privacy_policy'):
            current_doc = LegalDocument.objects.filter(document_type=doc_type, is_active=True).first()
            if not current_doc:
                continue
            if not UserLegalAcceptance.objects.filter(user=obj, document=current_doc).exists():
                return False
        return True


class AdminUserSerializer(serializers.ModelSerializer):
    """GET /api/users/ and PATCH /api/users/<pk>/ response — admin-only."""
    class Meta:
        model = User
        fields = (
            'id', 'email', 'name', 'role', 'is_active', 'date_joined',
            'is_email_verified', 'is_phone_verified',
        )
        read_only_fields = fields


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    """Body for PATCH /api/users/<pk>/ — admin only."""
    class Meta:
        model = User
        fields = ('name', 'email', 'role', 'is_active')

    def validate_role(self, value):
        if value not in ('admin', 'importer', 'user'):
            raise serializers.ValidationError('Invalid role.')
        return value


VALID_ROLES = ['admin', 'importer', 'user']


class UserRoleSerializer(serializers.ModelSerializer):
    """PATCH /api/users/{id}/role — admin only; body: { \"role\": \"admin\" | \"importer\" | \"user\" }."""
    class Meta:
        model = User
        fields = ('role',)

    def validate_role(self, value):
        if value not in VALID_ROLES:
            raise serializers.ValidationError(
                f'Invalid role. Must be one of: {", ".join(VALID_ROLES)}.'
            )
        return value


# ---------------------------------------------------------------------------
# Profile serializers (Phase 2.8)
# ---------------------------------------------------------------------------

import re  # noqa: E402


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """
    PATCH /api/users/me/profile/ — update own profile fields.
    All fields optional. Avatar upload handled in the view (not here).
    """
    avatar_url = serializers.SerializerMethodField(read_only=True)
    city_name  = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = User
        fields = (
            'id', 'name', 'phone', 'bio',
            'city_obj', 'city_name',
            'show_phone', 'show_email',
            'avatar_url',
        )
        read_only_fields = ('id', 'avatar_url', 'city_name')

    def get_avatar_url(self, obj):
        return obj.avatar.url if obj.avatar else None

    def get_city_name(self, obj):
        return obj.city_obj.name_en if obj.city_obj else None

    def validate_phone(self, value):
        if not value:
            return value
        # Saudi format: +9665XXXXXXXX (13 chars) or 05XXXXXXXX (10 chars)
        pattern = r'^(\+9665\d{8}|05\d{8})$'
        if not re.match(pattern, value):
            raise serializers.ValidationError(
                'Phone must be Saudi format: +9665XXXXXXXX or 05XXXXXXXX.'
            )
        return value

    def validate_bio(self, value):
        if len(value) > 500:
            raise serializers.ValidationError('Bio cannot exceed 500 characters.')
        return value


class BuyerPublicProfileSerializer(serializers.ModelSerializer):
    """
    GET /api/users/<pk>/public/ — who an importer is selling to.

    Trust without contact details: enough for an importer to see that the
    person holding their car is real and has bought before, and nothing that
    would let either side take the deal off the platform. Phone and email are
    absent by construction — not gated, not masked, simply never read — and
    `show_phone` / `show_email` deliberately do not apply here, because the
    contact rule on a deal is the balance payment (see
    `messaging.utils.contact_exchange_allowed`), not a profile preference.
    """
    full_name     = serializers.SerializerMethodField()
    first_name    = serializers.SerializerMethodField()
    avatar_url    = serializers.SerializerMethodField()
    city          = serializers.SerializerMethodField()
    member_since  = serializers.SerializerMethodField()
    reviews_received = serializers.SerializerMethodField()
    completed_orders_count = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = (
            'id', 'full_name', 'first_name', 'avatar_url', 'city',
            'member_since', 'reviews_received', 'completed_orders_count',
        )
        read_only_fields = fields

    def get_full_name(self, obj):
        return (obj.name or '').strip() or None

    def get_first_name(self, obj):
        """
        The same first name the reservation rows show, from one place.

        An importer sees "Faisal" on the row and must see "Faisal" on the
        profile it opens; `person_brief` is what wrote the row.
        """
        from orders.serializers import person_brief

        brief = person_brief(obj)
        return brief['first_name'] if brief else None

    def get_avatar_url(self, obj):
        try:
            return obj.avatar.url if obj.avatar else None
        except Exception:
            # A CloudinaryField holding a malformed string raises on .url.
            return None

    def get_city(self, obj):
        return obj.city_obj.name_en if obj.city_obj else None

    def get_member_since(self, obj):
        # ISO, matching the importer profile. `PublicProfileSerializer` formats
        # this as "March 2024"; clients read both, so the two must not be
        # confused for one another.
        return obj.date_joined.isoformat() if obj.date_joined else None

    def get_reviews_received(self, obj):
        """
        Reviews importers have left about this buyer: `null`, because the
        platform has no way to write one.

        `Review.review_type` lists 'seller_to_buyer', but nothing creates it —
        no endpoint, no serializer, no task — and the two review paths that do
        exist both write reviews *about importers*. Worse, `Review`'s
        uniqueness constraints key on listing and order, both of which such a
        row would leave null, so an importer could write the same buyer down
        without limit. Returning `null` says "this platform does not rate
        buyers" rather than implying an empty record; a real buyer reputation
        needs a gated creation path and a constraint first.
        """
        return None

    def get_completed_orders_count(self, obj):
        """
        Orders this buyer saw through, counted as the importer profile counts
        them — `status='completed'` only, per
        `ImporterProfileSerializer.get_completed_orders_count`. 'delivered'
        means the car arrived but nobody closed the order, and the same word
        must not mean two things on two profiles.
        """
        from orders.models import ImportOrder

        return ImportOrder.objects.filter(buyer=obj, status='completed').count()


class PublicProfileSerializer(serializers.ModelSerializer):
    """
    GET /api/users/<pk>/profile/ — public view of any user's profile.
    Phone and email are only included if the user has opted in.
    """
    avatar_url        = serializers.SerializerMethodField()
    city_name         = serializers.SerializerMethodField()
    member_since      = serializers.SerializerMethodField()
    stats             = serializers.SerializerMethodField()
    subscription_badge = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = (
            'id', 'name', 'avatar_url', 'bio',
            'city_name', 'member_since', 'role', 'stats',
            'subscription_badge',
        )

    def get_avatar_url(self, obj):
        return obj.avatar.url if obj.avatar else None

    def get_city_name(self, obj):
        return obj.city_obj.name_en if obj.city_obj else None

    def get_member_since(self, obj):
        return obj.date_joined.strftime('%B %Y')

    def get_stats(self, obj):
        from cars.models import Listing
        from favorites.models import Favorite
        # `total_listings` is what this seller has published — approved and
        # live. `active_listings` is what a buyer could open right now, which
        # excludes the ones a reservation or an order has taken off the
        # market. Neither counts drafts, pending or rejected cars: an
        # unfiltered `Listing.objects` told a stranger how much of this
        # seller's stock was sitting in the moderation queue.
        published = Listing.objects.moderated().filter(owner=obj)
        return {
            'total_listings':     published.count(),
            'active_listings':    Listing.objects.public().filter(owner=obj).count(),
            'sold_count':         Listing.objects.filter(
                                      owner=obj, is_active=True, status='sold',
                                  ).count(),
            'favorites_received': Favorite.objects.filter(listing__owner=obj).count(),
        }

    def get_subscription_badge(self, obj):
        """Return plan badge_type ('', 'basic', 'pro') for dealer profiles."""
        if obj.role not in ('importer', 'admin'):
            return ''
        try:
            from subscriptions.models import DealerSubscription
            sub = DealerSubscription.objects.select_related('plan').get(
                dealer=obj, status='active'
            )
            return sub.plan.badge_type or ''
        except Exception:
            return ''

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.show_phone and instance.phone:
            data['phone'] = instance.phone
        if instance.show_email:
            data['email'] = instance.email
        return data


# ---------------------------------------------------------------------------
# Password reset serializers (Phase 2.11)
# ---------------------------------------------------------------------------

class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid             = serializers.CharField()
    token           = serializers.CharField()
    new_password    = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError(
                {'confirm_password': 'Passwords do not match.'}
            )
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password     = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError(
                {'confirm_password': 'Passwords do not match.'}
            )
        return attrs


# ---------------------------------------------------------------------------
# OTP serializers (Phase 3.4)
# ---------------------------------------------------------------------------

_SAUDI_PHONE_RE = re.compile(r'^(\+9665\d{8}|05\d{8})$')


def _validate_saudi_phone(value: str) -> str:
    if not _SAUDI_PHONE_RE.match(value):
        raise serializers.ValidationError(
            'Phone must be Saudi format: +9665XXXXXXXX or 05XXXXXXXX.'
        )
    return value


class SendOTPSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)

    def validate_phone(self, value):
        return _validate_saudi_phone(value)


class VerifyOTPSerializer(serializers.Serializer):
    code = serializers.CharField(min_length=6, max_length=6)

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError('Code must be exactly 6 digits.')
        return value


class LoginOTPSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    code  = serializers.CharField(min_length=6, max_length=6)

    def validate_phone(self, value):
        return _validate_saudi_phone(value)

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError('Code must be exactly 6 digits.')
        return value


class ResendOTPSerializer(serializers.Serializer):
    TYPE_CHOICES = ('email', 'phone')
    type = serializers.ChoiceField(choices=TYPE_CHOICES)


class VerifyEmailOTPSerializer(serializers.Serializer):
    code = serializers.CharField(min_length=6, max_length=6)

    def validate_code(self, value):
        if not value.isdigit():
            raise serializers.ValidationError('Code must be exactly 6 digits.')
        return value


# ---------------------------------------------------------------------------
# Verification serializers (Phase 5.1)
# ---------------------------------------------------------------------------

class SubmitVerificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationRequest
        fields = ['verification_type', 'document_number', 'document_file']

    def validate(self, data):
        vtype = data.get('verification_type')
        doc_num = data.get('document_number', '')
        if vtype == 'national_id':
            if not _re.match(r'^[12]\d{9}$', doc_num):
                raise serializers.ValidationError(
                    {'document_number': 'Saudi National ID must be 10 digits starting with 1 or 2.'}
                )
        elif vtype == 'commercial_registration':
            if not doc_num.isdigit():
                raise serializers.ValidationError(
                    {'document_number': 'CR number must be numeric.'}
                )
        return data


class VerificationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = VerificationRequest
        fields = [
            'id', 'verification_type', 'document_number', 'status',
            'rejection_reason', 'admin_note', 'created_at', 'reviewed_at',
        ]
        read_only_fields = fields


class AdminVerificationSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.CharField(source='user.name', read_only=True)
    reviewed_by_email = serializers.EmailField(source='reviewed_by.email', read_only=True, allow_null=True)
    document_file_url = serializers.SerializerMethodField()

    class Meta:
        model = VerificationRequest
        fields = [
            'id', 'user', 'user_email', 'user_name', 'verification_type',
            'document_number', 'document_file', 'document_file_url', 'status',
            'rejection_reason', 'admin_note', 'reviewed_by', 'reviewed_by_email',
            'reviewed_at', 'created_at', 'notes',
        ]

    def get_document_file_url(self, obj):
        if obj.document_file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.document_file.url)
        return None


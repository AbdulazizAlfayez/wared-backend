import datetime

from rest_framework import serializers

from .models import (
    Car, CarImage, Listing, ListingImage, Showroom, Workshop,
    ShowroomWorkingHours, ShowroomBranch, ShowroomReview,
    WorkshopWorkingHours, WorkshopService, WorkshopReview,
    SavedSearch, RecentlyViewed, ViewLog, BulkUpload,
    PromotionPackage, ListingPromotion,
)

# Lazy import — bookings does NOT import cars at module level, so this is safe.
from bookings.models import ServiceBooking, _SERVICE_BOOKING_VALID_TRANSITIONS


# ---------------------------------------------------------------------------
# Language helper mixin (Phase 2.12)
# ---------------------------------------------------------------------------

class BilingualMixin:
    """
    Mixin for serializers that need to return different field values depending
    on the Accept-Language header sent with the request.

    Usage: inherit alongside serializers.ModelSerializer and call
    self._lang() to get 'ar' or 'en'.
    """

    def _lang(self) -> str:
        request = self.context.get('request')
        if request:
            accept = request.headers.get('Accept-Language', 'en')
            code = accept[:2].lower()
            return code if code in ('en', 'ar') else 'en'
        return 'en'

    def _pick(self, en_val, ar_val) -> str:
        """Return ar_val when language is 'ar' and it is non-empty, else en_val."""
        if self._lang() == 'ar' and ar_val:
            return ar_val
        return en_val


# ---------------------------------------------------------------------------
# Social counts mixin (likes & comments)
# ---------------------------------------------------------------------------

class SocialCountsMixin:
    """
    Shared by ListingSerializer and ListingListSerializer so the detail and
    list shapes cannot drift apart.

    Each count prefers the annotation added by ListingViewSet.get_queryset()
    and falls back to a per-row query. The fallback matters: several endpoints
    (e.g. /api/listings/my/, favorites, the importer dashboard) build their own
    queryset and would otherwise raise. Those paths pay one query per row.
    """

    def get_like_count(self, obj):
        annotated = getattr(obj, 'like_total', None)
        return annotated if annotated is not None else obj.likes.count()

    def get_comment_count(self, obj):
        annotated = getattr(obj, 'comment_total', None)
        if annotated is not None:
            return annotated
        return obj.comments.filter(is_deleted=False, is_hidden=False).count()

    def get_is_liked(self, obj):
        """
        False — not None — for anonymous callers: this is a boolean the client
        binds to a filled/outline heart, so it must never be null. (The older
        `is_favorited_by_me` returns None instead; the two are deliberately
        different here.)
        """
        request = self.context.get('request')
        if not request or not request.user or not request.user.is_authenticated:
            return False
        # One query for the whole page, mirroring the favorites approach.
        if not hasattr(self, '_liked_ids'):
            from social.models import ListingLike
            self._liked_ids = set(
                ListingLike.objects.filter(user=request.user)
                .values_list('listing_id', flat=True)
            )
        return obj.id in self._liked_ids

    def get_reservation_state(self, obj):
        """
        null | "reserved_by_you" | "reserved" — see
        cars.visibility.reservation_state. Lets the app show "you reserved
        this" / "reserved" instead of hitting a payment error.
        """
        from .visibility import reservation_state
        request = self.context.get('request')
        return reservation_state(obj, getattr(request, 'user', None))


# ---------------------------------------------------------------------------
# Listing Images
# ---------------------------------------------------------------------------

class ListingImageSerializer(serializers.ModelSerializer):
    """
    Read: returns id, listing (id), image_url + size variants, is_primary, order, uploaded_at.
    Write: accepts an image file via multipart — handled explicitly in the view.
    """
    image_url = serializers.SerializerMethodField(read_only=True)
    variants  = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ListingImage
        fields = ('id', 'listing', 'image', 'image_url', 'variants', 'is_primary', 'order', 'uploaded_at')
        read_only_fields = ('id', 'listing', 'uploaded_at', 'image_url', 'variants')

    def get_image_url(self, obj):
        if obj.image:
            return obj.image.url
        return None

    def get_variants(self, obj):
        from .utils.cloudinary_urls import image_variants
        if obj.image:
            return image_variants(obj.image.url)
        return {'thumb': None, 'card': None, 'full': None}


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

class CostBreakdownSerializer(serializers.Serializer):
    """
    The stored cost breakdown, read-only.

    `calculated_total` used to live here: a property that summed `source_price`
    — a foreign-currency amount — straight into the SAR costs, reporting
    SAR 75,050,506 for a SAR 254,256 Korean car. It is gone.
    `total_landed_cost` is the real figure, computed in `cars/pricing.py` at
    save time. It is what the car cost to land, NOT what it sells for:
    `final_price_sar` is the importer's asking price and is set by them.
    """

    source_price        = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    source_currency     = serializers.CharField(read_only=True)
    shipping_cost       = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True, allow_null=True)
    customs_duty_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True, allow_null=True)
    vat_amount          = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True, allow_null=True)
    inspection_fee      = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True, allow_null=True)
    transportation_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True, allow_null=True)
    total_landed_cost   = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    final_price_sar     = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, allow_null=True)


class ListingSerializer(BilingualMixin, SocialCountsMixin, serializers.ModelSerializer):
    """Serializer for Listing (api/listings). Includes nested images and primary_image URL."""

    # The asking price. `final_price_sar` is the one the importer fills in on
    # both clients; `price` mirrors it (see `validate`), and either may be
    # sent. Neither is required on a draft — see `validate`.
    price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    final_price_sar = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    # Filled in `to_representation` for the owner and admins only.
    owner_feedback = serializers.SerializerMethodField()
    feedback_at = serializers.SerializerMethodField()
    missing_for_submit = serializers.SerializerMethodField()

    def get_owner_feedback(self, obj):
        return self._owner_feedback(obj)

    def get_feedback_at(self, obj):
        return self._feedback_at(obj)

    def get_missing_for_submit(self, obj):
        return self._missing_for_submit(obj)

    owner_id = serializers.IntegerField(source='owner.id', read_only=True)
    # Nested owner (importer) info. Name/role are public (shown on the car
    # page); email is PRIVATE — admins only (contact-exchange policy).
    owner = serializers.SerializerMethodField(read_only=True)
    images = ListingImageSerializer(many=True, read_only=True)
    primary_image = serializers.SerializerMethodField(read_only=True)

    # Bilingual display fields (Phase 2.12) — read-only, language-aware
    make_display        = serializers.SerializerMethodField(read_only=True)
    model_display       = serializers.SerializerMethodField(read_only=True)
    description_display = serializers.SerializerMethodField(read_only=True)
    color_display       = serializers.SerializerMethodField(read_only=True)
    city_display        = serializers.SerializerMethodField(read_only=True)
    region_display      = serializers.SerializerMethodField(read_only=True)

    # View counters (Phase 2.13)
    view_stats = serializers.SerializerMethodField(read_only=True)

    # Whether the requesting user owns this car. Every listing serializer
    # carries it so a client never has to compare owner_id itself.
    is_owner = serializers.SerializerMethodField(read_only=True)
    # The five counts an importer watches. Stripped for everyone else in
    # `to_representation` — what a car has attracted is the seller's business.
    owner_stats = serializers.SerializerMethodField(read_only=True)

    # Phase 4.6 — Promotion helpers
    is_promoted      = serializers.SerializerMethodField(read_only=True)
    active_promotion = serializers.SerializerMethodField(read_only=True)

    # Phase 5.1 — Owner verification
    owner_verified           = serializers.SerializerMethodField(read_only=True)
    owner_verification_level = serializers.SerializerMethodField(read_only=True)

    # Import cost breakdown (nested, read-only)
    cost_breakdown = CostBreakdownSerializer(source='*', read_only=True)

    # Favorites (Phase M8)
    is_favorited_by_me = serializers.SerializerMethodField(read_only=True)

    # Social (likes & comments) — see SocialCountsMixin
    like_count    = serializers.SerializerMethodField(read_only=True)
    comment_count = serializers.SerializerMethodField(read_only=True)
    is_liked      = serializers.SerializerMethodField(read_only=True)

    # Reservation lock as seen by the requesting user
    reservation_state = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Listing
        fields = (
            # Core
            'id', 'title', 'make', 'model', 'year', 'price', 'mileage',
            'city', 'description', 'vin', 'color', 'status',
            # Arabic fields (Phase 2.12)
            'make_ar', 'model_ar', 'description_ar',
            'color_ar', 'color_interior_ar', 'city_ar',
            # Bilingual display helpers
            'make_display', 'model_display', 'description_display',
            'color_display', 'city_display', 'region_display',
            # Specs
            'body_type', 'drive_type', 'fuel_type', 'transmission', 'condition',
            'engine_size', 'horsepower', 'cylinders', 'seats', 'doors', 'color_interior',
            # Saudi-specific
            'imported_from', 'customs_cleared', 'accident_history', 'accident_description',
            'warranty_remaining', 'service_history', 'negotiable',
            # Location
            'city_obj', 'latitude', 'longitude',
            # Relations & audit
            'owner_id', 'owner', 'showroom', 'workshop',
            'is_active', 'approved_by', 'approved_at', 'created_at',
            # Status tracking (Phase 2.14)
            'rejection_reason', 'admin_notes', 'owner_feedback', 'feedback_at',
            'missing_for_submit', 'submitted_at', 'status_changed_at',
            # View counters (Phase 2.13)
            'view_count', 'unique_view_count', 'view_stats',
            'is_owner', 'owner_stats',
            # Images
            'images', 'primary_image',
            # Phase 4.6 — Promotion
            'is_featured', 'is_highlighted', 'is_top_search', 'is_homepage',
            'promotion_priority', 'is_promoted', 'active_promotion',
            # Phase 5.1 — Owner verification
            'owner_verified', 'owner_verification_level',
            # Import — Source Information
            'source_country', 'source_city', 'auction_source', 'auction_lot_number',
            'original_listing_url', 'source_price', 'source_currency',
            # Import — Cost Breakdown
            'shipping_cost', 'customs_duty_amount', 'vat_amount', 'inspection_fee',
            'transportation_cost',
            'total_landed_cost', 'final_price_sar',
            'cost_breakdown',
            # Import — Status & Reservation
            'import_status', 'is_reserved', 'reservation_state',
            # Import — Shipping Information
            'vessel_name', 'shipping_line', 'bill_of_lading_number', 'container_number',
            'port_of_origin', 'port_of_entry', 'estimated_arrival_date', 'actual_arrival_date',
            # Import — Customs & Compliance
            'customs_declaration_number', 'customs_clearance_date',
            'conformity_certificate_number', 'vehicle_inspection_result',
            'vehicle_inspection_date', 'gcc_specs', 'spec_origin',
            'odometer_verified', 'emissions_compliant',
            # Import — History & Condition
            'carfax_url', 'autocheck_url', 'has_salvage_title', 'has_flood_damage',
            'has_frame_damage', 'damage_description',
            # Favorites
            'is_favorited_by_me',
            # Social (likes & comments)
            'like_count', 'comment_count', 'is_liked',
        )
        read_only_fields = (
            'id', 'owner_id', 'owner', 'is_active', 'approved_by', 'approved_at',
            'status',  # approval status — admin-only, enforced server-side
            'created_at', 'images', 'primary_image',
            'make_display', 'model_display', 'description_display',
            'color_display', 'city_display', 'region_display',
            'view_count', 'unique_view_count', 'view_stats',
            'is_owner', 'owner_stats',
            'rejection_reason', 'admin_notes', 'owner_feedback', 'feedback_at',
            'missing_for_submit', 'submitted_at', 'status_changed_at',
            'is_featured', 'is_highlighted', 'is_top_search', 'is_homepage',
            'promotion_priority', 'is_promoted', 'active_promotion',
            'owner_verified', 'owner_verification_level',
            'cost_breakdown', 'is_favorited_by_me',
            # The importer sets `final_price_sar`; `price` is kept in step with
            # it in validate(). `total_landed_cost` is informational and
            # computed in save() — see cars/pricing.py.
            'total_landed_cost',
            'like_count', 'comment_count', 'is_liked',
            'reservation_state',
        )

    def get_owner(self, obj):
        owner = getattr(obj, 'owner', None)
        if owner is None:
            return None
        data = {
            'id': owner.id,
            'name': owner.name,
            'role': getattr(owner, 'role', 'user'),
            # Which id opens this seller's public page. `/api/importers/{pk}/`
            # is keyed by the PROFILE, not the user, and the two differ — a
            # client linking with `owner.id` lands on somebody else's importer
            # or on a 404. None for an owner with no importer profile.
            'profile_url_id': getattr(
                getattr(owner, 'importer_profile', None), 'pk', None,
            ),
        }
        request = self.context.get('request')
        user = getattr(request, 'user', None) if request else None
        if user and user.is_authenticated and (
            getattr(user, 'role', None) == 'admin'
            or user.is_staff
            or user.pk == owner.id
        ):
            data['email'] = owner.email
            data['phone'] = getattr(owner, 'phone', '') or None
        return data

    def get_is_favorited_by_me(self, obj):
        request = self.context.get('request')
        if not request or not request.user or not request.user.is_authenticated:
            return None
        if not hasattr(self, '_favorite_ids'):
            from favorites.models import Favorite
            self._favorite_ids = set(
                Favorite.objects.filter(user=request.user)
                .values_list('listing_id', flat=True)
            )
        return obj.id in self._favorite_ids

    # ── Input validation (create/update) ──────────────────────────────────

    def validate_price(self, value):
        if value is not None and (value < 5000 or value > 5000000):
            raise serializers.ValidationError('Price must be between 5,000 and 5,000,000 SAR.')
        return value

    def validate_mileage(self, value):
        if value is not None and (value < 0 or value > 500000):
            raise serializers.ValidationError('Mileage must be between 0 and 500,000 km.')
        return value

    def validate_year(self, value):
        import datetime
        max_year = datetime.date.today().year + 1
        if value is not None and (value < 1990 or value > max_year):
            raise serializers.ValidationError(f'Year must be between 1990 and {max_year}.')
        return value

    def validate_import_status(self, value):
        """Restrict import_status changes based on order state (non-admin)."""
        request = self.context.get('request')
        if not request or not request.user or request.user.is_staff:
            return value  # admin exempt

        instance = self.instance
        if not instance:
            return value  # new listing — any status ok

        # Check if this listing has an active (non-terminal) order
        from orders.models import ImportOrder
        has_active_order = ImportOrder.objects.filter(
            car=instance,
            status__in=[
                'pending', 'deposit_requested', 'deposit_paid', 'confirmed',
                'sourcing', 'purchased', 'preparing_shipment', 'shipped',
                'arrived_port', 'in_customs', 'customs_cleared', 'inspection',
                'ready', 'delivered',
            ],
        ).exists()

        if has_active_order:
            raise serializers.ValidationError(
                'Cannot change car status while an active order exists. '
                'Update the order status instead — it will sync the car automatically. '
                '/ لا يمكن تغيير حالة السيارة أثناء وجود طلب نشط. حدّث حالة الطلب بدلاً من ذلك.'
            )

        # No active order: only sourcing and available are allowed
        allowed = {'sourcing', 'available'}
        if value not in allowed:
            raise serializers.ValidationError(
                f'Before a buyer purchases, status can only be "sourcing" or "available". '
                f'/ قبل الشراء، الحالة المسموحة فقط "بحث" أو "متاح".'
            )
        return value

    def get_primary_image(self, obj):
        from .utils.cloudinary_urls import primary_image_payload

        # One shape for every endpoint — see `primary_image_payload`. This used
        # to be a bare full-size URL, which is why the same key had two types
        # depending on whether you asked for a list or a detail.
        return primary_image_payload(obj)

    def get_make_display(self, obj):
        return self._pick(obj.make, obj.make_ar)

    def get_model_display(self, obj):
        return self._pick(obj.model, obj.model_ar)

    def get_description_display(self, obj):
        return self._pick(obj.description or '', obj.description_ar)

    def get_color_display(self, obj):
        return self._pick(obj.color, obj.color_ar)

    def get_city_display(self, obj):
        if obj.city_obj:
            return self._pick(obj.city_obj.name_en, obj.city_obj.name_ar)
        return self._pick(obj.city, obj.city_ar)

    def get_region_display(self, obj):
        if obj.city_obj and obj.city_obj.region_id:
            region = obj.city_obj.region
            return self._pick(region.name_en, region.name_ar)
        return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        user = request.user if request else None

        is_owner = user and user.is_authenticated and user.pk == instance.owner_id
        is_admin = user and user.is_authenticated and getattr(user, 'role', None) == 'admin'

        # admin_notes: admins only
        if not is_admin:
            data.pop('admin_notes', None)

        # rejection_reason: owner or admin only
        if not (is_owner or is_admin):
            data.pop('rejection_reason', None)

        # owner_feedback: the reviewer's words, for the person who has to act
        # on them. `request-changes` writes to `admin_notes`, which is stripped
        # above — so without this an owner could see that changes were wanted
        # but never what to change.
        if is_owner or is_admin:
            data['owner_feedback'] = self._owner_feedback(instance)
            data['feedback_at'] = self._feedback_at(instance)
            data['missing_for_submit'] = self._missing_for_submit(instance)
        else:
            # Owner-facing only: how many people saved, liked, messaged about
            # or reserved this car is the seller's business, not a buyer's.
            data.pop('owner_stats', None)
            # Gated exactly as `owner_feedback` is. A timestamp is a small
            # leak but it is still one: it tells an anonymous caller that a
            # reviewer acted, and when.
            data.pop('owner_feedback', None)
            data.pop('feedback_at', None)
            data.pop('missing_for_submit', None)

        return data

    @classmethod
    def compute_missing_for_submit(cls, listing):
        """
        What still stands between this listing and the review queue.

        Field names, in the order the form asks for them, so a client can say
        exactly what is left rather than "something is missing". Empty means
        `submit` will succeed.
        """
        missing = []
        for field in cls.REQUIRED_FOR_SUBMIT:
            value = getattr(listing, field, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(field)
        if listing.final_price_sar is None and listing.price is None:
            missing.append('final_price_sar')
        return missing

    def _missing_for_submit(self, instance):
        """Owner-facing only; a buyer has no business knowing."""
        return self.compute_missing_for_submit(instance)

    def _feedback_at(self, instance):
        """
        When the reviewer wrote the note, or None when there is no note.

        `status_changed_at` is the moment: `request-changes` and `reject` both
        stamp it in the same save that writes the note. A separate column
        would be a second source of truth for one fact.
        """
        if self._owner_feedback(instance) is None:
            return None
        stamped = getattr(instance, 'status_changed_at', None)
        return stamped.isoformat() if stamped else None

    def _owner_feedback(self, instance):
        """What the reviewer asked for, or None when nothing is outstanding."""
        if instance.status == 'changes_requested':
            return (instance.admin_notes or '').strip() or None
        if instance.status == 'rejected':
            return (instance.rejection_reason or '').strip() or None
        return None

    def get_is_owner(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        return user.pk == obj.owner_id

    def get_owner_stats(self, obj):
        from .stats import owner_stats

        # Prefetched by the desk, which counts six listings in one go.
        cached = getattr(self, '_owner_stats_bulk', None)
        if cached is not None and obj.pk in cached:
            return cached[obj.pk]
        return owner_stats(obj)

    def get_view_stats(self, obj):
        """
        Returns daily/weekly/monthly view breakdowns.
        Only visible to the listing owner or an admin — returns None for everyone else.
        """
        from django.utils import timezone
        from datetime import timedelta

        request = self.context.get('request')
        if not request:
            return None
        user = request.user
        if not user or not user.is_authenticated:
            return None
        if user.pk != obj.owner_id and getattr(user, 'role', None) != 'admin':
            return None

        now        = timezone.now()
        today      = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago   = now - timedelta(days=7)
        month_ago  = now - timedelta(days=30)

        qs = ViewLog.objects.filter(listing=obj)
        return {
            'views_today':      qs.filter(viewed_at__gte=today).count(),
            'views_this_week':  qs.filter(viewed_at__gte=week_ago).count(),
            'views_this_month': qs.filter(viewed_at__gte=month_ago).count(),
        }

    def get_owner_verified(self, obj) -> bool:
        owner = getattr(obj, 'owner', None)
        if owner is None:
            return False
        return getattr(owner, 'is_identity_verified', False)

    def get_owner_verification_level(self, obj) -> str:
        owner = getattr(obj, 'owner', None)
        if owner is None:
            return 'none'
        return getattr(owner, 'verification_level', 'none')

    def get_is_promoted(self, obj) -> bool:
        return bool(obj.is_featured or obj.is_highlighted or obj.is_top_search or obj.is_homepage)

    def get_active_promotion(self, obj):
        """
        Returns the most recent active promotion for the listing owner or admin.
        Uses prefetched 'active_promotions_prefetched' if the viewset populated it.
        """
        request = self.context.get('request')
        if not request:
            return None
        user = request.user
        if not user or not user.is_authenticated:
            return None
        is_owner = user.pk == obj.owner_id
        is_admin = getattr(user, 'role', None) == 'admin'
        if not (is_owner or is_admin):
            return None

        # Use viewset-prefetched data when available (avoids N+1 on list)
        promos = getattr(obj, 'active_promotions_prefetched', None)
        if promos is None:
            from django.utils import timezone as tz
            promos = list(
                obj.promotions.filter(status='active', expires_at__gt=tz.now())
                .select_related('package')
                .order_by('-started_at')
            )

        if not promos:
            return None
        promo = promos[0]
        return {
            'id':             promo.pk,
            'package_name':   promo.package.name,
            'promotion_type': promo.package.promotion_type,
            'expires_at':     promo.expires_at.isoformat(),
            'days_remaining': promo.days_remaining,
        }

    PRICE_MIN = 5000
    PRICE_MAX = 5000000

    #: Everything a listing needs before it may enter the review queue.
    #: `price`/`final_price_sar` is handled separately — either satisfies it.
    REQUIRED_FOR_SUBMIT = ('title', 'make', 'model', 'year', 'mileage', 'city')

    #: What a draft may leave empty. A half-filled form is the entire point of
    #: a draft, so the only things asked for are the three that identify the
    #: car — and `title` is composed from them when it is missing.
    DRAFT_OPTIONAL = ('title', 'city', 'mileage', 'price', 'final_price_sar', 'description', 'vin')

    def __init__(self, *args, **kwargs):
        """
        Relax the required fields when the payload parks a draft.

        Field-level `required`/`allow_blank` runs before `validate()`, so a
        draft has to be recognised here or the blank `title` and `city` are
        rejected before any of our own rules are reached. This is what made
        "This field may not be blank." the answer to saving a half-filled form.
        """
        super().__init__(*args, **kwargs)
        if not self._is_draft(getattr(self, 'initial_data', {}) or {}):
            return
        for name in self.DRAFT_OPTIONAL:
            field = self.fields.get(name)
            if field is None:
                continue
            field.required = False
            field.allow_null = True
            if hasattr(field, 'allow_blank'):
                field.allow_blank = True

    def _is_draft(self, data):
        """
        Whether this payload leaves the listing parked as a draft.

        A draft is a half-filled form the importer saved to come back to, so
        the price is not demanded yet. Submitting it is what requires one —
        enforced here on create/update and again in `ListingViewSet.submit`.
        """
        requested = (self.initial_data.get('status') or '').strip().lower() \
            if hasattr(self, 'initial_data') else ''
        if requested:
            return requested == 'draft'
        # `instance` is a QuerySet under `many=True`; there is no single
        # status to read and a list serializer never validates a payload.
        return getattr(self.instance, 'status', None) == 'draft'

    def _sent_price(self, data):
        """
        The price in this payload, if it carries one.

        `final_price_sar` is the field both clients fill in; `price` is the
        older name and still accepted. Whichever arrives wins.
        """
        for field in ('final_price_sar', 'price'):
            if data.get(field) is not None:
                return data[field]
        return None

    def _stored_price(self):
        if not self.instance:
            return None
        return self.instance.final_price_sar or self.instance.price

    @staticmethod
    def compose_title(data, instance=None):
        """"year make model" — what both clients would have typed anyway."""
        def pick(field):
            value = data.get(field)
            if value in (None, ''):
                value = getattr(instance, field, None) if instance else None
            return str(value).strip() if value not in (None, '') else ''

        return ' '.join(part for part in (pick('year'), pick('make'), pick('model')) if part)

    def validate(self, data):
        errors = {}
        current_year = datetime.date.today().year

        # `mileage` and `city` are nullable on the model so a draft can be
        # saved half-filled — which means DRF no longer demands them of
        # anybody. Anything that is not a draft still has to carry them.
        if not self._is_draft(data):
            for field in ('mileage', 'city'):
                value = data.get(field, getattr(self.instance, field, None))
                if value is None or (isinstance(value, str) and not value.strip()):
                    errors[field] = 'This field is required.'

        # A draft saved from a half-filled form has no title yet. The rule is
        # "title OR make+model", so compose one rather than demand it — the
        # column is NOT NULL and an empty title makes an unreadable queue row.
        if not (data.get('title') or '').strip():
            composed = self.compose_title(data, self.instance)
            if composed:
                data['title'] = composed
            elif not self._is_draft(data):
                errors['title'] = 'This field is required.'

        # Price: the importer's number, within the marketplace bounds. The
        # cost lines are informational and never move it (see cars/pricing.py).
        sent = self._sent_price(data)
        if sent is None:
            # Nothing sent: required on the way in unless this stays a draft,
            # and untouched on an edit that is about something else. Mirroring
            # here instead would rewrite final_price_sar on a description edit
            # and send an approved listing back for review over nothing.
            if self._stored_price() is None and not self._is_draft(data):
                errors['final_price_sar'] = 'This field is required.'
        elif sent < self.PRICE_MIN or sent > self.PRICE_MAX:
            message = (
                f'Price must be between SAR {self.PRICE_MIN:,} and '
                f'SAR {self.PRICE_MAX:,}.'
            )
            for field in ('final_price_sar', 'price'):
                if data.get(field) is not None:
                    errors[field] = message
            errors.setdefault('final_price_sar', message)
        else:
            # Written together, so the marketplace card and the import screens
            # can never advertise different numbers.
            data['final_price_sar'] = sent
            data['price'] = sent

        # Year: 1980 to current_year + 1
        year = data.get('year')
        if year is not None:
            if year < 1980 or year > current_year + 1:
                errors['year'] = f'Year must be between 1980 and {current_year + 1}.'

        # Mileage: 0 to 1,000,000
        mileage = data.get('mileage')
        if mileage is not None:
            if mileage < 0:
                errors['mileage'] = 'Mileage cannot be negative.'
            elif mileage > 1000000:
                errors['mileage'] = 'Mileage cannot exceed 1,000,000 km.'

        # The source price is what the car cost abroad, in its own currency —
        # informational, but a negative or absurd figure is still a typo.
        source_price = data.get('source_price')
        if source_price is not None:
            if source_price <= 0:
                errors['source_price'] = 'Price must be greater than zero.'
            elif source_price > self.PRICE_MAX:
                errors['source_price'] = f'Price cannot exceed SAR {self.PRICE_MAX:,}.'

        # Required fields for submission (only on create, not partial update)
        if not self.instance:
            if not data.get('make'):
                errors.setdefault('make', 'Make is required.')
            if not data.get('model'):
                errors.setdefault('model', 'Model is required.')

        # VIN duplicate check (same importer, same VIN, active listing)
        vin = data.get('vin')
        if vin and vin.strip():
            from .models import Listing
            request = self.context.get('request')
            owner = request.user if request else None
            dup_qs = Listing.objects.filter(
                owner=owner, vin=vin.strip(), is_active=True
            ).exclude(status__in=('sold', 'rejected'))
            # Exclude current instance on update
            if self.instance:
                dup_qs = dup_qs.exclude(pk=self.instance.pk)
            if dup_qs.exists():
                errors['vin'] = 'You already have an active listing with this VIN.'

        if errors:
            raise serializers.ValidationError(errors)
        return data


# ---------------------------------------------------------------------------
# Listing List (lean — no full image array, just primary_image variants)
# ---------------------------------------------------------------------------

class ListingListSerializer(BilingualMixin, SocialCountsMixin, serializers.ModelSerializer):
    """
    Lean serializer for listing list endpoints.  Returns the primary image as
    {thumb, card} variants instead of the full image array — biggest payload
    savings for mobile scrolling lists.
    """
    owner_id     = serializers.IntegerField(source='owner.id', read_only=True)
    primary_image = serializers.SerializerMethodField(read_only=True)

    # Bilingual display
    make_display  = serializers.SerializerMethodField(read_only=True)
    model_display = serializers.SerializerMethodField(read_only=True)
    city_display  = serializers.SerializerMethodField(read_only=True)

    # Promotion flags
    is_promoted = serializers.SerializerMethodField(read_only=True)

    # Verification
    owner_verification_level = serializers.SerializerMethodField(read_only=True)

    # Social (likes & comments) — see SocialCountsMixin
    like_count    = serializers.SerializerMethodField(read_only=True)
    comment_count = serializers.SerializerMethodField(read_only=True)
    is_liked      = serializers.SerializerMethodField(read_only=True)

    # Reservation lock as seen by the requesting user
    reservation_state = serializers.SerializerMethodField(read_only=True)

    # Same field, same meaning as on the detail serializer.
    is_owner = serializers.SerializerMethodField(read_only=True)

    def get_is_owner(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        return user.pk == obj.owner_id

    class Meta:
        model = Listing
        fields = (
            'id', 'title', 'make', 'model', 'year', 'price', 'mileage',
            'city', 'condition', 'fuel_type', 'transmission', 'body_type',
            'imported_from', 'status', 'import_status', 'is_reserved',
            'reservation_state', 'final_price_sar',
            'owner_id', 'primary_image',
            'make_display', 'model_display', 'city_display',
            'is_featured', 'is_highlighted', 'is_homepage',
            'is_promoted', 'owner_verification_level',
            'is_owner', 'negotiable', 'created_at',
            # Social (likes & comments)
            'like_count', 'comment_count', 'is_liked',
        )
        read_only_fields = fields

    def get_primary_image(self, obj):
        from .utils.cloudinary_urls import primary_image_payload

        return primary_image_payload(obj)

    def get_make_display(self, obj):
        return self._pick(obj.make, obj.make_ar)

    def get_model_display(self, obj):
        return self._pick(obj.model, obj.model_ar)

    def get_city_display(self, obj):
        try:
            if obj.city_obj:
                return self._pick(obj.city_obj.name_en, obj.city_obj.name_ar)
        except Exception:
            pass
        return self._pick(obj.city, obj.city_ar)

    def get_is_promoted(self, obj):
        prefetched = getattr(obj, 'active_promotions_prefetched', None)
        if prefetched is not None:
            return len(prefetched) > 0
        return obj.promotions.filter(status='active').exists()

    def get_owner_verification_level(self, obj):
        try:
            return obj.owner.verification_level
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Car Images
# ---------------------------------------------------------------------------

class CarImageSerializer(serializers.ModelSerializer):
    """Serializer for CarImage. image_url returns the full Cloudinary URL."""

    image_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CarImage
        fields = ('id', 'image', 'image_url', 'created_at')
        read_only_fields = ('id', 'image_url', 'created_at')

    def get_image_url(self, obj):
        if obj.image:
            return obj.image.url
        return None


# ---------------------------------------------------------------------------
# Car
# ---------------------------------------------------------------------------

class CarSerializer(serializers.ModelSerializer):
    """Serializer for Car model."""

    images = CarImageSerializer(many=True, read_only=True)
    seller = serializers.StringRelatedField(read_only=True)
    seller_id = serializers.IntegerField(read_only=True)
    seller_email = serializers.EmailField(source='seller.email', read_only=True)
    seller_name = serializers.CharField(source='seller.name', read_only=True)
    seller_phone = serializers.CharField(source='seller.phone', read_only=True)

    class Meta:
        model = Car
        fields = (
            'id', 'title', 'description', 'make', 'model', 'year', 'price',
            'mileage', 'color', 'fuel_type', 'transmission', 'condition',
            'location', 'status', 'seller', 'seller_id', 'seller_email',
            'seller_name', 'seller_phone', 'images', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'created_at', 'updated_at')

    def create(self, validated_data):
        validated_data['seller'] = self.context['request'].user
        return super().create(validated_data)


class CarListSerializer(serializers.ModelSerializer):
    """Simplified serializer for car list (with first image only)."""

    thumbnail = serializers.SerializerMethodField()
    seller_name = serializers.CharField(source='seller.name', read_only=True)
    seller_email = serializers.EmailField(source='seller.email', read_only=True)

    class Meta:
        model = Car
        fields = (
            'id', 'title', 'make', 'model', 'year', 'price', 'mileage',
            'fuel_type', 'transmission', 'condition', 'location', 'status',
            'seller_name', 'seller_email', 'thumbnail', 'created_at',
        )
        read_only_fields = ('id', 'created_at')

    # `Car` is the legacy model with its own `CarImage`; it has no Listing to
    # hand `primary_image_payload`, so this stays a bare URL. Nothing mobile
    # reads it — /api/cars/ predates the listing API.
    def get_thumbnail(self, obj):
        first_image = obj.images.first()
        if first_image and first_image.image:
            return first_image.image.url
        return None


# ---------------------------------------------------------------------------
# Showroom & Workshop
# ---------------------------------------------------------------------------

class ShowroomSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField(read_only=True)
    cover_photo_url = serializers.SerializerMethodField(read_only=True)
    owner_verified = serializers.SerializerMethodField(read_only=True)
    owner_verification_level = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Showroom
        fields = (
            'id', 'name', 'city', 'address', 'phone', 'verified', 'owner',
            'city_obj', 'latitude', 'longitude',
            'logo', 'cover_photo', 'logo_url', 'cover_photo_url', 'created_at',
            'owner_verified', 'owner_verification_level',
        )
        read_only_fields = ('id', 'created_at', 'logo_url', 'cover_photo_url', 'owner_verified', 'owner_verification_level')

    def get_logo_url(self, obj):
        return obj.logo.url if obj.logo else None

    def get_cover_photo_url(self, obj):
        return obj.cover_photo.url if obj.cover_photo else None

    def get_owner_verified(self, obj) -> bool:
        owner = getattr(obj, 'owner', None)
        if owner is None:
            return False
        return getattr(owner, 'is_identity_verified', False)

    def get_owner_verification_level(self, obj) -> str:
        owner = getattr(obj, 'owner', None)
        if owner is None:
            return 'none'
        return getattr(owner, 'verification_level', 'none')


class WorkshopSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField(read_only=True)
    cover_photo_url = serializers.SerializerMethodField(read_only=True)
    owner_verified = serializers.SerializerMethodField(read_only=True)
    owner_verification_level = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Workshop
        fields = (
            'id', 'name', 'city', 'services', 'phone', 'rating',
            'city_obj', 'latitude', 'longitude', 'owner',
            'logo', 'cover_photo', 'logo_url', 'cover_photo_url', 'created_at',
            'owner_verified', 'owner_verification_level',
        )
        read_only_fields = ('id', 'created_at', 'logo_url', 'cover_photo_url', 'owner_verified', 'owner_verification_level')

    def get_logo_url(self, obj):
        return obj.logo.url if obj.logo else None

    def get_cover_photo_url(self, obj):
        return obj.cover_photo.url if obj.cover_photo else None

    def get_owner_verified(self, obj) -> bool:
        owner = getattr(obj, 'owner', None)
        if owner is None:
            return False
        return getattr(owner, 'is_identity_verified', False)

    def get_owner_verification_level(self, obj) -> str:
        owner = getattr(obj, 'owner', None)
        if owner is None:
            return 'none'
        return getattr(owner, 'verification_level', 'none')


# ---------------------------------------------------------------------------
# Phase 4.1 — Showroom Management serializers
# ---------------------------------------------------------------------------

class ShowroomWorkingHoursSerializer(serializers.ModelSerializer):
    day_display = serializers.CharField(source='get_day_display', read_only=True)

    class Meta:
        model  = ShowroomWorkingHours
        fields = ('id', 'day', 'day_display', 'opening_time', 'closing_time', 'is_closed')
        read_only_fields = ('id', 'day_display')


class ShowroomBranchSerializer(BilingualMixin, serializers.ModelSerializer):
    name_display    = serializers.SerializerMethodField(read_only=True)
    address_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = ShowroomBranch
        fields = (
            'id', 'name', 'name_ar', 'name_display',
            'city_obj', 'address', 'address_ar', 'address_display',
            'phone', 'latitude', 'longitude', 'is_main', 'is_active', 'created_at',
        )
        read_only_fields = ('id', 'created_at', 'name_display', 'address_display')

    def get_name_display(self, obj):
        return self._pick(obj.name, obj.name_ar)

    def get_address_display(self, obj):
        return self._pick(obj.address, obj.address_ar)


class ShowroomReviewSerializer(serializers.ModelSerializer):
    user_id   = serializers.IntegerField(source='user.id', read_only=True)
    user_name = serializers.CharField(source='user.name', read_only=True)

    class Meta:
        model  = ShowroomReview
        fields = (
            'id', 'user_id', 'user_name',
            'rating', 'title', 'comment',
            'is_approved', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'user_id', 'user_name', 'is_approved', 'created_at', 'updated_at')


class ShowroomListSerializer(BilingualMixin, serializers.ModelSerializer):
    """Lightweight serializer for showroom list view."""
    name_display = serializers.SerializerMethodField(read_only=True)
    city_display = serializers.SerializerMethodField(read_only=True)
    logo_url     = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Showroom
        fields = (
            'id', 'name', 'name_ar', 'name_display',
            'city', 'city_display', 'city_obj',
            'is_verified', 'is_active',
            'logo_url',
            'total_listings', 'active_listings',
            'average_rating', 'total_reviews',
            'latitude', 'longitude',
            'created_at',
        )
        read_only_fields = fields

    def get_name_display(self, obj):
        return self._pick(obj.name, obj.name_ar)

    def get_city_display(self, obj):
        if obj.city_obj:
            return self._pick(obj.city_obj.name_en, obj.city_obj.name_ar)
        return obj.city

    def get_logo_url(self, obj):
        return obj.logo.url if obj.logo else None


class ShowroomDetailSerializer(BilingualMixin, serializers.ModelSerializer):
    """Full serializer for showroom detail page."""
    name_display        = serializers.SerializerMethodField(read_only=True)
    description_display = serializers.SerializerMethodField(read_only=True)
    address_display     = serializers.SerializerMethodField(read_only=True)
    city_display        = serializers.SerializerMethodField(read_only=True)
    logo_url            = serializers.SerializerMethodField(read_only=True)
    cover_photo_url     = serializers.SerializerMethodField(read_only=True)
    working_hours       = ShowroomWorkingHoursSerializer(many=True, read_only=True)
    branches            = ShowroomBranchSerializer(many=True, read_only=True)
    is_open_now         = serializers.SerializerMethodField(read_only=True)
    listings_preview    = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Showroom
        fields = (
            'id', 'name', 'name_ar', 'name_display',
            'description', 'description_ar', 'description_display',
            'address', 'address_ar', 'address_display',
            'city', 'city_display', 'city_obj',
            'phone', 'whatsapp', 'email', 'website',
            'instagram', 'twitter', 'snapchat', 'tiktok',
            'commercial_registration',
            'is_verified', 'verified_at', 'is_active',
            'established_year', 'specializations',
            'total_listings', 'active_listings', 'total_sold',
            'average_rating', 'total_reviews',
            'latitude', 'longitude',
            'logo', 'cover_photo', 'logo_url', 'cover_photo_url',
            'working_hours', 'branches',
            'is_open_now', 'listings_preview',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'is_verified', 'verified_at',
            'total_listings', 'active_listings', 'total_sold',
            'average_rating', 'total_reviews',
            'created_at', 'updated_at',
            'name_display', 'description_display', 'address_display', 'city_display',
            'logo_url', 'cover_photo_url',
            'working_hours', 'branches', 'is_open_now', 'listings_preview',
        )

    def get_name_display(self, obj):
        return self._pick(obj.name, obj.name_ar)

    def get_description_display(self, obj):
        return self._pick(obj.description or '', obj.description_ar)

    def get_address_display(self, obj):
        return self._pick(obj.address or '', obj.address_ar)

    def get_city_display(self, obj):
        if obj.city_obj:
            return self._pick(obj.city_obj.name_en, obj.city_obj.name_ar)
        return obj.city

    def get_logo_url(self, obj):
        return obj.logo.url if obj.logo else None

    def get_cover_photo_url(self, obj):
        return obj.cover_photo.url if obj.cover_photo else None

    def get_is_open_now(self, obj):
        """Return True/False/None based on current Saudi time (UTC+3) vs working hours."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        from datetime import datetime
        now_saudi = datetime.now(ZoneInfo('Asia/Riyadh'))
        # Python weekday 0=Mon … 6=Sun → Saudi 0=Sun: (weekday+1)%7
        saudi_day   = (now_saudi.weekday() + 1) % 7
        current_time = now_saudi.time().replace(tzinfo=None)
        try:
            hours = obj.working_hours.get(day=saudi_day)
        except ShowroomWorkingHours.DoesNotExist:
            return None
        if hours.is_closed:
            return False
        if not hours.opening_time or not hours.closing_time:
            return None
        return hours.opening_time <= current_time <= hours.closing_time

    def get_listings_preview(self, obj):
        from .visibility import public_market_q
        request = self.context.get('request')
        qs = Listing.objects.filter(
            public_market_q(getattr(request, 'user', None), browse=True),
            showroom=obj, status='approved', is_active=True,
        ).order_by('-created_at')[:4]
        return [
            {
                'id': lst.id,
                'title': lst.title,
                'make': lst.make,
                'model': lst.model,
                'year': lst.year,
                'price': str(lst.price),
            }
            for lst in qs
        ]


class ShowroomCreateUpdateSerializer(serializers.ModelSerializer):
    """Used for POST/PUT/PATCH on showrooms."""

    class Meta:
        model  = Showroom
        fields = (
            'name', 'name_ar',
            'description', 'description_ar',
            'address', 'address_ar',
            'city', 'city_obj',
            'phone', 'whatsapp', 'email', 'website',
            'instagram', 'twitter', 'snapchat', 'tiktok',
            'commercial_registration',
            'is_active', 'established_year', 'specializations',
            'logo', 'cover_photo',
            'latitude', 'longitude',
        )


# ---------------------------------------------------------------------------
# Phase 4.2 — Workshop Management serializers
# ---------------------------------------------------------------------------

class WorkshopWorkingHoursSerializer(serializers.ModelSerializer):
    day_display = serializers.CharField(source='get_day_display', read_only=True)

    class Meta:
        model  = WorkshopWorkingHours
        fields = ('id', 'day', 'day_display', 'opening_time', 'closing_time', 'is_closed')
        read_only_fields = ('id', 'day_display')


class WorkshopServiceSerializer(BilingualMixin, serializers.ModelSerializer):
    name_display        = serializers.SerializerMethodField(read_only=True)
    description_display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = WorkshopService
        fields = (
            'id', 'name', 'name_ar', 'name_display',
            'description', 'description_ar', 'description_display',
            'category', 'price', 'price_type',
            'duration_minutes', 'is_active', 'order',
        )
        read_only_fields = ('id', 'name_display', 'description_display')

    def get_name_display(self, obj):
        return self._pick(obj.name, obj.name_ar)

    def get_description_display(self, obj):
        return self._pick(obj.description, obj.description_ar)


class WorkshopReviewSerializer(serializers.ModelSerializer):
    user_id      = serializers.IntegerField(source='user.id', read_only=True)
    user_name    = serializers.CharField(source='user.name', read_only=True)
    service_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = WorkshopReview
        fields = (
            'id', 'user_id', 'user_name',
            'rating', 'title', 'comment',
            'service', 'service_name',
            'is_approved', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'user_id', 'user_name', 'service_name', 'is_approved',
            'created_at', 'updated_at',
        )

    def get_service_name(self, obj):
        return obj.service.name if obj.service else None


class WorkshopListSerializer(BilingualMixin, serializers.ModelSerializer):
    """Lightweight serializer for workshop list view."""
    name_display   = serializers.SerializerMethodField(read_only=True)
    city_display   = serializers.SerializerMethodField(read_only=True)
    logo_url       = serializers.SerializerMethodField(read_only=True)
    services_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Workshop
        fields = (
            'id', 'name', 'name_ar', 'name_display',
            'city', 'city_display', 'city_obj',
            'is_verified', 'is_active',
            'logo_url',
            'average_rating', 'total_reviews',
            'specializations', 'services_count',
            'latitude', 'longitude',
            'created_at',
        )
        read_only_fields = fields

    def get_name_display(self, obj):
        return self._pick(obj.name, obj.name_ar)

    def get_city_display(self, obj):
        if obj.city_obj:
            return self._pick(obj.city_obj.name_en, obj.city_obj.name_ar)
        return obj.city

    def get_logo_url(self, obj):
        return obj.logo.url if obj.logo else None

    def get_services_count(self, obj):
        return obj.service_items.filter(is_active=True).count()


class WorkshopDetailSerializer(BilingualMixin, serializers.ModelSerializer):
    """Full serializer for workshop detail page."""
    name_display        = serializers.SerializerMethodField(read_only=True)
    description_display = serializers.SerializerMethodField(read_only=True)
    address_display     = serializers.SerializerMethodField(read_only=True)
    city_display        = serializers.SerializerMethodField(read_only=True)
    logo_url            = serializers.SerializerMethodField(read_only=True)
    cover_photo_url     = serializers.SerializerMethodField(read_only=True)
    working_hours       = WorkshopWorkingHoursSerializer(many=True, read_only=True)
    services            = serializers.SerializerMethodField(read_only=True)
    is_open_now         = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Workshop
        fields = (
            'id', 'name', 'name_ar', 'name_display',
            'description', 'description_ar', 'description_display',
            'address', 'address_ar', 'address_display',
            'city', 'city_display', 'city_obj',
            'phone', 'whatsapp', 'email', 'website',
            'instagram', 'twitter', 'snapchat',
            'commercial_registration',
            'is_verified', 'verified_at', 'is_active',
            'established_year', 'specializations',
            'average_rating', 'total_reviews', 'total_bookings',
            'latitude', 'longitude',
            'logo', 'cover_photo', 'logo_url', 'cover_photo_url',
            'working_hours', 'services',
            'is_open_now',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'is_verified', 'verified_at',
            'average_rating', 'total_reviews', 'total_bookings',
            'created_at', 'updated_at',
            'name_display', 'description_display', 'address_display', 'city_display',
            'logo_url', 'cover_photo_url',
            'working_hours', 'services', 'is_open_now',
        )

    def get_name_display(self, obj):
        return self._pick(obj.name, obj.name_ar)

    def get_description_display(self, obj):
        return self._pick(obj.description or '', obj.description_ar)

    def get_address_display(self, obj):
        return self._pick(obj.address or '', obj.address_ar)

    def get_city_display(self, obj):
        if obj.city_obj:
            return self._pick(obj.city_obj.name_en, obj.city_obj.name_ar)
        return obj.city

    def get_logo_url(self, obj):
        return obj.logo.url if obj.logo else None

    def get_cover_photo_url(self, obj):
        return obj.cover_photo.url if obj.cover_photo else None

    def get_services(self, obj):
        qs = obj.service_items.filter(is_active=True)
        return WorkshopServiceSerializer(qs, many=True, context=self.context).data

    def get_is_open_now(self, obj):
        """Return True/False/None based on current Saudi time (UTC+3) vs working hours."""
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        from datetime import datetime
        now_saudi    = datetime.now(ZoneInfo('Asia/Riyadh'))
        saudi_day    = (now_saudi.weekday() + 1) % 7  # 0=Sunday
        current_time = now_saudi.time().replace(tzinfo=None)
        try:
            hours = obj.working_hours.get(day=saudi_day)
        except WorkshopWorkingHours.DoesNotExist:
            return None
        if hours.is_closed:
            return False
        if not hours.opening_time or not hours.closing_time:
            return None
        return hours.opening_time <= current_time <= hours.closing_time


class WorkshopCreateUpdateSerializer(serializers.ModelSerializer):
    """Used for POST/PUT/PATCH on workshops."""

    class Meta:
        model  = Workshop
        fields = (
            'id',
            'name', 'name_ar',
            'description', 'description_ar',
            'address', 'address_ar',
            'city', 'city_obj',
            'phone', 'whatsapp', 'email', 'website',
            'instagram', 'twitter', 'snapchat',
            'commercial_registration',
            'is_active', 'established_year', 'specializations',
            'logo', 'cover_photo',
            'latitude', 'longitude',
        )
        read_only_fields = ('id',)


# ---------------------------------------------------------------------------
# Phase 4.2 — ServiceBooking serializers
# ---------------------------------------------------------------------------

class ServiceBookingCreateSerializer(serializers.ModelSerializer):
    """POST /api/service-bookings/ — customer creates a service booking."""

    class Meta:
        model  = ServiceBooking
        fields = (
            'workshop', 'service',
            'vehicle_make', 'vehicle_model', 'vehicle_year', 'vehicle_plate',
            'booking_date', 'booking_time',
            'description',
        )

    def validate_booking_date(self, value):
        from django.utils import timezone
        if value < timezone.now().date():
            raise serializers.ValidationError('Booking date cannot be in the past.')
        return value

    def validate(self, attrs):
        request  = self.context.get('request')
        workshop = attrs.get('workshop')
        if request and workshop:
            customer = request.user
            if workshop.owner_id and workshop.owner_id == customer.pk:
                raise serializers.ValidationError(
                    {'workshop': 'You cannot book your own workshop.'}
                )
            pending_count = ServiceBooking.objects.filter(
                customer=customer,
                workshop=workshop,
                status='pending',
            ).count()
            if pending_count >= 3:
                raise serializers.ValidationError(
                    'Maximum 3 pending bookings per workshop. '
                    'Please wait for existing ones to be resolved.'
                )
        service = attrs.get('service')
        if service and workshop and service.workshop_id != workshop.pk:
            raise serializers.ValidationError(
                {'service': 'This service does not belong to the selected workshop.'}
            )
        return attrs


class ServiceBookingListSerializer(BilingualMixin, serializers.ModelSerializer):
    """Full read-only representation returned on list/retrieve."""
    workshop_name = serializers.CharField(source='workshop.name', read_only=True)
    service_name  = serializers.SerializerMethodField(read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)

    class Meta:
        model  = ServiceBooking
        fields = (
            'id',
            'workshop', 'workshop_name',
            'service', 'service_name',
            'customer', 'customer_name',
            'vehicle_make', 'vehicle_model', 'vehicle_year', 'vehicle_plate',
            'booking_date', 'booking_time',
            'description',
            'status',
            'estimated_cost', 'final_cost',
            'notes', 'cancellation_reason',
            'created_at', 'updated_at',
        )
        read_only_fields = fields

    def get_service_name(self, obj):
        return obj.service.name if obj.service else None


class ServiceBookingUpdateSerializer(serializers.ModelSerializer):
    """PATCH /api/service-bookings/{id}/ — status + notes/cost updates."""

    class Meta:
        model  = ServiceBooking
        fields = ('status', 'estimated_cost', 'final_cost', 'notes', 'cancellation_reason')

    def validate_status(self, value):
        instance = self.instance
        if not instance or value == instance.status:
            return value
        allowed = _SERVICE_BOOKING_VALID_TRANSITIONS.get(instance.status, set())
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


# ---------------------------------------------------------------------------
# Phase 2.9 — Advanced Search serializers
# ---------------------------------------------------------------------------

# Build the set of allowed filter keys from ListingFilter at import time so
# it stays in sync automatically when new filters are added to the FilterSet.
def _get_known_filter_keys():
    from .filters import ListingFilter
    return set(ListingFilter.base_filters.keys())


class SavedSearchSerializer(serializers.ModelSerializer):
    """
    Serializer for SavedSearch.
    results_count re-runs the stored filter dict against current approved listings.
    validate_filters rejects any key not present in ListingFilter.
    """
    results_count = serializers.SerializerMethodField()

    class Meta:
        model = SavedSearch
        fields = ('id', 'name', 'filters', 'notify', 'last_checked', 'created_at', 'results_count')
        read_only_fields = ('id', 'last_checked', 'created_at', 'results_count')

    def get_results_count(self, obj):
        from .filters import ListingFilter
        from .visibility import public_market_q
        request = self.context.get('request')
        base_qs = Listing.objects.filter(
            public_market_q(getattr(request, 'user', None), browse=True),
            status='approved', is_active=True,
        )
        return ListingFilter(obj.filters, queryset=base_qs).qs.count()

    def validate_filters(self, value):
        allowed = _get_known_filter_keys()
        unknown = set(value.keys()) - allowed
        if unknown:
            raise serializers.ValidationError(
                f"Unknown filter key(s): {sorted(unknown)}. "
                f"Allowed keys: {sorted(allowed)}."
            )
        return value


class RecentlyViewedSerializer(serializers.ModelSerializer):
    """Returns a RecentlyViewed entry with the full nested listing."""
    listing = ListingSerializer(read_only=True)

    class Meta:
        model = RecentlyViewed
        fields = ('id', 'listing', 'viewed_at')
        read_only_fields = ('id', 'listing', 'viewed_at')


# ---------------------------------------------------------------------------
# Phase 2.15 — Map-pin (lightweight) serializers
# ---------------------------------------------------------------------------

class ListingMapPinSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for /api/listings/map-pins/.
    Only the fields needed to render a map pin + tooltip.
    """
    primary_image_url = serializers.SerializerMethodField(read_only=True)
    #: The same sized variants every other listing endpoint sends.
    primary_image = serializers.SerializerMethodField(read_only=True)
    # One boolean, so a pin can be drawn as "yours" without a second lookup.
    is_owner = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Listing
        fields = (
            'id', 'make', 'model', 'year', 'price',
            'latitude', 'longitude', 'primary_image_url', 'primary_image', 'is_owner',
        )
        read_only_fields = fields

    def get_is_owner(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            return False
        return user.pk == obj.owner_id

    def get_primary_image_url(self, obj):
        from .utils.cloudinary_urls import primary_image_url

        return primary_image_url(obj)

    def get_primary_image(self, obj):
        from .utils.cloudinary_urls import primary_image_payload

        return primary_image_payload(obj)


class NearbyListingSerializer(ListingMapPinSerializer):
    """Extends ListingMapPinSerializer with distance_km for /api/listings/nearby/."""
    distance_km = serializers.FloatField(read_only=True)

    class Meta(ListingMapPinSerializer.Meta):
        fields = ListingMapPinSerializer.Meta.fields + ('distance_km',)
        read_only_fields = fields


class ShowroomMapPinSerializer(serializers.ModelSerializer):
    """Lightweight serializer for /api/showrooms/map-pins/."""
    logo_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Showroom
        fields = ('id', 'name', 'city', 'verified', 'latitude', 'longitude', 'logo_url')
        read_only_fields = fields

    def get_logo_url(self, obj):
        return obj.logo.url if obj.logo else None


class NearbyShowroomSerializer(ShowroomMapPinSerializer):
    """Extends ShowroomMapPinSerializer with distance_km for /api/showrooms/nearby/."""
    distance_km = serializers.FloatField(read_only=True)

    class Meta(ShowroomMapPinSerializer.Meta):
        fields = ShowroomMapPinSerializer.Meta.fields + ('distance_km',)
        read_only_fields = fields


class WorkshopMapPinSerializer(serializers.ModelSerializer):
    """Lightweight serializer for /api/workshops/map-pins/."""
    logo_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Workshop
        fields = ('id', 'name', 'city', 'rating', 'latitude', 'longitude', 'logo_url')
        read_only_fields = fields

    def get_logo_url(self, obj):
        return obj.logo.url if obj.logo else None


class NearbyWorkshopSerializer(WorkshopMapPinSerializer):
    """Extends WorkshopMapPinSerializer with distance_km for /api/workshops/nearby/."""
    distance_km = serializers.FloatField(read_only=True)

    class Meta(WorkshopMapPinSerializer.Meta):
        fields = WorkshopMapPinSerializer.Meta.fields + ('distance_km',)
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Phase 4.5 — Bulk Operations
# ---------------------------------------------------------------------------

class BulkUploadSerializer(serializers.ModelSerializer):
    """Read serializer for BulkUpload — returned on list/retrieve."""

    class Meta:
        model  = BulkUpload
        fields = (
            'id', 'file_name', 'status',
            'total_rows', 'successful_rows', 'failed_rows',
            'errors', 'created_at', 'completed_at',
        )
        read_only_fields = fields


class BulkUploadCreateSerializer(serializers.Serializer):
    """Write serializer — accepts a CSV file upload."""
    file = serializers.FileField()

    def validate_file(self, value):
        name = value.name.lower()
        if not name.endswith('.csv'):
            raise serializers.ValidationError('Only .csv files are accepted.')
        if value.size > 5 * 1024 * 1024:  # 5 MB
            raise serializers.ValidationError('File must be smaller than 5 MB.')
        return value


class BulkStatusChangeSerializer(serializers.Serializer):
    """POST /api/listings/bulk/status/"""
    listing_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=1,
        max_length=50,
    )
    status = serializers.ChoiceField(choices=['draft', 'pending', 'approved', 'rejected', 'sold'])


class BulkDeleteSerializer(serializers.Serializer):
    """POST /api/listings/bulk/delete/"""
    listing_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=1,
        max_length=50,
    )


# ---------------------------------------------------------------------------
# Phase 4.6 — Promotion Serializers
# ---------------------------------------------------------------------------

class PromotionPackageSerializer(serializers.ModelSerializer):
    """Read-only serializer for PromotionPackage."""

    class Meta:
        model  = PromotionPackage
        fields = ('id', 'name', 'slug', 'description', 'duration_days',
                  'price', 'promotion_type', 'priority')


class ListingPromotionSerializer(serializers.ModelSerializer):
    """Full serializer for ListingPromotion (list + detail)."""

    package        = PromotionPackageSerializer(read_only=True)
    listing        = serializers.SerializerMethodField()
    is_active_now  = serializers.SerializerMethodField()
    days_remaining = serializers.SerializerMethodField()

    class Meta:
        model  = ListingPromotion
        fields = (
            'id', 'listing', 'package', 'dealer', 'status',
            'started_at', 'expires_at', 'days_remaining',
            'is_active_now', 'amount_paid', 'created_at',
        )
        read_only_fields = fields

    def get_listing(self, obj):
        return {
            'id':    obj.listing_id,
            'make':  obj.listing.make,
            'model': obj.listing.model,
            'year':  obj.listing.year,
            'price': str(obj.listing.price),
        }

    def get_is_active_now(self, obj) -> bool:
        return obj.is_active_now

    def get_days_remaining(self, obj) -> int:
        return obj.days_remaining


class PromoteListingSerializer(serializers.Serializer):
    """Input serializer for POST /api/listings/{id}/promote/."""

    package_id = serializers.IntegerField()

    def validate(self, data):
        try:
            pkg = PromotionPackage.objects.get(pk=data['package_id'], is_active=True)
        except PromotionPackage.DoesNotExist:
            raise serializers.ValidationError(
                {'package_id': 'Promotion package not found or inactive.'}
            )
        data['package'] = pkg
        return data

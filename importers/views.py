from rest_framework import permissions, status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from django.utils import timezone
from rest_framework.parsers import MultiPartParser, FormParser

from .models import CRVerificationHistory, ImporterProfile
from .serializers import (
    ImporterProfileDetailSerializer,
    ImporterProfileListSerializer,
    ImporterProfileOwnerSerializer,
    ImporterProfileUpdateSerializer,
)


class ImporterProfileListView(ListAPIView):
    """GET /api/importers/ — public browse of verified importers."""
    permission_classes = [permissions.AllowAny]
    serializer_class   = ImporterProfileListSerializer

    def get_queryset(self):
        qs = ImporterProfile.objects.filter(is_verified=True).select_related('city')
        p  = self.request.query_params
        if q := p.get('search'):
            qs = qs.filter(business_name__icontains=q)
        if cc := p.get('source_countries'):
            # JSON contains filter — works on PostgreSQL
            qs = qs.filter(source_countries__contains=[cc])
        if sp := p.get('specializations'):
            qs = qs.filter(specializations__contains=[sp])
        if city_id := p.get('city'):
            qs = qs.filter(city_id=city_id)
        return qs


class ImporterProfileDetailView(APIView):
    """GET /api/importers/{id}/"""
    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        try:
            profile = ImporterProfile.objects.select_related('city', 'user').get(pk=pk)
        except ImporterProfile.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ImporterProfileDetailSerializer(profile).data)


class ImporterInventoryView(ListAPIView):
    """GET /api/importers/{id}/inventory/ — importer's active listings."""
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        from cars.models import Listing
        try:
            profile = ImporterProfile.objects.get(pk=self.kwargs['pk'])
        except ImporterProfile.DoesNotExist:
            return Listing.objects.none()
        return Listing.objects.filter(
            owner=profile.user,
            is_active=True,
            import_status__in=['available', 'reserved', 'shipping'],
        ).select_related('owner', 'city_obj').prefetch_related('images')

    def get_serializer_class(self):
        from cars.serializers import ListingSerializer
        return ListingSerializer


class ImporterReviewsView(APIView):
    """GET /api/importers/{id}/reviews/ — public reviews for an importer."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        try:
            profile = ImporterProfile.objects.select_related('user').get(pk=pk)
        except ImporterProfile.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        try:
            from reviews.models import Review
            from reviews.serializers import ReviewSerializer
            qs = Review.objects.filter(
                reviewed_user=profile.user, status='approved'
            ).select_related('reviewer').order_by('-created_at')
            return Response(ReviewSerializer(qs, many=True, context={'request': request}).data)
        except (ImportError, Exception):
            return Response([])

    def post(self, request, pk):
        """POST — buyer leaves review for importer (must have completed order)."""
        if not request.user.is_authenticated:
            return Response({'detail': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            profile = ImporterProfile.objects.select_related('user').get(pk=pk)
        except ImporterProfile.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        # Validate completed order
        from orders.models import ImportOrder
        has_completed = ImportOrder.objects.filter(
            buyer=request.user,
            importer=profile.user,
            status='completed',
        ).exists()
        if not has_completed:
            return Response(
                {'detail': 'You can only review importers after a completed order.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            from reviews.serializers import ReviewCreateSerializer
            data = dict(request.data)
            data['reviewed_user'] = profile.user.id
            data['review_type']   = 'buyer_to_seller'
            serializer = ReviewCreateSerializer(data=data, context={'request': request})
            serializer.is_valid(raise_exception=True)
            review = serializer.save()
            from reviews.serializers import ReviewSerializer
            return Response(ReviewSerializer(review, context={'request': request}).data, status=status.HTTP_201_CREATED)
        except ImportError:
            return Response({'detail': 'Reviews system not available.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


class MyImporterProfileView(APIView):
    """GET/PATCH /api/importers/me/ — importer manages own profile."""
    permission_classes = [permissions.IsAuthenticated]

    def get_profile(self, user):
        try:
            return ImporterProfile.objects.select_related('city').get(user=user)
        except ImporterProfile.DoesNotExist:
            return None

    def get(self, request):
        if getattr(request.user, 'role', '') not in ('importer', 'admin'):
            return Response({'detail': 'Only importers can access this endpoint.'}, status=status.HTTP_403_FORBIDDEN)
        profile = self.get_profile(request.user)
        if not profile:
            return Response({'detail': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(ImporterProfileOwnerSerializer(profile).data)

    def patch(self, request):
        if getattr(request.user, 'role', '') not in ('importer', 'admin'):
            return Response({'detail': 'Only importers can access this endpoint.'}, status=status.HTTP_403_FORBIDDEN)
        profile = self.get_profile(request.user)
        if not profile:
            return Response({'detail': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = ImporterProfileUpdateSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ImporterProfileOwnerSerializer(profile).data)


# ---------------------------------------------------------------------------
# Phase B — CR Lifecycle Endpoints
# ---------------------------------------------------------------------------

class CRSubmitRenewalView(APIView):
    """POST /api/importer/cr/submit-renewal/"""
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        user = request.user
        if user.role != 'importer':
            return Response({'error': 'Only importers can submit CR renewals.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            profile = user.importer_profile
        except ImporterProfile.DoesNotExist:
            return Response({'error': 'Importer profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        cr_document = request.FILES.get('cr_document')
        cr_expiry_date = request.data.get('cr_expiry_date')

        if not cr_document or not cr_expiry_date:
            return Response({'error': 'cr_document and cr_expiry_date are required.'}, status=status.HTTP_400_BAD_REQUEST)

        from datetime import date
        try:
            expiry = date.fromisoformat(cr_expiry_date)
        except (ValueError, TypeError):
            return Response({'error': 'Invalid cr_expiry_date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        if expiry <= date.today():
            return Response({'error': 'CR expiry date must be in the future.'}, status=status.HTTP_400_BAD_REQUEST)

        old_status = profile.cr_verification_status
        profile.cr_document = cr_document
        cr_issue = request.data.get('cr_issue_date')
        if cr_issue:
            try:
                profile.cr_issue_date = date.fromisoformat(cr_issue)
            except (ValueError, TypeError):
                pass
        profile.cr_expiry_date = expiry
        profile.cr_verification_status = 'renewal_under_review'
        profile.save(update_fields=['cr_document', 'cr_issue_date', 'cr_expiry_date', 'cr_verification_status'])

        CRVerificationHistory.objects.create(
            importer_profile=profile, action='renewal_submitted',
            old_status=old_status, new_status='renewal_under_review',
            cr_expiry_date=expiry, performed_by=user,
            notes=f"CR renewal submitted. New expiry: {expiry}",
        )

        from django.contrib.auth import get_user_model
        User = get_user_model()
        from notifications.utils import notify
        for admin in User.objects.filter(role='admin', is_active=True):
            notify(recipient=admin, notification_type='system',
                   title=f'CR Renewal: {profile.business_name}',
                   message=f'{profile.business_name} submitted a CR renewal for review.')

        return Response({'status': 'pending_review', 'message': 'Your CR renewal has been submitted and is under review.', 'cr_expiry_date': str(expiry)})


class CRStatusView(APIView):
    """GET /api/importer/cr/status/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != 'importer':
            return Response({'error': 'Only importers can view CR status.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            profile = request.user.importer_profile
        except ImporterProfile.DoesNotExist:
            return Response({'error': 'Importer profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        from datetime import date
        days_until = (profile.cr_expiry_date - date.today()).days if profile.cr_expiry_date else None
        history = CRVerificationHistory.objects.filter(importer_profile=profile)[:5]

        return Response({
            'cr_verification_status': profile.cr_verification_status,
            'cr_expiry_date': str(profile.cr_expiry_date) if profile.cr_expiry_date else None,
            'days_until_expiry': days_until,
            'can_submit_renewal': profile.cr_verification_status != 'renewal_under_review',
            'history': [{'action': h.get_action_display(), 'notes': h.notes, 'created_at': h.created_at.isoformat()} for h in history],
        })


class AdminCRReviewsView(APIView):
    """GET /api/admin/cr-reviews/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != 'admin':
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        status_filter = request.query_params.get('status')
        qs = ImporterProfile.objects.select_related('user')
        qs = qs.filter(cr_verification_status=status_filter) if status_filter else qs.filter(cr_verification_status='renewal_under_review')
        return Response([{
            'id': ip.id, 'business_name': ip.business_name, 'user_email': ip.user.email,
            'cr_verification_status': ip.cr_verification_status,
            'cr_expiry_date': str(ip.cr_expiry_date) if ip.cr_expiry_date else None,
            'cr_document': ip.cr_document.url if ip.cr_document else None,
        } for ip in qs])


class AdminCRApproveView(APIView):
    """POST /api/admin/cr-reviews/{id}/approve/"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role != 'admin':
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            profile = ImporterProfile.objects.select_related('user').get(pk=pk)
        except ImporterProfile.DoesNotExist:
            return Response({'error': 'Importer not found.'}, status=status.HTTP_404_NOT_FOUND)

        from datetime import date
        new_expiry = request.data.get('new_expiry_date')
        if new_expiry:
            try:
                profile.cr_expiry_date = date.fromisoformat(new_expiry)
            except (ValueError, TypeError):
                return Response({'error': 'Invalid date format.'}, status=status.HTTP_400_BAD_REQUEST)

        was_suspended = profile.cr_verification_status == 'suspended'
        old_status = profile.cr_verification_status
        profile.cr_verification_status = 'verified'
        profile.cr_last_confirmed_date = date.today()
        if was_suspended:
            profile.cr_reactivated_at = timezone.now()
        profile.save()

        CRVerificationHistory.objects.create(
            importer_profile=profile, action='reactivated' if was_suspended else 'renewal_approved',
            old_status=old_status, new_status='verified',
            cr_expiry_date=profile.cr_expiry_date, performed_by=request.user,
            notes=request.data.get('notes', ''),
        )

        if was_suspended:
            from cars.models import Listing
            Listing.objects.filter(owner=profile.user, status='archived').update(status='approved', is_active=True)

        from notifications.utils import notify
        notify(recipient=profile.user, notification_type='system', title='CR Renewal Approved',
               message='Your Commercial Registration has been verified. Your account is fully active.')
        try:
            from notifications.emails import send_templated_email
            send_templated_email(to_email=profile.user.email, subject='CR Renewal Approved — WARED',
                                 template_name='cr_renewal_approved',
                                 context={'name': profile.user.name, 'business_name': profile.business_name})
        except Exception:
            pass
        return Response({'detail': f'CR approved for {profile.business_name}.', 'status': 'verified'})


class AdminCRRejectView(APIView):
    """POST /api/admin/cr-reviews/{id}/reject/"""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role != 'admin':
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            profile = ImporterProfile.objects.select_related('user').get(pk=pk)
        except ImporterProfile.DoesNotExist:
            return Response({'error': 'Importer not found.'}, status=status.HTTP_404_NOT_FOUND)

        reason = request.data.get('reason', '')
        old_status = profile.cr_verification_status
        profile.cr_verification_status = 'expired' if (profile.cr_expiry_date and profile.cr_expiry_date < timezone.now().date()) else ('verified' if old_status == 'renewal_under_review' else old_status)
        profile.save(update_fields=['cr_verification_status'])

        CRVerificationHistory.objects.create(
            importer_profile=profile, action='renewal_rejected',
            old_status=old_status, new_status=profile.cr_verification_status,
            cr_expiry_date=profile.cr_expiry_date, performed_by=request.user,
            notes=f"Rejected: {reason}",
        )

        from notifications.utils import notify
        notify(recipient=profile.user, notification_type='system', title='CR Renewal Rejected',
               message=f'Your CR renewal was rejected. Reason: {reason}. You may resubmit.')
        try:
            from notifications.emails import send_templated_email
            send_templated_email(to_email=profile.user.email, subject='CR Renewal Rejected — WARED',
                                 template_name='cr_renewal_rejected',
                                 context={'name': profile.user.name, 'business_name': profile.business_name, 'reason': reason})
        except Exception:
            pass
        return Response({'detail': f'CR rejected for {profile.business_name}.', 'reason': reason})


class AdminCRDashboardView(APIView):
    """GET /api/admin/cr-dashboard/"""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != 'admin':
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        from datetime import date, timedelta as td
        today = date.today()
        qs = ImporterProfile.objects.all()
        history = CRVerificationHistory.objects.select_related('importer_profile')[:20]
        return Response({
            'importers_verified': qs.filter(cr_verification_status='verified').count(),
            'importers_expiring_60d': qs.filter(cr_expiry_date__lte=today + td(days=60), cr_expiry_date__gt=today + td(days=30), cr_verification_status__in=['verified', 'expiring_soon']).count(),
            'importers_expiring_30d': qs.filter(cr_expiry_date__lte=today + td(days=30), cr_expiry_date__gt=today + td(days=7), cr_verification_status__in=['verified', 'expiring_soon']).count(),
            'importers_expiring_7d': qs.filter(cr_expiry_date__lte=today + td(days=7), cr_expiry_date__gt=today, cr_verification_status='expiring_soon').count(),
            'importers_expired_suspended': qs.filter(cr_verification_status='suspended').count(),
            'importers_pending_renewal_review': qs.filter(cr_verification_status='renewal_under_review').count(),
            'recent_actions': [{'business_name': h.importer_profile.business_name, 'action': h.get_action_display(), 'notes': h.notes, 'created_at': h.created_at.isoformat()} for h in history],
        })

from django.db.models import Q
from rest_framework import viewsets, status, mixins
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from auditlog.utils import get_client_ip, log_action
from .models import Lead
from .serializers import LeadCreateSerializer, LeadSerializer, LeadUpdateSerializer


@extend_schema(tags=['Leads'])
class LeadViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    POST   /api/leads/          — any authenticated user creates a lead
    GET    /api/leads/          — importer sees own leads; admin sees all; buyer sees sent
    GET    /api/leads/{id}/     — visible to importer, admin, or the buyer
    PATCH  /api/leads/{id}/     — importer or admin only; updates status / dealer_notes
    No DELETE — leads are permanent business records.
    """

    permission_classes = [IsAuthenticated]
    http_method_names  = ['get', 'post', 'patch', 'head', 'options']

    # -- serializer routing --------------------------------------------------

    def get_serializer_class(self):
        if self.action == 'create':
            return LeadCreateSerializer
        if self.action in ('update', 'partial_update'):
            return LeadUpdateSerializer
        return LeadSerializer

    # -- queryset -----------------------------------------------------------

    def get_queryset(self):
        user = self.request.user
        base = (
            Lead.objects
            .select_related('buyer', 'dealer', 'listing')
            .prefetch_related('listing__images')
        )

        if getattr(user, 'role', None) == 'admin':
            qs = base.all()
        elif getattr(user, 'role', None) == 'importer':
            qs = base.filter(dealer=user)
        else:
            qs = base.filter(buyer=user)

        # -- filters from query params --
        status_param  = self.request.query_params.get('status')
        listing_param = self.request.query_params.get('listing')
        date_from     = self.request.query_params.get('created_at__gte')
        date_to       = self.request.query_params.get('created_at__lte')
        search        = self.request.query_params.get('search')

        if status_param:
            qs = qs.filter(status=status_param)
        if listing_param:
            qs = qs.filter(listing_id=listing_param)
        if date_from:
            qs = qs.filter(created_at__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__lte=date_to)
        if search:
            qs = qs.filter(
                Q(buyer__name__icontains=search)
                | Q(buyer__email__icontains=search)
                | Q(listing__title__icontains=search)
            )

        return qs.order_by('-created_at')

    # -- retrieve: also accessible to the buyer ----------------------------

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        user = request.user
        is_dealer = (instance.dealer_id == user.pk)
        is_buyer  = (instance.buyer_id == user.pk)
        is_admin  = (getattr(user, 'role', None) == 'admin')
        if not (is_dealer or is_buyer or is_admin):
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    # -- create -------------------------------------------------------------

    def perform_create(self, serializer):
        listing = serializer.validated_data['listing']
        lead = serializer.save(buyer=self.request.user, dealer=listing.owner)
        log_action(
            user=self.request.user,
            action='create',
            model_name='Lead',
            object_id=lead.pk,
            old_value=None,
            new_value={
                'listing_id': lead.listing_id,
                'source':     lead.source,
            },
            ip_address=get_client_ip(self.request),
        )
        from notifications.utils import notify
        notify(
            recipient=listing.owner,
            notification_type='new_lead',
            title='New lead on your listing',
            message=(
                f'{self.request.user.name or self.request.user.email} '
                f'is interested in your listing "{listing.title}".'
            ),
            listing=listing,
            lead=lead,
        )
        # Create or reuse a conversation thread for this lead
        from messaging.models import Conversation, Message
        conv, _ = Conversation.objects.get_or_create(
            listing=listing, buyer=self.request.user, seller=listing.owner,
            defaults={'lead': lead},
        )
        Message.objects.create(
            conversation=conv, sender=self.request.user,
            content=lead.message, is_system=True,
        )
        from notifications.tasks import send_new_lead_email
        send_new_lead_email.delay(lead.pk)
        from notifications.sms_tasks import send_new_lead_sms
        send_new_lead_sms.delay(lead.pk)

    # -- partial update (dealer only) ---------------------------------------

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        user = request.user

        is_dealer = (instance.dealer_id == user.pk)
        is_admin  = (getattr(user, 'role', None) == 'admin')
        if not (is_dealer or is_admin):
            return Response(
                {'error': 'Only the importer or an admin can update a lead.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        old_status = instance.status
        kwargs['partial'] = True
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        lead = serializer.save()

        if lead.status != old_status:
            log_action(
                user=request.user,
                action='status_change',
                model_name='Lead',
                object_id=lead.pk,
                old_value={'status': old_status},
                new_value={'status': lead.status},
                ip_address=get_client_ip(request),
            )

        return Response(LeadSerializer(lead, context={'request': request}).data)

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from auditlog.utils import get_client_ip, log_action
from .models import Appointment, ServiceBooking, _SERVICE_BOOKING_VALID_TRANSITIONS
from .serializers import (
    AppointmentCreateSerializer,
    AppointmentListSerializer,
    AppointmentUpdateSerializer,
)
from cars.serializers import (
    ServiceBookingCreateSerializer,
    ServiceBookingListSerializer,
    ServiceBookingUpdateSerializer,
)


@extend_schema(tags=['Bookings'])
class AppointmentViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/appointments/             — list (scoped by role)
    POST   /api/appointments/             — buyer creates appointment
    GET    /api/appointments/{id}/        — retrieve (buyer/seller/admin)
    PATCH  /api/appointments/{id}/        — update status/notes
    GET    /api/appointments/upcoming/    — confirmed appts in next 7 days
    GET    /api/appointments/stats/       — seller stats

    No DELETE — appointments are permanent business records.
    """

    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    # -- Serializer routing -------------------------------------------------

    def get_serializer_class(self):
        if self.action == 'create':
            return AppointmentCreateSerializer
        if self.action in ('update', 'partial_update'):
            return AppointmentUpdateSerializer
        return AppointmentListSerializer

    # -- Queryset -----------------------------------------------------------

    def get_queryset(self):
        user = self.request.user
        base = (
            Appointment.objects
            .select_related('listing', 'buyer', 'seller')
            .prefetch_related('listing__images')
        )

        if getattr(user, 'role', None) == 'admin':
            qs = base.all()
        else:
            # Both buyers and sellers see their relevant appointments.
            qs = base.filter(Q(buyer=user) | Q(seller=user))

        # Manual filter params
        status_p  = self.request.query_params.get('status')
        listing_p = self.request.query_params.get('listing')
        date_gte  = self.request.query_params.get('appointment_date__gte')
        date_lte  = self.request.query_params.get('appointment_date__lte')
        cat_gte   = self.request.query_params.get('created_at__gte')
        cat_lte   = self.request.query_params.get('created_at__lte')

        if status_p:
            qs = qs.filter(status=status_p)
        if listing_p:
            qs = qs.filter(listing_id=listing_p)
        if date_gte:
            qs = qs.filter(appointment_date__gte=date_gte)
        if date_lte:
            qs = qs.filter(appointment_date__lte=date_lte)
        if cat_gte:
            qs = qs.filter(created_at__gte=cat_gte)
        if cat_lte:
            qs = qs.filter(created_at__lte=cat_lte)

        return qs.order_by('appointment_date', 'appointment_time')

    # -- Retrieve (buyer, seller, or admin only) ----------------------------

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        user = request.user
        is_buyer  = instance.buyer_id == user.pk
        is_seller = instance.seller_id == user.pk
        is_admin  = getattr(user, 'role', None) == 'admin'
        if not (is_buyer or is_seller or is_admin):
            return Response(
                {'error': 'Permission denied.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = AppointmentListSerializer(instance, context={'request': request})
        return Response(serializer.data)

    # -- Create (auto-set buyer + seller from listing.owner) ----------------

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except DjangoValidationError as exc:
            msgs = exc.message_dict if hasattr(exc, 'message_dict') else {'error': exc.messages}
            return Response(msgs, status=status.HTTP_400_BAD_REQUEST)
        headers = self.get_success_headers(serializer.data)
        instance = serializer.instance
        return Response(
            AppointmentListSerializer(instance, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
            headers=headers,
        )

    def perform_create(self, serializer):
        listing = serializer.validated_data['listing']
        instance = serializer.save(
            buyer=self.request.user,
            seller=listing.owner,
        )
        log_action(
            user=self.request.user,
            action='create',
            model_name='Appointment',
            object_id=instance.pk,
            old_value=None,
            new_value={
                'listing_id':       instance.listing_id,
                'appointment_date': str(instance.appointment_date),
                'appointment_time': str(instance.appointment_time),
            },
            ip_address=get_client_ip(self.request),
        )

    # -- Partial update (role-based restrictions) ----------------------------

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        user = request.user

        is_buyer  = instance.buyer_id == user.pk
        is_seller = instance.seller_id == user.pk
        is_admin  = getattr(user, 'role', None) == 'admin'

        if not (is_buyer or is_seller or is_admin):
            return Response(
                {'error': 'Permission denied.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Buyers can only cancel.
        if is_buyer and not is_admin:
            new_status = request.data.get('status')
            if new_status and new_status != 'cancelled':
                return Response(
                    {'error': 'As a buyer you can only cancel appointments.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if 'seller_notes' in request.data:
                return Response(
                    {'error': 'Buyers cannot set seller_notes.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        old_status = instance.status
        kwargs['partial'] = True
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            appt = serializer.save()
        except DjangoValidationError as exc:
            msgs = exc.message_dict if hasattr(exc, 'message_dict') else {'error': exc.messages}
            return Response(msgs, status=status.HTTP_400_BAD_REQUEST)

        if appt.status != old_status:
            log_action(
                user=request.user,
                action='status_change',
                model_name='Appointment',
                object_id=appt.pk,
                old_value={'status': old_status},
                new_value={'status': appt.status},
                ip_address=get_client_ip(request),
            )
            from notifications.utils import notify
            _APPT_NOTIF = {
                'confirmed':  ('appointment_confirmed', 'Appointment Confirmed',
                               'Your appointment has been confirmed.'),
                'rejected':   ('appointment_rejected',  'Appointment Rejected',
                               'Your appointment request was rejected.'),
                'cancelled':  ('appointment_cancelled', 'Appointment Cancelled',
                               'An appointment has been cancelled.'),
            }
            if appt.status in _APPT_NOTIF:
                ntype, title, msg = _APPT_NOTIF[appt.status]
                # Notify the party that did NOT make the change.
                recipient = appt.buyer if request.user.pk == appt.seller_id else appt.seller
                notify(
                    recipient=recipient,
                    notification_type=ntype,
                    title=title,
                    message=msg,
                    listing=appt.listing,
                    appointment=appt,
                )
                from notifications.tasks import (
                    send_appointment_confirmed_email,
                    send_appointment_rejected_email,
                )
                if appt.status == 'confirmed':
                    send_appointment_confirmed_email.delay(appt.pk)
                elif appt.status == 'rejected':
                    send_appointment_rejected_email.delay(appt.pk)
                from notifications.sms_tasks import send_appointment_sms
                if appt.status in ('confirmed', 'rejected'):
                    send_appointment_sms.delay(appt.pk, appt.status)

        return Response(
            AppointmentListSerializer(appt, context={'request': request}).data
        )

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    # -- Custom action: upcoming confirmed appointments (next 7 days) -------

    @action(detail=False, methods=['get'], url_path='upcoming')
    def upcoming(self, request):
        """
        GET /api/appointments/upcoming/
        Returns confirmed appointments in the next 7 days for the current user
        (as buyer or seller).
        """
        today     = timezone.now().date()
        next_week = today + timedelta(days=7)
        user = request.user

        qs = (
            Appointment.objects
            .filter(
                Q(buyer=user) | Q(seller=user),
                status='confirmed',
                appointment_date__gte=today,
                appointment_date__lte=next_week,
            )
            .select_related('listing', 'buyer', 'seller')
            .prefetch_related('listing__images')
            .order_by('appointment_date', 'appointment_time')
        )
        serializer = AppointmentListSerializer(qs, many=True, context={'request': request})
        return Response({'count': qs.count(), 'results': serializer.data})

    # -- Custom action: seller appointment stats ----------------------------

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        """
        GET /api/appointments/stats/
        Dealer/admin only — aggregate stats for appointments where user is seller.
        """
        user = request.user
        if getattr(user, 'role', None) not in ('importer', 'admin'):
            return Response(
                {'error': 'Only sellers/importers can view appointment stats.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        today       = timezone.now().date()
        week_start  = today - timedelta(days=today.weekday())   # Monday this week
        month_start = today.replace(day=1)

        qs = Appointment.objects.filter(seller=user)
        total = qs.count()

        by_status = {
            s: qs.filter(status=s).count()
            for s, _ in Appointment.STATUS_CHOICES
        }

        this_week  = qs.filter(appointment_date__gte=week_start).count()
        this_month = qs.filter(appointment_date__gte=month_start).count()

        # Completion rate = completed / (completed + no_show)
        resolved = by_status.get('completed', 0) + by_status.get('no_show', 0)
        completion_rate = (
            round(by_status.get('completed', 0) / resolved * 100, 1)
            if resolved > 0 else 0.0
        )

        return Response({
            'total':           total,
            'by_status':       by_status,
            'this_week':       this_week,
            'this_month':      this_month,
            'completion_rate': completion_rate,
        })


# ---------------------------------------------------------------------------
# Phase 4.2 — ServiceBookingViewSet
# ---------------------------------------------------------------------------

@extend_schema(tags=['Service Bookings'])
class ServiceBookingViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/service-bookings/           — list (customer: own, workshop owner: their workshop's)
    POST   /api/service-bookings/           — customer books a service
    GET    /api/service-bookings/{id}/      — retrieve
    PATCH  /api/service-bookings/{id}/      — update status / notes
    GET    /api/service-bookings/upcoming/  — confirmed bookings in next 7 days
    """

    permission_classes    = [IsAuthenticated]
    http_method_names     = ['get', 'post', 'patch', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'create':
            return ServiceBookingCreateSerializer
        if self.action in ('update', 'partial_update'):
            return ServiceBookingUpdateSerializer
        return ServiceBookingListSerializer

    def get_queryset(self):
        user = self.request.user
        base = (
            ServiceBooking.objects
            .select_related('workshop', 'service', 'customer')
        )
        if getattr(user, 'role', None) == 'admin':
            qs = base.all()
        elif hasattr(user, 'workshops'):
            # Workshop owner sees bookings for their own workshops + their own customer bookings
            from django.db.models import Q
            owned_workshops = user.workshops.values_list('pk', flat=True)
            qs = base.filter(
                Q(customer=user) | Q(workshop_id__in=owned_workshops)
            )
        else:
            qs = base.filter(customer=user)

        # Optional filters
        status_p   = self.request.query_params.get('status')
        workshop_p = self.request.query_params.get('workshop')
        date_gte   = self.request.query_params.get('booking_date__gte')
        date_lte   = self.request.query_params.get('booking_date__lte')
        if status_p:
            qs = qs.filter(status=status_p)
        if workshop_p:
            qs = qs.filter(workshop_id=workshop_p)
        if date_gte:
            qs = qs.filter(booking_date__gte=date_gte)
        if date_lte:
            qs = qs.filter(booking_date__lte=date_lte)

        return qs.order_by('booking_date', 'booking_time')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except DjangoValidationError as exc:
            msgs = exc.message_dict if hasattr(exc, 'message_dict') else {'error': exc.messages}
            return Response(msgs, status=status.HTTP_400_BAD_REQUEST)
        instance = serializer.instance
        log_action(
            user=request.user, action='create',
            model_name='ServiceBooking', object_id=instance.pk,
            ip_address=get_client_ip(request),
        )
        # Notify workshop owner
        from notifications.utils import notify
        if instance.workshop.owner:
            notify(
                recipient=instance.workshop.owner,
                notification_type='system',
                title='New Service Booking',
                message=(
                    f'{request.user.name} booked '
                    f'{instance.service.name if instance.service else "a service"} '
                    f'at {instance.workshop.name} on {instance.booking_date}.'
                ),
                metadata={'service_booking_id': instance.pk},
            )
        return Response(
            ServiceBookingListSerializer(instance, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        serializer.save(customer=self.request.user)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        user     = request.user

        is_customer = instance.customer_id == user.pk
        is_owner    = instance.workshop.owner_id == user.pk if instance.workshop.owner_id else False
        is_admin    = getattr(user, 'role', None) == 'admin'

        if not (is_customer or is_owner or is_admin):
            return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        # Customers can only cancel
        if is_customer and not is_admin:
            new_status = request.data.get('status')
            if new_status and new_status != 'cancelled':
                return Response(
                    {'error': 'Customers can only cancel bookings.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if 'notes' in request.data:
                return Response(
                    {'error': 'Customers cannot set workshop notes.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        old_status = instance.status
        kwargs['partial'] = True
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            booking = serializer.save()
        except DjangoValidationError as exc:
            msgs = exc.message_dict if hasattr(exc, 'message_dict') else {'error': exc.messages}
            return Response(msgs, status=status.HTTP_400_BAD_REQUEST)

        if booking.status != old_status:
            log_action(
                user=request.user, action='status_change',
                model_name='ServiceBooking', object_id=booking.pk,
                old_value={'status': old_status},
                new_value={'status': booking.status},
                ip_address=get_client_ip(request),
            )
            from notifications.utils import notify
            _BOOKING_NOTIF = {
                'confirmed':   'Your workshop booking has been confirmed.',
                'in_progress': 'Work on your vehicle has started.',
                'completed':   'Your vehicle service has been completed.',
                'cancelled':   'A workshop booking has been cancelled.',
            }
            msg = _BOOKING_NOTIF.get(booking.status)
            if msg:
                # Notify the other party
                recipient = booking.customer if is_owner or is_admin else booking.workshop.owner
                if recipient:
                    notify(
                        recipient=recipient,
                        notification_type='system',
                        title=f'Booking {booking.status.replace("_", " ").title()}',
                        message=msg,
                        metadata={'service_booking_id': booking.pk},
                    )

            # Send email on confirmed / completed
            if booking.status in ('confirmed', 'completed'):
                from notifications.tasks import send_service_booking_email
                send_service_booking_email.delay(booking.pk, booking.status)

        return Response(
            ServiceBookingListSerializer(booking, context={'request': request}).data
        )

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='upcoming')
    def upcoming(self, request):
        """GET /api/service-bookings/upcoming/ — confirmed bookings in the next 7 days."""
        today     = timezone.now().date()
        next_week = today + timedelta(days=7)
        user      = request.user
        from django.db.models import Q
        owned_workshops = user.workshops.values_list('pk', flat=True) if hasattr(user, 'workshops') else []
        qs = (
            ServiceBooking.objects
            .filter(
                Q(customer=user) | Q(workshop_id__in=owned_workshops),
                status='confirmed',
                booking_date__gte=today,
                booking_date__lte=next_week,
            )
            .select_related('workshop', 'service', 'customer')
            .order_by('booking_date', 'booking_time')
        )
        serializer = ServiceBookingListSerializer(qs, many=True, context={'request': request})
        return Response({'count': qs.count(), 'results': serializer.data})

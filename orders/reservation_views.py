"""
Reservation views (Phase M3).
"""
import uuid

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Reservation, ImportOrder, ImportTimeline
from .serializers import (
    CreateReservationSerializer,
    ReservationDetailSerializer,
    ReservationListSerializer,
)


import logging

_logger = logging.getLogger(__name__)


class ReservationPagination(PageNumberPagination):
    page_size = 20


# ---------------------------------------------------------------------------
# Email helpers (sync with try/except — never blocks the action)
# ---------------------------------------------------------------------------

def _safe_send(template_name, to_email, subject, context):
    """Send a templated email, never raising."""
    try:
        from notifications.emails import send_templated_email
        send_templated_email(to_email=to_email, subject=subject,
                             template_name=template_name, context=context)
    except Exception as exc:
        _logger.error("Email '%s' to %s failed: %s", template_name, to_email, exc)


def _send_buyer_reservation_email(reservation):
    """Gap #5: Buyer receives confirmation after placing a reservation."""
    car = reservation.car
    importer_profile = getattr(reservation.importer, 'importer_profile', None)
    importer_name = importer_profile.business_name if importer_profile else reservation.importer.name
    _safe_send(
        'reservation_buyer_confirmation',
        reservation.buyer.email,
        f'تم حجز سيارتك · {reservation.reservation_number}',
        {
            'name': reservation.buyer.name or reservation.buyer.email,
            'car_title': car.title,
            'importer_name': importer_name,
            'reservation_number': reservation.reservation_number,
        },
    )


def _send_buyer_accepted_email(reservation, order):
    """Gap #7: Buyer notified when importer accepts reservation → order created."""
    car = reservation.car
    importer_profile = getattr(reservation.importer, 'importer_profile', None)
    importer_name = importer_profile.business_name if importer_profile else reservation.importer.name
    _safe_send(
        'reservation_accepted',
        reservation.buyer.email,
        f'تم قبول حجزك · {order.order_number}',
        {
            'name': reservation.buyer.name or reservation.buyer.email,
            'car_title': car.title,
            'importer_name': importer_name,
            'order_number': order.order_number,
            'order_id': order.pk,
        },
    )


def _send_buyer_rejected_email(reservation, reason=''):
    """Gap #8: Buyer notified when importer rejects reservation."""
    _safe_send(
        'reservation_rejected',
        reservation.buyer.email,
        f'تحديث على حجزك · {reservation.reservation_number}',
        {
            'name': reservation.buyer.name or reservation.buyer.email,
            'car_title': reservation.car.title,
            'reason': reason,
        },
    )


def _create_reservation_conversation(reservation):
    """Auto-create a conversation and system message when a car is reserved."""
    try:
        from messaging.models import Conversation, Message
        from django.contrib.auth import get_user_model
        User = get_user_model()

        conv, created = Conversation.objects.get_or_create(
            listing=reservation.car,
            buyer=reservation.buyer,
            seller=reservation.importer,
            defaults={'reservation': reservation},
        )
        # Get or create a system user for system messages
        system_user, _ = User.objects.get_or_create(
            email='system@wared.sa',
            defaults={'name': 'مَركَبة', 'role': 'admin', 'is_active': True, 'is_email_verified': True},
        )
        msg = Message.objects.create(
            conversation=conv,
            sender=system_user,
            content='تم حجز هذه السيارة. يمكنك الآن التواصل مباشرة.',
            message_type='system',
            is_system=True,
        )
        conv.update_last_message(msg)
    except Exception:
        pass  # Don't block reservation flow


def _post_system_message(reservation, content):
    """Post a system message to the reservation's conversation."""
    try:
        from messaging.models import Conversation, Message
        from django.contrib.auth import get_user_model
        User = get_user_model()

        conv = Conversation.objects.filter(
            listing=reservation.car,
            buyer=reservation.buyer,
            seller=reservation.importer,
        ).first()
        if not conv:
            return
        system_user = User.objects.filter(email='system@wared.sa').first()
        if not system_user:
            return
        msg = Message.objects.create(
            conversation=conv,
            sender=system_user,
            content=content,
            message_type='system',
            is_system=True,
        )
        conv.update_last_message(msg)
    except Exception:
        pass


def _notify_importer(reservation):
    """Notify importer about a new reservation via notification + email."""
    try:
        from notifications.utils import notify
        notify(
            recipient=reservation.importer,
            notification_type='system',
            title='حجز جديد على سيارتك',
            message=(
                f'قام {reservation.buyer.name} بحجز {reservation.car.title} '
                f'(رقم الحجز: {reservation.reservation_number}). '
                f'تواصل مع المشتري لاستكمال البيع.'
            ),
        )
    except Exception:
        pass

    # Email notification (CELERY_TASK_ALWAYS_EAGER handles sync execution in dev)
    try:
        _send_reservation_email_sync(reservation)
    except Exception:
        pass

    reservation.importer_notified_at = timezone.now()
    reservation.save(update_fields=['importer_notified_at'])


def _send_reservation_email_sync(reservation):
    """Send reservation notification email synchronously."""
    try:
        from notifications.emails import send_templated_email
        send_templated_email(
            to_email=reservation.importer.email,
            subject=f'حجز جديد — {reservation.reservation_number}',
            template_name='reservation_notification',
            context={
                'importer_name': reservation.importer.name,
                'buyer_name': reservation.buyer.name,
                'buyer_phone': reservation.buyer.phone or '',
                'car_title': reservation.car.title,
                'reservation_number': reservation.reservation_number,
            },
        )
    except Exception:
        pass


def _mock_payment(reservation):
    """Simulate payment processing. Returns True if payment "succeeded"."""
    reservation.payment_reference = f'MOCK-PAY-{uuid.uuid4().hex[:12]}'
    reservation.payment_status = 'succeeded'
    reservation.paid_at = timezone.now()
    reservation.save(update_fields=[
        'payment_reference', 'payment_status', 'paid_at', 'updated_at',
    ])
    return True


# ---------------------------------------------------------------------------
# POST /api/reservations/ — Create a reservation
# ---------------------------------------------------------------------------

class ReservationCreateView(APIView):
    """
    POST /api/reservations/
    Creates a reservation, processes mock payment, locks the car.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = CreateReservationSerializer(
            data=request.data, context={'request': request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        car = data['_car']
        fee = getattr(settings, 'PLATFORM_DEPOSIT_AMOUNT_SAR', 99)

        reservation = Reservation.objects.create(
            car=car,
            buyer=request.user,
            importer=car.owner,
            status='pending_payment',
            platform_fee_sar=fee,
            payment_method=data.get('payment_method', 'mada'),
            buyer_notes=data.get('buyer_notes', ''),
        )

        # Email buyer confirmation (never blocks reservation)
        try:
            _send_buyer_reservation_email(reservation)
        except Exception:
            pass

        out = ReservationDetailSerializer(reservation, context={'request': request})
        return Response(out.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# GET /api/reservations/ — List reservations
# ---------------------------------------------------------------------------

class ReservationListView(APIView):
    """
    GET /api/reservations/
    Buyers see their own; importers see reservations on their cars.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.db.models import Q
        user = request.user
        role = getattr(user, 'role', '')

        if user.is_staff or role == 'admin':
            qs = Reservation.objects.select_related('car', 'buyer', 'importer').all()
        else:
            # Show reservations where user is buyer OR importer
            # (an importer can also be a buyer on another importer's car)
            qs = Reservation.objects.select_related('car', 'buyer', 'importer').filter(
                Q(buyer=user) | Q(importer=user)
            )

        # Filter by status
        status_filter = request.query_params.get('status')
        if status_filter and status_filter != 'all':
            qs = qs.filter(status=status_filter)

        paginator = ReservationPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ReservationListSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)


# ---------------------------------------------------------------------------
# GET /api/reservations/{id}/ — Reservation detail
# ---------------------------------------------------------------------------

class ReservationDetailView(APIView):
    """
    GET /api/reservations/{id}/
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            res = Reservation.objects.select_related('car', 'buyer', 'importer').get(pk=pk)
        except Reservation.DoesNotExist:
            return Response({'detail': 'Reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        if not (user.is_staff or getattr(user, 'role', '') == 'admin'
                or res.buyer_id == user.id or res.importer_id == user.id):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ReservationDetailSerializer(res, context={'request': request})
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# POST /api/reservations/{id}/cancel/ — Cancel a reservation
# ---------------------------------------------------------------------------

class ReservationCancelView(APIView):
    """
    POST /api/reservations/{id}/cancel/
    Buyer cancels their own reservation. 99 SAR is non-refundable.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            res = Reservation.objects.select_related('car', 'buyer', 'importer').get(pk=pk)
        except Reservation.DoesNotExist:
            return Response({'detail': 'Reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        is_buyer = res.buyer_id == user.id
        is_importer = res.importer_id == user.id
        is_admin = user.is_staff or getattr(user, 'role', '') == 'admin'

        if not (is_buyer or is_importer or is_admin):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        if res.status not in ('active', 'pending_payment', 'pending_review'):
            return Response(
                {'detail': f'Cannot cancel a reservation with status "{res.get_status_display()}".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get('reason', '')
        by = 'buyer' if is_buyer else 'importer'
        res.cancel(by=by, reason=reason)
        _post_system_message(res, 'تم إلغاء الحجز.')

        # Notify the other party
        try:
            from notifications.utils import notify
            recipient = res.importer if is_buyer else res.buyer
            notify(
                recipient=recipient,
                notification_type='system',
                title='تم إلغاء الحجز',
                message=f'تم إلغاء الحجز {res.reservation_number}. السبب: {reason or "غير محدد"}',
            )
        except Exception:
            pass

        return Response({
            'detail': 'Reservation cancelled successfully.',
            'refund_amount': 0,
            'reservation': ReservationDetailSerializer(res, context={'request': request}).data,
        })


# ---------------------------------------------------------------------------
# POST /api/reservations/{id}/convert-to-order/ — Convert to ImportOrder
# ---------------------------------------------------------------------------

class ReservationConvertView(APIView):
    """
    POST /api/reservations/{id}/convert-to-order/
    Importer accepts the reservation and creates an ImportOrder.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            res = Reservation.objects.select_related('car', 'buyer', 'importer').get(pk=pk)
        except Reservation.DoesNotExist:
            return Response({'detail': 'Reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        is_importer = res.importer_id == user.id
        is_admin = user.is_staff or getattr(user, 'role', '') == 'admin'

        if not (is_importer or is_admin):
            return Response(
                {'detail': 'Only the importer can convert a reservation to an order.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        if res.status not in ('active', 'pending_review'):
            return Response(
                {'detail': f'Cannot convert a reservation with status "{res.get_status_display()}".'},
                status=status.HTTP_409_CONFLICT,
            )

        # Create ImportOrder
        car = res.car
        price = car.final_price_sar or car.price or 0
        order = ImportOrder.objects.create(
            car=car,
            buyer=res.buyer,
            importer=res.importer,
            total_price=price,
            remaining_balance=price,
            status='confirmed',
            buyer_notes=res.buyer_notes,
        )

        # First timeline event
        ImportTimeline.objects.create(
            order=order,
            event_type='order_confirmed',
            title='تم تأكيد الطلب من الحجز',
            description=f'تم تحويل الحجز {res.reservation_number} إلى طلب.',
            date=timezone.now(),
            created_by=user,
            is_public=True,
        )

        # Update reservation
        res.convert_to_order(order)
        _post_system_message(res, f'تم تأكيد الطلب. رقم الطلب: {order.order_number}')

        # Notify buyer (in-app + email)
        try:
            from notifications.utils import notify
            notify(
                recipient=res.buyer,
                notification_type='system',
                title='تم تأكيد طلبك',
                message=f'قبل المستورد حجزك وتم إنشاء طلب جديد #{order.order_number}.',
            )
        except Exception:
            pass
        _send_buyer_accepted_email(res, order)

        from .serializers import ImportOrderDetailSerializer
        return Response({
            'detail': 'Reservation converted to order successfully.',
            'order': ImportOrderDetailSerializer(order, context={'request': request}).data,
        }, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# POST /api/reservations/{id}/accept/ — Importer accepts
# ---------------------------------------------------------------------------

class ReservationAcceptView(APIView):
    """Importer accepts a pending_review reservation → creates ImportOrder."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            res = Reservation.objects.select_related('car', 'buyer', 'importer').get(pk=pk)
        except Reservation.DoesNotExist:
            return Response({'detail': 'Reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        if res.importer_id != request.user.id and not (request.user.is_staff or getattr(request.user, 'role', '') == 'admin'):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        if res.status != 'pending_review':
            return Response(
                {'detail': f'Cannot accept a reservation with status "{res.get_status_display()}".'},
                status=status.HTTP_409_CONFLICT,
            )

        car = res.car
        price = car.final_price_sar or car.price or 0
        order = ImportOrder.objects.create(
            car=car, buyer=res.buyer, importer=res.importer,
            total_price=price, remaining_balance=price,
            status='confirmed', buyer_notes=res.buyer_notes,
        )
        ImportTimeline.objects.create(
            order=order, event_type='order_confirmed',
            title='تم تأكيد الطلب من الحجز',
            description=f'تم قبول الحجز {res.reservation_number}.',
            date=timezone.now(), created_by=request.user, is_public=True,
        )
        res.convert_to_order(order)
        _post_system_message(res, f'تم قبول حجزك. رقم الطلب: {order.order_number}')

        try:
            from notifications.utils import notify, notify_admins
            notify(recipient=res.buyer, notification_type='system',
                   title='تم قبول حجزك!',
                   message=f'قبل المستورد حجزك على {car.title}. رقم الطلب: {order.order_number}')
            # WARED admins must always know when a deal starts
            notify_admins(
                title=f'Reservation accepted → order {order.order_number}',
                message=(f'{request.user.name} accepted {res.buyer.name}\'s reservation '
                         f'on "{car.title}". Order {order.order_number} created — '
                         'awaiting the buyer\'s full payment.'),
                listing=car,
            )
        except Exception:
            pass
        _send_buyer_accepted_email(res, order)

        from .serializers import ImportOrderDetailSerializer
        return Response({
            'detail': 'Reservation accepted successfully.',
            'order': ImportOrderDetailSerializer(order, context={'request': request}).data,
            'reservation': ReservationDetailSerializer(res, context={'request': request}).data,
        }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST /api/reservations/{id}/reject/ — Importer rejects
# ---------------------------------------------------------------------------

class ReservationRejectView(APIView):
    """Importer rejects a pending_review reservation."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            res = Reservation.objects.select_related('car', 'buyer', 'importer').get(pk=pk)
        except Reservation.DoesNotExist:
            return Response({'detail': 'Reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        if res.importer_id != request.user.id and not (request.user.is_staff or getattr(request.user, 'role', '') == 'admin'):
            return Response({'detail': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

        if res.status != 'pending_review':
            return Response(
                {'detail': f'Cannot reject a reservation with status "{res.get_status_display()}".'},
                status=status.HTTP_409_CONFLICT,
            )

        reason = request.data.get('reason', '')
        res.cancel(by='importer', reason=reason)
        _post_system_message(res, 'تم إلغاء الحجز من قبل المستورد.')

        try:
            from notifications.utils import notify, notify_admins
            notify(recipient=res.buyer, notification_type='system',
                   title='تم إلغاء الحجز',
                   message=f'تم إلغاء حجزك على {res.car.title}. يمكنك تصفح سيارات أخرى.')
            # WARED admins must always know when an importer rejects a buyer
            notify_admins(
                title=f'Reservation rejected — {res.reservation_number}',
                message=(f'{request.user.name} rejected {res.buyer.name}\'s reservation '
                         f'on "{res.car.title}".'
                         + (f' Reason: {reason}' if reason else ' No reason given.')),
                listing=res.car,
            )
        except Exception:
            pass
        _send_buyer_rejected_email(res, reason)

        return Response({
            'detail': 'Reservation rejected.',
            'reservation': ReservationDetailSerializer(res, context={'request': request}).data,
        })


# ---------------------------------------------------------------------------
# GET /api/reservations/pending-for-me/ — Importer's pending reservations
# ---------------------------------------------------------------------------

class ReservationPendingForMeView(APIView):
    """GET /api/reservations/pending-for-me/ — reservations awaiting importer decision."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        if getattr(user, 'role', '') not in ('importer', 'admin') and not user.is_staff:
            return Response({'detail': 'Importers only.'}, status=status.HTTP_403_FORBIDDEN)

        qs = Reservation.objects.filter(
            importer=user, status='pending_review',
        ).select_related('car', 'buyer').order_by('created_at')

        now = timezone.now()
        results = []
        for res in qs:
            expiry_date = res.created_at + timezone.timedelta(days=Reservation.RESERVATION_EXPIRY_DAYS)
            remaining_hours = max(0, (expiry_date - now).total_seconds() / 3600)
            results.append({
                'id': res.id,
                'reservation_number': res.reservation_number,
                'car': {
                    'id': res.car.id,
                    'title': res.car.title,
                    'make': res.car.make,
                    'model': res.car.model,
                    'year': res.car.year,
                    'final_price_sar': str(res.car.final_price_sar or res.car.price or 0),
                },
                'buyer': {
                    'id': res.buyer.id,
                    'name': res.buyer.name,
                    # phone intentionally omitted — contact exchange is only
                    # allowed after full payment (use in-app chat until then)
                    'member_since': res.buyer.date_joined.isoformat(),
                },
                'platform_fee_sar': str(res.platform_fee_sar),
                'paid_at': res.paid_at.isoformat() if res.paid_at else None,
                'created_at': res.created_at.isoformat(),
                'time_remaining_hours': round(remaining_hours, 1),
            })

        return Response({'count': len(results), 'results': results})

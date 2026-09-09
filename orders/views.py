"""
Views for the Import Order & Tracking system (Phase C).
"""
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ImportOrder, ImportTimeline, OrderDocument
from .serializers import (
    CreateOrderSerializer,
    CreateTimelineEventSerializer,
    ImportOrderDetailSerializer,
    ImportOrderListSerializer,
    UpdateOrderStatusSerializer,
    UploadDocumentSerializer,
    OrderDocumentSerializer,
    ImportTimelineSerializer,
)

# ---------------------------------------------------------------------------
# Map status → (event_type, default_title)
# ---------------------------------------------------------------------------
STATUS_TO_EVENT = {
    'deposit_requested':  ('deposit_requested',   'Deposit requested by importer'),
    'deposit_paid':       ('deposit_paid',         'Deposit payment confirmed'),
    'confirmed':          ('order_confirmed',      'Order confirmed by importer'),
    'sourcing':           ('sourcing_started',     'Sourcing car from auction'),
    'purchased':          ('car_purchased',        'Car purchased from source'),
    'preparing_shipment': ('preparing_shipment',   'Preparing car for shipment'),
    'shipped':            ('shipped',              'Car has been shipped'),
    'arrived_port':       ('arrived_port',         'Car arrived at Saudi port'),
    'in_customs':         ('customs_started',      'Customs processing started'),
    'customs_cleared':    ('customs_cleared',      'Customs clearance complete'),
    'inspection':         ('inspection_scheduled', 'Vehicle inspection scheduled'),
    'ready':              ('ready_for_pickup',     'Car is ready for delivery'),
    'delivered':          ('delivered',            'Car delivered to buyer'),
    'completed':          ('completed',            'Order completed'),
    'cancelled':          ('cancelled',            'Order cancelled'),
    'refunded':           ('refunded',             'Deposit refunded to buyer'),
    'disputed':           ('disputed',             'Dispute raised'),
}


def _is_importer_or_admin(user):
    if not user or not user.is_authenticated:
        return False
    return user.is_staff or getattr(user, 'role', '') in ('admin', 'importer')


# Map order statuses → car import_status for public badge accuracy
_ORDER_TO_IMPORT_STATUS = {
    'sourcing':           'sourcing',
    'purchased':          'purchased',
    'preparing_shipment': 'preparing',
    'shipped':            'shipping',
    'arrived_port':       'at_port',
    'in_customs':         'in_customs',
    'customs_cleared':    'customs_cleared',
    'inspection':         'inspection',
    'ready':              'ready_for_delivery',
    'delivered':          'delivered',
    'completed':          'completed',
}


def _sync_car_import_status(order, new_order_status):
    """Keep the car's import_status in sync with order progress.

    On cancellation/refund the car returns to the market: import_status back
    to 'available' and, if it was auto-marked Sold at payment confirmation,
    approval status back to 'approved' (so it reappears in My Listings and
    Browse)."""
    try:
        car = order.car
        if not car:
            return
        if new_order_status in ('cancelled', 'refunded'):
            update_fields = []
            if car.import_status != 'available':
                car.import_status = 'available'
                update_fields.append('import_status')
            if car.status == 'sold':
                car.status = 'approved'
                update_fields.append('status')
            if update_fields:
                car.save(update_fields=update_fields)
            return
        import_status = _ORDER_TO_IMPORT_STATUS.get(new_order_status)
        if import_status:
            car.import_status = import_status
            car.save(update_fields=['import_status'])
    except Exception:
        pass  # Never block order update


class OrderPagination(PageNumberPagination):
    page_size = 20


# ---------------------------------------------------------------------------
# OrderListCreateView
# ---------------------------------------------------------------------------

class OrderListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        role = getattr(user, 'role', '')

        if user.is_staff or role == 'admin':
            qs = ImportOrder.objects.select_related('car', 'buyer', 'importer').all()
        elif role == 'importer':
            qs = ImportOrder.objects.select_related(
                'car', 'buyer', 'importer'
            ).filter(importer=user)
        else:
            qs = ImportOrder.objects.select_related(
                'car', 'buyer', 'importer'
            ).filter(buyer=user)

        paginator = OrderPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = ImportOrderListSerializer(
            page, many=True, context={'request': request}
        )
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = CreateOrderSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            order = serializer.save()
            # v3: notify importer of new order
            try:
                from notifications.tasks import send_order_created_email
                send_order_created_email.delay(order.id)
            except Exception:
                pass
            out = ImportOrderDetailSerializer(order, context={'request': request})
            return Response(out.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# OrderDetailView
# ---------------------------------------------------------------------------

class OrderDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_order(self, pk, user):
        try:
            o = ImportOrder.objects.select_related('car', 'buyer', 'importer').get(pk=pk)
        except ImportOrder.DoesNotExist:
            return None
        if user.is_staff or getattr(user, 'role', '') == 'admin':
            return o
        if o.buyer_id == user.id or o.importer_id == user.id:
            return o
        return None

    def get(self, request, pk):
        order = self._get_order(pk, request.user)
        if order is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = ImportOrderDetailSerializer(order, context={'request': request})
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# OrderUpdateStatusView
# ---------------------------------------------------------------------------

class OrderUpdateStatusView(APIView):
    """PATCH /api/orders/{id}/update-status/ — importer or admin only."""
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        user = request.user
        if not _is_importer_or_admin(user):
            return Response(
                {'detail': 'Only importers or admins can update order status.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            order = ImportOrder.objects.select_related('car', 'buyer', 'importer').get(pk=pk)
        except ImportOrder.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Check the user is the importer on this order (or admin/staff)
        if not (user.is_staff or getattr(user, 'role', '') == 'admin'):
            if order.importer_id != user.id:
                return Response(
                    {'detail': 'You are not the importer for this order.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        serializer = UpdateOrderStatusSerializer(
            data=request.data, context={'order': order}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data       = serializer.validated_data
        new_status = data['status']
        old_status = order.status

        # Apply status change
        order.status = new_status
        if data.get('estimated_delivery_date'):
            order.estimated_delivery_date = data['estimated_delivery_date']
        if new_status == 'cancelled':
            order.cancellation_reason = data.get('cancellation_reason', '')
            order.cancelled_by        = user
            order.cancelled_at        = timezone.now()
        if new_status == 'deposit_paid':
            order.deposit_paid    = True
            order.deposit_paid_at = timezone.now()
        order.save()

        # Auto-create timeline event
        event_info = STATUS_TO_EVENT.get(new_status)
        if event_info:
            event_type, default_title = event_info
            ImportTimeline.objects.create(
                order=order,
                event_type=event_type,
                title=data.get('notes') or default_title,
                description=data.get('notes', ''),
                date=timezone.now(),
                created_by=user,
                is_public=True,
            )

        # Sync car's import_status to match order progress
        _sync_car_import_status(order, new_status)

        # Notify buyer
        try:
            from notifications.utils import notify, notify_admins
            notify(
                recipient=order.buyer,
                notification_type='system',
                title=f'Order {order.order_number} Updated',
                message=f'Your order status is now: {order.get_status_display()}',
            )
            # Admins must know when a deal is cancelled/refunded
            if new_status in ('cancelled', 'refunded') and getattr(user, 'role', '') != 'admin':
                notify_admins(
                    title=f'Order {new_status} — {order.order_number}',
                    message=(f'{user.name} set order {order.order_number} '
                             f'("{order.car.title if order.car else ""}") to {new_status}.'
                             + (f' Reason: {order.cancellation_reason}' if order.cancellation_reason else '')),
                    listing=order.car,
                )
        except Exception:
            pass

        out = ImportOrderDetailSerializer(order, context={'request': request})
        return Response(out.data)


# ---------------------------------------------------------------------------
# OrderCancelView
# ---------------------------------------------------------------------------

class OrderCancelView(APIView):
    """POST /api/orders/{id}/cancel/ — buyer, importer, or admin."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        user = request.user
        try:
            order = ImportOrder.objects.select_related('car', 'buyer', 'importer').get(pk=pk)
        except ImportOrder.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Access check: buyer, importer, or admin
        is_admin = user.is_staff or getattr(user, 'role', '') == 'admin'
        if not is_admin and order.buyer_id != user.id and order.importer_id != user.id:
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        cancellation_reason = request.data.get('cancellation_reason', '')
        if not cancellation_reason:
            return Response(
                {'cancellation_reason': 'Cancellation reason is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate transition
        try:
            order.validate_status_transition('cancelled')
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        order.status              = 'cancelled'
        order.cancellation_reason = cancellation_reason
        order.cancelled_by        = user
        order.cancelled_at        = timezone.now()
        order.save()

        # Release car back to the market if no other active orders
        # (import_status → available; auto-Sold status reverts to approved)
        active_orders = ImportOrder.objects.filter(
            car=order.car
        ).exclude(status__in=['cancelled', 'refunded', 'completed']).exclude(pk=order.pk)
        if not active_orders.exists():
            _sync_car_import_status(order, 'cancelled')

        ImportTimeline.objects.create(
            order=order,
            event_type='cancelled',
            title='Order cancelled',
            description=cancellation_reason,
            date=timezone.now(),
            created_by=user,
            is_public=True,
        )

        try:
            from notifications.utils import notify, notify_admins
            notify(
                recipient=order.buyer,
                notification_type='system',
                title=f'Order {order.order_number} Cancelled',
                message=f'Your order has been cancelled. Reason: {cancellation_reason}',
            )
            if getattr(user, 'role', '') != 'admin':
                notify_admins(
                    title=f'Order cancelled — {order.order_number}',
                    message=(f'{user.name} cancelled order {order.order_number} '
                             f'("{order.car.title if order.car else ""}"). '
                             f'Reason: {cancellation_reason}'),
                    listing=order.car,
                )
        except Exception:
            pass

        out = ImportOrderDetailSerializer(order, context={'request': request})
        return Response(out.data)


# ---------------------------------------------------------------------------
# OrderTimelineListCreateView
# ---------------------------------------------------------------------------

class OrderTimelineListCreateView(APIView):
    """GET + POST /api/orders/{id}/timeline/"""
    permission_classes = [permissions.IsAuthenticated]

    def _get_order(self, pk, user):
        try:
            order = ImportOrder.objects.get(pk=pk)
        except ImportOrder.DoesNotExist:
            return None
        is_admin = user.is_staff or getattr(user, 'role', '') == 'admin'
        if is_admin:
            return order
        if order.buyer_id == user.id or order.importer_id == user.id:
            return order
        return None

    def get(self, request, pk):
        order = self._get_order(pk, request.user)
        if order is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        is_priv = _is_importer_or_admin(request.user)
        qs = order.timeline_events.all()
        if not is_priv:
            qs = qs.filter(is_public=True)
        serializer = ImportTimelineSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request, pk):
        order = self._get_order(pk, request.user)
        if order is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not _is_importer_or_admin(request.user):
            return Response(
                {'detail': 'Only importers or admins can add timeline events.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = CreateTimelineEventSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        event = ImportTimeline.objects.create(
            order=order,
            event_type=data['event_type'],
            title=data['title'],
            description=data.get('description', ''),
            date=data['date'],
            created_by=request.user,
            is_public=data.get('is_public', True),
        )
        return Response(ImportTimelineSerializer(event).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# OrderDocumentListCreateView
# ---------------------------------------------------------------------------

class OrderDocumentListCreateView(APIView):
    """GET + POST /api/orders/{id}/documents/"""
    permission_classes = [permissions.IsAuthenticated]

    def _get_order(self, pk, user):
        try:
            order = ImportOrder.objects.get(pk=pk)
        except ImportOrder.DoesNotExist:
            return None
        is_admin = user.is_staff or getattr(user, 'role', '') == 'admin'
        if is_admin:
            return order
        if order.buyer_id == user.id or order.importer_id == user.id:
            return order
        return None

    def get(self, request, pk):
        order = self._get_order(pk, request.user)
        if order is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        qs = order.documents.all()
        if not _is_importer_or_admin(request.user):
            qs = qs.filter(is_buyer_visible=True)
        serializer = OrderDocumentSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request, pk):
        order = self._get_order(pk, request.user)
        if order is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not _is_importer_or_admin(request.user):
            return Response(
                {'detail': 'Only importers or admins can upload documents.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = UploadDocumentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        doc = OrderDocument.objects.create(
            order=order,
            document_type=data['document_type'],
            title=data['title'],
            file=data['file'],
            uploaded_by=request.user,
            is_buyer_visible=data.get('is_buyer_visible', True),
            notes=data.get('notes', ''),
        )
        # Add a timeline event
        ImportTimeline.objects.create(
            order=order,
            event_type='document_uploaded',
            title=f'Document uploaded: {doc.title}',
            description=f'Type: {doc.document_type}',
            date=timezone.now(),
            created_by=request.user,
            is_public=data.get('is_buyer_visible', True),
        )
        return Response(OrderDocumentSerializer(doc).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Balance payment — buyer pays the full car price to WARED by bank transfer,
# WARED staff verify receipt, then the importer is cleared to deliver.
# WARED keeps settings.PLATFORM_COMMISSION_PCT %, the importer gets the rest.
# ---------------------------------------------------------------------------

class OrderPayBalanceView(APIView):
    """
    POST /api/orders/{id}/pay-balance/  — buyer submits their bank-transfer
    reference. Creates a pending PaymentTransaction that WARED verifies.
    Body: { "reference": "<bank transfer reference number>" }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            order = ImportOrder.objects.select_related('buyer', 'importer', 'car').get(pk=pk)
        except ImportOrder.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if order.buyer_id != request.user.id:
            return Response({'detail': 'Only the buyer can pay for this order.'},
                            status=status.HTTP_403_FORBIDDEN)

        if order.status in ('cancelled', 'refunded', 'completed'):
            return Response({'detail': 'This order can no longer be paid.'},
                            status=status.HTTP_400_BAD_REQUEST)

        reference = (request.data.get('reference') or '').strip()
        if not reference:
            return Response({'reference': ['Bank transfer reference is required.']},
                            status=status.HTTP_400_BAD_REQUEST)

        from payments.models import PaymentTransaction

        # Already fully paid?
        if PaymentTransaction.objects.filter(
            order=order, payment_type='balance', status='succeeded',
        ).exists():
            return Response({'detail': 'This order is already paid.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # A submission is already under review — update the reference instead
        existing = PaymentTransaction.objects.filter(
            order=order, payment_type='balance', status='pending',
        ).first()
        amount = order.remaining_balance or order.total_price or 0
        if existing:
            existing.provider_transaction_id = reference
            existing.save(update_fields=['provider_transaction_id', 'updated_at'])
            txn = existing
        else:
            txn = PaymentTransaction.objects.create(
                order=order,
                user=request.user,
                amount=amount,
                payment_type='balance',
                method='bank_transfer',
                provider='bank_transfer',
                provider_transaction_id=reference,
                status='pending',
            )
            ImportTimeline.objects.create(
                order=order,
                event_type='payment',
                title='Payment submitted — under review by WARED',
                description=f'Bank transfer reference: {reference}',
                date=timezone.now(),
                created_by=request.user,
                is_public=True,
            )
            # Notify the importer that payment is on its way
            try:
                from notifications.utils import notify
                notify(
                    recipient=order.importer,
                    notification_type='system',
                    title=f'Payment submitted for {order.order_number}',
                    message='The buyer submitted a bank transfer. WARED is verifying it.',
                )
            except Exception:
                pass

        return Response({
            'detail': 'Transfer reference received. WARED will verify the payment shortly.',
            'payment': {
                'status': txn.status,
                'amount': float(txn.amount),
                'reference': txn.provider_transaction_id,
            },
        }, status=status.HTTP_201_CREATED)


class OrderConfirmPaymentView(APIView):
    """
    POST /api/orders/{id}/confirm-payment/ — WARED admin confirms the bank
    transfer arrived. Marks the balance transaction as succeeded, zeroes the
    remaining balance, and notifies both parties.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if not (request.user.is_staff or getattr(request.user, 'role', '') == 'admin'):
            return Response({'detail': 'Only WARED admins can confirm payments.'},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            order = ImportOrder.objects.select_related('buyer', 'importer').get(pk=pk)
        except ImportOrder.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        from django.conf import settings as dj_settings
        from payments.models import PaymentTransaction

        txn = PaymentTransaction.objects.filter(
            order=order, payment_type='balance', status='pending',
        ).order_by('-created_at').first()
        if not txn:
            return Response({'detail': 'No pending balance payment on this order.'},
                            status=status.HTTP_400_BAD_REQUEST)

        txn.status = 'succeeded'
        txn.save(update_fields=['status', 'updated_at'])
        order.remaining_balance = 0
        order.save(update_fields=['remaining_balance'])

        # Full payment received → the car is SOLD, automatically.
        # (Importers can never mark a car sold manually — this is the only
        # path. If the order is later cancelled/refunded the car reverts.)
        car = order.car
        if car and car.status != 'sold':
            car.status = 'sold'
            car.save(update_fields=['status'])

        commission_pct = float(getattr(dj_settings, 'PLATFORM_COMMISSION_PCT', 1.0))
        importer_share = float(txn.amount) * (100.0 - commission_pct) / 100.0

        ImportTimeline.objects.create(
            order=order,
            event_type='payment',
            title='Payment confirmed by WARED',
            description='Full payment received. The importer can proceed with delivery.',
            date=timezone.now(),
            created_by=request.user,
            is_public=True,
        )
        try:
            from notifications.utils import notify, notify_admins
            notify(
                recipient=order.importer,
                notification_type='system',
                title=f'Payment confirmed — {order.order_number}',
                message=f'WARED received the full payment. Your payout: SAR {importer_share:,.0f} ({100 - commission_pct:.0f}%). The car is now marked SOLD. Proceed with delivery.',
            )
            notify(
                recipient=order.buyer,
                notification_type='system',
                title=f'Payment confirmed — {order.order_number}',
                message='WARED confirmed your payment. The importer will now arrange delivery.',
            )
            notify_admins(
                title=f'Car sold — {order.order_number}',
                message=(f'Full payment (SAR {float(txn.amount):,.0f}) confirmed for '
                         f'"{order.car.title if order.car else ""}". The car is now marked SOLD. '
                         f'Importer payout: SAR {importer_share:,.0f}.'),
                listing=order.car,
            )
        except Exception:
            pass

        return Response({
            'detail': 'Payment confirmed.',
            'importer_payout_sar': round(importer_share, 2),
            'commission_pct': commission_pct,
        })


class AdminPendingPaymentsView(APIView):
    """
    GET /api/admin/payments/?status=pending — WARED's payment verification
    queue. Each row is a balance PaymentTransaction with its order context so
    the admin can match it against the bank account and confirm or reject.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not (request.user.is_staff or getattr(request.user, 'role', '') == 'admin'):
            return Response({'detail': 'Admins only.'}, status=status.HTTP_403_FORBIDDEN)

        from payments.models import PaymentTransaction

        status_param = request.query_params.get('status', 'pending')
        qs = PaymentTransaction.objects.filter(
            payment_type__in=('balance', 'promotion'),
        ).select_related(
            'order', 'order__car', 'order__buyer', 'order__importer', 'user',
            'promotion', 'promotion__package', 'promotion__listing',
        ).order_by('-created_at')
        if status_param != 'all':
            qs = qs.filter(status=status_param)

        results = []
        for txn in qs[:100]:
            if txn.payment_type == 'promotion':
                p = txn.promotion
                results.append({
                    'id': txn.id,
                    'type': 'promotion',
                    'order_id': None,
                    'order_number': (p.package.name if p and p.package else 'Promotion'),
                    'car_title': (p.listing.title if p and p.listing else ''),
                    'buyer_name': txn.user.name if txn.user else '',
                    'importer_name': txn.user.name if txn.user else '',
                    'amount': float(txn.amount),
                    'reference': txn.provider_transaction_id,
                    'status': txn.status,
                    'submitted_at': txn.created_at.isoformat(),
                })
                continue
            o = txn.order
            results.append({
                'id': txn.id,
                'type': 'balance',
                'order_id': o.id if o else None,
                'order_number': o.order_number if o else '',
                'car_title': (o.car.title if o and o.car else ''),
                'buyer_name': (o.buyer.name if o and o.buyer else ''),
                'importer_name': (o.importer.name if o and o.importer else ''),
                'amount': float(txn.amount),
                'reference': txn.provider_transaction_id,
                'status': txn.status,
                'submitted_at': txn.created_at.isoformat(),
            })
        return Response({'count': len(results), 'results': results})


class OrderRejectPaymentView(APIView):
    """
    POST /api/orders/{id}/reject-payment/ — WARED admin could not match the
    transfer. Marks the pending balance transaction failed and tells the buyer
    to double-check the reference / re-submit.
    Body: { "reason": "..." } (optional)
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if not (request.user.is_staff or getattr(request.user, 'role', '') == 'admin'):
            return Response({'detail': 'Only WARED admins can reject payments.'},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            order = ImportOrder.objects.select_related('buyer').get(pk=pk)
        except ImportOrder.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        from payments.models import PaymentTransaction
        txn = PaymentTransaction.objects.filter(
            order=order, payment_type='balance', status='pending',
        ).order_by('-created_at').first()
        if not txn:
            return Response({'detail': 'No pending balance payment on this order.'},
                            status=status.HTTP_400_BAD_REQUEST)

        reason = (request.data.get('reason') or 'Transfer could not be verified.').strip()
        txn.status = 'failed'
        txn.error_message = reason
        txn.save(update_fields=['status', 'error_message', 'updated_at'])

        ImportTimeline.objects.create(
            order=order,
            event_type='payment',
            title='Payment could not be verified',
            description=f'{reason} Please check the transfer reference and submit it again.',
            date=timezone.now(),
            created_by=request.user,
            is_public=True,
        )
        try:
            from notifications.utils import notify
            notify(
                recipient=order.buyer,
                notification_type='system',
                title=f'Payment issue — {order.order_number}',
                message=f'{reason} Please re-check your bank transfer reference and submit it again on the order page.',
            )
        except Exception:
            pass

        return Response({'detail': 'Payment rejected — the buyer has been asked to re-submit.'})

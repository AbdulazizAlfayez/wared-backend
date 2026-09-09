from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import Reservation
from .models import PaymentTransaction
from .providers import get_payment_provider


VALID_METHODS = {'mada', 'visa', 'mastercard', 'apple_pay', 'stc_pay'}


class ReservationPayView(APIView):
    """
    POST /api/reservations/{id}/pay/
    Process payment for a pending reservation.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            reservation = Reservation.objects.select_related('car', 'buyer', 'importer').get(pk=pk)
        except Reservation.DoesNotExist:
            return Response({'error': 'Reservation not found.'}, status=status.HTTP_404_NOT_FOUND)

        if reservation.buyer_id != request.user.id:
            return Response({'error': 'Not your reservation.'}, status=status.HTTP_403_FORBIDDEN)

        if reservation.payment_status == 'succeeded':
            return Response({'error': 'Already paid.'}, status=status.HTTP_400_BAD_REQUEST)

        method = request.data.get('method', '')
        if method not in VALID_METHODS:
            return Response(
                {'error': f'Invalid payment method. Choose from: {", ".join(sorted(VALID_METHODS))}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create pending transaction
        txn = PaymentTransaction.objects.create(
            reservation=reservation,
            user=request.user,
            amount=reservation.platform_fee_sar,
            payment_type='deposit',
            method=method,
            status='pending',
        )

        # Charge via provider
        provider = get_payment_provider()
        result = provider.charge(
            amount=reservation.platform_fee_sar,
            method=method,
            metadata={
                'reservation_id': reservation.id,
                'reservation_number': reservation.reservation_number,
                'buyer_id': request.user.id,
            },
        )

        txn.provider = result.provider
        txn.provider_transaction_id = result.transaction_id

        if result.success:
            txn.status = 'succeeded'
            txn.save()

            # Activate reservation
            reservation.payment_method = method
            reservation.payment_reference = result.transaction_id
            reservation.payment_status = 'succeeded'
            reservation.paid_at = timezone.now()
            reservation.status = 'pending_review'
            reservation.save()

            # Mark car as reserved
            car = reservation.car
            if hasattr(car, 'is_reserved'):
                car.is_reserved = True
                car.import_status = 'reserved'
                car.save(update_fields=['is_reserved', 'import_status'])

            # Notify importer
            try:
                from notifications.utils import notify
                notify(
                    recipient=reservation.importer,
                    notification_type='system',
                    title='New reservation paid',
                    message=f'{request.user.name} paid SAR {reservation.platform_fee_sar} to reserve {car.make} {car.model}.',
                )
            except Exception:
                pass

            return Response({
                'success': True,
                'transaction_id': txn.provider_transaction_id,
                'reservation_status': reservation.status,
            })
        else:
            txn.status = 'failed'
            txn.error_message = result.error_message
            txn.save()

            return Response({
                'success': False,
                'error': result.error_message or 'Payment failed. Please try again.',
            }, status=status.HTTP_400_BAD_REQUEST)


class BankDetailsView(APIView):
    """GET /api/payments/bank-details/ — WARED's bank account for manual
    transfers (car balance + promotion payments). Authenticated users only."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.conf import settings as dj_settings
        return Response({
            'bank_name':   getattr(dj_settings, 'WARED_BANK_NAME', ''),
            'beneficiary': getattr(dj_settings, 'WARED_BANK_BENEFICIARY', ''),
            'iban':        getattr(dj_settings, 'WARED_BANK_IBAN', ''),
        })

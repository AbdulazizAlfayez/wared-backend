"""
Management command to expire old pending_review reservations.

Usage:
    python manage.py expire_reservations

Also configured as a Celery Beat task (runs every hour in production).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from orders.models import Reservation


class Command(BaseCommand):
    help = 'Expire reservations in pending_review status older than 7 days'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timezone.timedelta(days=Reservation.RESERVATION_EXPIRY_DAYS)
        expired_qs = Reservation.objects.filter(
            status='pending_review',
            created_at__lt=cutoff,
        ).select_related('car', 'buyer', 'importer')

        count = 0
        for res in expired_qs:
            res.expire()
            count += 1

            # Post system message
            try:
                from orders.reservation_views import _post_system_message
                _post_system_message(res, 'انتهت صلاحية الحجز.')
            except Exception:
                pass

            # Notify both parties
            try:
                from notifications.utils import notify
                notify(
                    recipient=res.buyer,
                    notification_type='system',
                    title='انتهت صلاحية الحجز',
                    message=f'لم يستجب المستورد لحجزك على {res.car.title} خلال 7 أيام. يمكنك حجز سيارة أخرى.',
                )
                notify(
                    recipient=res.importer,
                    notification_type='system',
                    title='فاتك حجز',
                    message=f'انتهت صلاحية حجز {res.buyer.name} على {res.car.title} لعدم الاستجابة.',
                )
            except Exception:
                pass

            self.stdout.write(f'  Expired: {res.reservation_number} (car: {res.car.title})')

        self.stdout.write(self.style.SUCCESS(f'Expired {count} reservation(s).'))

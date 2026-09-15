"""
Management command to expire old pending_review reservations.

Usage:
    python manage.py expire_reservations

The logic lives in orders.tasks.expire_due_reservations, which the hourly
Celery Beat task 'orders.tasks.expire_reservations' also calls — this command
is a thin wrapper so the two can never diverge.
"""
from django.core.management.base import BaseCommand

from orders.tasks import expire_due_reservations


class Command(BaseCommand):
    help = 'Expire reservations in pending_review status older than 7 days'

    def handle(self, *args, **options):
        expired = expire_due_reservations(stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(f'Expired {len(expired)} reservation(s).'))

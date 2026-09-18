"""
Re-derive every listing's reservation lock from the reservation and order rows.

Locks a car whose newest reservation is paid (pending_review) or converted to
a still-live order, and releases a car whose reservations have all ended and
that has no live order. Safe to run repeatedly — it writes only what differs,
and it goes through orders.locks, so it cannot invent a rule of its own.

    python manage.py repair_reservation_flags [--dry-run]
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from cars.models import Listing
from cars.visibility import ACTIVE_ORDER_STATUSES, PAID_RESERVATION_STATUSES
from orders.locks import lock_listing, release_listing
from orders.models import ImportOrder, Reservation

HOLDING_STATUSES = PAID_RESERVATION_STATUSES + ('converted_to_order',)


def holding_reservation(car_id):
    """The reservation that should hold this car right now, or None."""
    for res in (
        Reservation.objects
        .filter(car_id=car_id, status__in=HOLDING_STATUSES)
        .select_related('converted_order')
        .order_by('-created_at')
    ):
        if res.status in PAID_RESERVATION_STATUSES:
            return res
        order = res.converted_order
        if order is not None and order.status in ACTIVE_ORDER_STATUSES:
            return res
    return None


def repair(dry_run=False, stdout=None):
    """Returns (locked, released) lists of listing ids that needed a change."""
    locked, released = [], []

    candidate_ids = set(
        Reservation.objects.values_list('car_id', flat=True)
    ) | set(
        Listing.objects.filter(is_reserved=True).values_list('pk', flat=True)
    ) | set(
        ImportOrder.objects.values_list('car_id', flat=True)
    )

    for car_id in sorted(i for i in candidate_ids if i):
        car = Listing.objects.filter(pk=car_id).first()
        if car is None:
            continue
        res = holding_reservation(car_id)

        if res is not None:
            wants_reserved_status = res.status in PAID_RESERVATION_STATUSES
            in_shape = (
                car.is_reserved
                and car.current_reservation_id == res.pk
                and (car.import_status == 'reserved' or not wants_reserved_status)
            )
            if in_shape:
                continue
            locked.append(car_id)
            if dry_run:
                continue
            if wants_reserved_status:
                lock_listing(res)
            else:
                # Converted to a live order: the order's progress owns
                # import_status, so only the lock fields are restored.
                with transaction.atomic():
                    car.is_reserved = True
                    car.current_reservation = res
                    car.save(update_fields=['is_reserved', 'current_reservation'])
            continue

        live_order = ImportOrder.objects.filter(
            car_id=car_id, status__in=ACTIVE_ORDER_STATUSES,
        ).exists()
        if live_order:
            continue
        if not (car.is_reserved or car.current_reservation_id or car.import_status == 'reserved'):
            continue
        released.append(car_id)
        if not dry_run:
            release_listing(car)

    if stdout is not None:
        stdout.write(f'locked:   {locked or "none"}')
        stdout.write(f'released: {released or "none"}')
    return locked, released


class Command(BaseCommand):
    help = "Re-derive listing reservation locks from reservations and orders."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **options):
        locked, released = repair(dry_run=options['dry_run'], stdout=self.stdout)
        verb = 'would change' if options['dry_run'] else 'changed'
        self.stdout.write(self.style.SUCCESS(
            f'{verb}: {len(locked)} locked, {len(released)} released'
        ))

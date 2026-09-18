"""
The only place that writes a listing's reservation lock.

Three fields decide whether a car is on the market: `is_reserved`,
`current_reservation` and `import_status`. cars.visibility.public_market_q()
reads all three (plus the reservation and order rows), so they must always be
written together — by these functions and nowhere else. Every caller that ends
or begins a deal (Reservation.activate/cancel/expire, reject, accept, order
create, order cancel/refund, the repair command, the data migration) goes
through `lock_listing` / `lock_listing_for_order` / `release_listing`.

Each function takes the car's row FOR UPDATE inside a transaction, so two
buyers acting at once cannot interleave.
"""
from django.db import transaction


def _locked_listing(listing_id):
    from cars.models import Listing
    return Listing.objects.select_for_update().get(pk=listing_id)


def lock_listing(reservation):
    """
    Take the car off the market for *reservation* (a paid reservation).

    Remembers the car's previous import_status on the reservation so
    release_listing can put it back exactly as it was.
    """
    with transaction.atomic():
        car = _locked_listing(reservation.car_id)
        if reservation.car_import_status_before != (car.import_status or ''):
            reservation.car_import_status_before = car.import_status or ''
            reservation.save(update_fields=['car_import_status_before', 'updated_at'])

        car.is_reserved = True
        car.current_reservation = reservation
        car.import_status = 'reserved'
        car.save(update_fields=['is_reserved', 'current_reservation', 'import_status'])
        reservation.car = car
        return car


def lock_listing_for_order(order):
    """
    An order placed straight on a car, with no reservation behind it
    (POST /api/orders/). There is no reservation to point at — the order row
    itself is what hides the car — so only import_status moves.
    """
    with transaction.atomic():
        car = _locked_listing(order.car_id)
        if car.import_status != 'reserved':
            car.import_status = 'reserved'
            car.save(update_fields=['import_status'])
        order.car = car
        return car


def release_listing(listing, *, reservation=None, order=None):
    """
    Put *listing* back on the market.

    Refuses in two cases, so a release can never steal a live lock:
      - another live deal (an order that is not cancelled/refunded) still
        holds the car, excluding *order* itself;
      - a reservation other than *reservation* currently holds the lock.

    Restores the pre-reservation import_status when one was recorded, else
    'available', and reverts an auto-Sold car to 'approved'.
    """
    from cars.visibility import ACTIVE_ORDER_STATUSES, PAID_RESERVATION_STATUSES
    from orders.models import ImportOrder, Reservation

    with transaction.atomic():
        car = _locked_listing(listing.pk)

        live_orders = ImportOrder.objects.filter(
            car_id=car.pk, status__in=ACTIVE_ORDER_STATUSES,
        )
        if order is not None:
            live_orders = live_orders.exclude(pk=order.pk)
        if live_orders.exists():
            return car

        holder_id = car.current_reservation_id
        if holder_id and (reservation is None or holder_id != reservation.pk):
            holder = Reservation.objects.filter(pk=holder_id).first()
            if holder is not None and holder.status in PAID_RESERVATION_STATUSES:
                return car

        previous = ''
        if reservation is not None:
            previous = reservation.car_import_status_before or ''
        elif order is not None:
            source = (
                order.source_reservation.exclude(car_import_status_before='')
                .order_by('-created_at').first()
            )
            previous = source.car_import_status_before if source else ''

        update_fields = ['is_reserved', 'current_reservation', 'import_status']
        car.is_reserved = False
        car.current_reservation = None
        if reservation is not None and car.import_status != 'reserved':
            # Someone moved the car on (e.g. order progress); leave that alone.
            update_fields.remove('import_status')
        else:
            car.import_status = previous or 'available'
        if car.status == 'sold':
            car.status = 'approved'
            update_fields.append('status')
        car.save(update_fields=update_fields)
        return car

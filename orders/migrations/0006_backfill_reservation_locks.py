"""
Backfill the car lock for reservations paid before pay/ went through
Reservation.activate().

That code set is_reserved (sometimes) but never current_reservation, so the
reserving buyer got reservation_state=null. For every car whose newest paid
(pending_review/active) reservation — or whose reservation became a still
live order — is not reflected on the car, set is_reserved and
current_reservation. import_status is set to 'reserved' only for
pending_review reservations, remembering the previous value so release can
restore it; a converted car's import_status tracks its order and is left alone.
"""
from django.db import migrations

PAID = ('pending_review', 'active')
ACTIVE_ORDER = (
    'pending', 'deposit_requested', 'deposit_paid', 'confirmed',
    'sourcing', 'purchased', 'preparing_shipment', 'shipped',
    'arrived_port', 'in_customs', 'customs_cleared', 'inspection',
    'ready', 'delivered', 'completed',
)


def backfill(apps, schema_editor):
    Reservation = apps.get_model('orders', 'Reservation')
    Listing = apps.get_model('cars', 'Listing')

    candidates = (
        Reservation.objects
        .filter(status__in=PAID + ('converted_to_order',))
        .order_by('car_id', '-created_at')
    )
    seen = set()
    for res in candidates:
        if res.car_id in seen:
            continue
        if res.status == 'converted_to_order' and not (
            res.converted_order_id
            and res.converted_order.status in ACTIVE_ORDER
        ):
            continue
        seen.add(res.car_id)

        car = Listing.objects.get(pk=res.car_id)
        fields = []
        if not car.is_reserved:
            car.is_reserved = True
            fields.append('is_reserved')
        if car.current_reservation_id is None:
            car.current_reservation_id = res.pk
            fields.append('current_reservation')
        if res.status in PAID and car.import_status != 'reserved':
            if not res.car_import_status_before:
                res.car_import_status_before = car.import_status or ''
                res.save(update_fields=['car_import_status_before'])
            car.import_status = 'reserved'
            fields.append('import_status')
        if fields:
            car.save(update_fields=fields)


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0005_reservation_car_import_status_before'),
        ('cars', '0022_add_changes_requested_status'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]

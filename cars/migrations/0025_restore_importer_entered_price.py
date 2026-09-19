"""
Puts the advertised price back under the importer's control.

Phase 4 (0023/0024) made the price a formula: landed cost + margin. This
restores the number each listing carried before that migration ran and writes
it to BOTH `final_price_sar` and `price`, so the two cannot disagree.

0024 preserved the advertised figure by deriving `margin_sar` from it
(margin = advertised - landed), so `landed + margin` reconstructs exactly what
was there. Listings 0024 skipped — no cost lines, or a source price in a
currency with no stored rate — never had their price rewritten and keep what
they have.

Anything that actually moves is logged with its before and after.
"""
import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations

CENTS = Decimal('0.01')
logger = logging.getLogger(__name__)


def _money(value):
    if value is None:
        return None
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def restore(apps, schema_editor):
    Listing = apps.get_model('cars', 'Listing')

    restored = 0
    moved = []

    for listing in Listing.objects.all().iterator():
        landed = _money(listing.total_landed_cost)
        margin = _money(listing.margin_sar)
        final = _money(listing.final_price_sar)
        price = _money(listing.price)

        if margin is not None and landed is not None:
            # The pre-Phase-4 price, reconstructed from what 0024 stored.
            target = (landed + margin).quantize(CENTS, rounding=ROUND_HALF_UP)
        else:
            target = final or price

        if target is None:
            logger.warning('Listing %s has no price at all; left untouched.', listing.pk)
            continue

        if final != target or price != target:
            moved.append((listing.pk, price, final, target))

        listing.final_price_sar = target
        listing.price = target
        listing.save(update_fields=['final_price_sar', 'price'])
        restored += 1

    logger.info('Restored importer-entered pricing on %s listings.', restored)
    for pk, was_price, was_final, now in moved:
        logger.warning(
            'Listing %s: price %s -> %s, final_price_sar %s -> %s.',
            pk, was_price, now, was_final, now,
        )


def noop(apps, schema_editor):
    """Nothing to undo: this restores the values 0024 replaced."""


class Migration(migrations.Migration):
    dependencies = [('cars', '0024_recompute_listing_pricing')]

    operations = [migrations.RunPython(restore, noop)]

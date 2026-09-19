"""
Back-fills the pricing the server now owns.

Every existing listing had `total_landed_cost` and `final_price_sar` written by
a client, and the two clients disagreed about what those meant. This recomputes
the landed cost from the same inputs the server will use from now on, and
derives `margin_sar` from the price the listing already carried — so no car's
advertised price moves. A listing whose landed cost shifts by more than 1% is
logged, because that is a client that had been storing a figure the server
would not have produced.
"""
import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations

CENTS = Decimal('0.01')
logger = logging.getLogger(__name__)

SAR_COST_FIELDS = (
    'shipping_cost',
    'customs_duty_amount',
    'vat_amount',
    'inspection_fee',
    'transportation_cost',
)


def recompute(apps, schema_editor):
    Listing = apps.get_model('cars', 'Listing')
    ExchangeRate = apps.get_model('calculator', 'ExchangeRate')

    rates = {
        row.currency.lower(): row
        for row in ExchangeRate.objects.all()
    }

    changed = 0
    drifted = []
    missing_rate = []

    for listing in Listing.objects.all().iterator():
        costs = [getattr(listing, field) for field in SAR_COST_FIELDS]
        source_price = listing.source_price

        if source_price is None and not any(cost is not None for cost in costs):
            continue

        cost_total = sum((cost for cost in costs if cost is not None), Decimal('0.00'))

        source_in_sar = Decimal('0.00')
        fx_rate = None
        fx_date = None
        if source_price:
            rate_row = rates.get((listing.source_currency or '').lower())
            if rate_row is None:
                # Left untouched rather than recomputed without the car's price.
                missing_rate.append((listing.pk, listing.source_currency))
                continue
            fx_rate = rate_row.rate_to_sar
            fx_date = rate_row.updated_at
            source_in_sar = (source_price * fx_rate).quantize(CENTS, rounding=ROUND_HALF_UP)

        landed = (source_in_sar + cost_total).quantize(CENTS, rounding=ROUND_HALF_UP)

        previous = listing.total_landed_cost
        if previous and previous != 0:
            drift = abs(landed - previous) / previous
            if drift > Decimal('0.01'):
                drifted.append((listing.pk, previous, landed, drift * 100))

        # The price the listing already advertises is the one that must not
        # move, so the margin is whatever reconciles it with the new cost.
        advertised = listing.final_price_sar or listing.price
        margin = (advertised - landed).quantize(CENTS, rounding=ROUND_HALF_UP) if advertised else Decimal('0.00')

        listing.total_landed_cost = landed
        listing.margin_sar = margin
        listing.final_price_sar = (landed + margin).quantize(CENTS, rounding=ROUND_HALF_UP)
        listing.fx_rate_used = fx_rate
        listing.fx_rate_date = fx_date
        listing.save(update_fields=[
            'total_landed_cost', 'margin_sar', 'final_price_sar',
            'fx_rate_used', 'fx_rate_date',
        ])
        changed += 1

    logger.info('Recomputed pricing for %s listings.', changed)
    for pk, currency in missing_rate:
        logger.warning('Listing %s left untouched: no exchange rate for %r.', pk, currency)
    for pk, before, after, pct in drifted:
        logger.warning(
            'Listing %s landed cost moved %.1f%%: %s -> %s (the stored figure was '
            'not what the server would have computed).', pk, pct, before, after,
        )


def noop(apps, schema_editor):
    """Nothing to undo: the old values were client-supplied and unreproducible."""


class Migration(migrations.Migration):
    dependencies = [
        ('cars', '0023_listing_computed_pricing'),
        ('calculator', '0003_exchangerate_last_api_attempt_at_and_more'),
    ]

    operations = [migrations.RunPython(recompute, noop)]

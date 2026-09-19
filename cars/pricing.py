"""
What a listing costs to land. Not what it sells for.

The price is the importer's to set: they know the car, the buyer and the
market, and a server-side formula (landed cost + margin) produced numbers they
had not agreed to. `final_price_sar` is written by the importer and `price`
mirrors it — see `cars/serializers.py`.

What stays here is `total_landed_cost`: the cost lines added up in SAR, with
the source price converted at the calculator's stored rate. It is
informational — it feeds the buyer's cost breakdown — and it never moves the
price. A missing exchange rate leaves it null rather than blocking the save:
an informational total is not worth refusing a listing over.
"""
from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal('0.01')

#: The cost components already denominated in SAR.
SAR_COST_FIELDS = (
    'shipping_cost',
    'customs_duty_amount',
    'vat_amount',
    'inspection_fee',
    'transportation_cost',
)


def _money(value):
    """A Decimal rounded to halalas, or None."""
    if value is None:
        return None
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def exchange_rate_for(currency):
    """The stored rate row for a currency, or None."""
    if not currency:
        return None
    from calculator.models import ExchangeRate

    return ExchangeRate.objects.filter(currency=str(currency).lower()).first()


def compute_landed_cost(listing):
    """
    The listing's total landed cost in SAR, or None.

    None means "cannot say": no cost lines at all, or a source price in a
    currency with no stored rate. Returning a partial total would understate
    the car by its entire purchase price, and a cost breakdown that reads low
    is worse than one that is absent.

    The conversion happens BEFORE the SAR costs are added. Getting that order
    wrong is not a rounding error — 75,000,000 KRW added raw to a few thousand
    riyals reads as a SAR 75 million car.
    """
    source_price = _money(listing.source_price)
    costs = [_money(getattr(listing, field, None)) for field in SAR_COST_FIELDS]
    has_costs = any(cost is not None for cost in costs)

    if source_price is None and not has_costs:
        return None

    cost_total = sum((cost for cost in costs if cost is not None), Decimal('0.00'))

    source_in_sar = Decimal('0.00')
    if source_price:
        rate_row = exchange_rate_for(getattr(listing, 'source_currency', None))
        if rate_row is None:
            return None
        source_in_sar = (source_price * rate_row.rate_to_sar).quantize(
            CENTS, rounding=ROUND_HALF_UP,
        )

    return (source_in_sar + cost_total).quantize(CENTS, rounding=ROUND_HALF_UP)


def apply_landed_cost(listing):
    """
    Write the landed cost onto the instance, in memory. Returns it, or None.

    Deliberately touches nothing else: `price` and `final_price_sar` belong to
    the importer.
    """
    landed = compute_landed_cost(listing)
    listing.total_landed_cost = landed
    return landed

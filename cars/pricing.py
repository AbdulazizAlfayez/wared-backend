"""
What a listing costs to land, and what it sells for.

Before this module both numbers were whatever the client sent. Two clients
computed them independently — the website summed the SAR costs and silently
left out the car itself, the mobile app converted the source price first — so
two listings with identical inputs could carry different totals, and nothing
ever reconciled them.

Now the server owns the arithmetic. The client sends what it knows (the source
price, the costs it paid, the margin it wants) and the server derives the rest
at save time, stamping the exchange rate it used so the figure can still be
explained months later when the rate has moved.
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


class MissingExchangeRate(Exception):
    """No stored rate for the listing's source currency."""

    def __init__(self, currency):
        self.currency = currency
        super().__init__(
            f"No exchange rate is stored for '{currency}'. "
            f"Ask an administrator to add one, or price the car in a currency that has one."
        )


def _money(value):
    """A Decimal rounded to halalas, or None."""
    if value is None:
        return None
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def exchange_rate_for(currency):
    """
    The stored rate row for a currency, or None.

    The row rather than the bare number, so the caller can stamp
    `fx_rate_date` from the same object it took the rate from.
    """
    if not currency:
        return None
    from calculator.models import ExchangeRate

    return ExchangeRate.objects.filter(currency=str(currency).lower()).first()


def compute_pricing(listing):
    """
    Work out a listing's landed cost and asking price.

    Returns a dict of fields to write, or an empty dict when the listing
    carries no import pricing at all — a plain local car has no landed cost,
    and inventing a zero for it would be worse than leaving the column null.

    Raises `MissingExchangeRate` when there is a source price in a currency we
    cannot convert. Refusing the save is the only honest option: storing the
    SAR costs alone would understate the car by its entire purchase price, and
    that understated figure is exactly what this module exists to stop.

    The conversion happens BEFORE the SAR costs are added. Getting that order
    wrong is not a rounding error — 75,000,000 KRW added raw to a few thousand
    riyals reads as a SAR 75 million car.
    """
    source_price = _money(listing.source_price)
    costs = [_money(getattr(listing, field)) for field in SAR_COST_FIELDS]
    has_costs = any(cost is not None for cost in costs)

    if source_price is None and not has_costs:
        return {}

    cost_total = sum((cost for cost in costs if cost is not None), Decimal('0.00'))

    fx_rate = None
    fx_date = None
    source_in_sar = Decimal('0.00')

    if source_price:
        rate_row = exchange_rate_for(listing.source_currency)
        if rate_row is None:
            raise MissingExchangeRate(listing.source_currency or '')
        fx_rate = rate_row.rate_to_sar
        fx_date = rate_row.updated_at
        source_in_sar = (source_price * fx_rate).quantize(CENTS, rounding=ROUND_HALF_UP)

    total_landed_cost = (source_in_sar + cost_total).quantize(CENTS, rounding=ROUND_HALF_UP)
    margin = _money(listing.margin_sar) or Decimal('0.00')
    final_price = (total_landed_cost + margin).quantize(CENTS, rounding=ROUND_HALF_UP)

    return {
        'total_landed_cost': total_landed_cost,
        'final_price_sar': final_price,
        'fx_rate_used': fx_rate,
        'fx_rate_date': fx_date,
    }


def apply_pricing(listing):
    """
    Writes the computed pricing onto the instance, in memory.

    `price` follows `final_price_sar` because the marketplace card reads
    `price` and the two drifting apart is how a car ends up advertised at one
    number and sold at another.
    """
    computed = compute_pricing(listing)
    if not computed:
        return {}
    for field, value in computed.items():
        setattr(listing, field, value)
    listing.price = computed['final_price_sar']
    return computed

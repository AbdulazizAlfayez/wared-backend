from datetime import date
from decimal import Decimal

from .models import ExchangeRate

SHIPPING_ESTIMATES = {
    'usa':    (4000, 8000),
    'japan':  (3500, 6500),
    'korea':  (3000, 6000),
    'uae':    (1000, 2500),
    'qatar':  (800,  2000),
    'europe': (5000, 9000),
    'canada': (4500, 8500),
    'other':  (3000, 8000),
}

INSPECTION_FEE     = Decimal('800')
PORT_HANDLING      = Decimal('500')
TRANSPORTATION     = Decimal('500')
CUSTOMS_DUTY_RATE  = Decimal('0.05')
VAT_RATE           = Decimal('0.15')


def calculate_import_cost(source_country, car_value, source_currency='usd',
                          year=None, engine_size_cc=None):
    """
    Returns a full import cost breakdown dict.
    All monetary values are Decimal, rounded to 2 dp.
    """
    rate_obj = ExchangeRate.objects.get(currency=source_currency.lower())
    rate     = rate_obj.rate_to_sar

    car_price_sar  = (Decimal(str(car_value)) * rate).quantize(Decimal('0.01'))
    customs_duty   = (car_price_sar * CUSTOMS_DUTY_RATE).quantize(Decimal('0.01'))
    vat_amount     = ((car_price_sar + customs_duty) * VAT_RATE).quantize(Decimal('0.01'))

    shipping_low, shipping_high = SHIPPING_ESTIMATES.get(
        source_country.lower(), SHIPPING_ESTIMATES['other']
    )
    shipping_low  = Decimal(shipping_low)
    shipping_high = Decimal(shipping_high)

    fixed = customs_duty + vat_amount + INSPECTION_FEE + PORT_HANDLING + TRANSPORTATION
    total_low  = (car_price_sar + fixed + shipping_low).quantize(Decimal('0.01'))
    total_high = (car_price_sar + fixed + shipping_high).quantize(Decimal('0.01'))

    age_warning = None
    if year is not None:
        current_year = date.today().year
        if current_year - int(year) > 5:
            age_warning = (
                "Cars older than 5 years may face import restrictions "
                "in Saudi Arabia"
            )

    return {
        'source_country':          source_country,
        'car_value':               Decimal(str(car_value)),
        'source_currency':         source_currency.lower(),
        'exchange_rate':           rate,
        'car_price_sar':           car_price_sar,
        'customs_duty_rate':       CUSTOMS_DUTY_RATE,
        'customs_duty_amount':     customs_duty,
        'vat_rate':                VAT_RATE,
        'vat_amount':              vat_amount,
        'shipping_estimate_low':   shipping_low,
        'shipping_estimate_high':  shipping_high,
        'inspection_fee':          INSPECTION_FEE,
        'port_handling_fee':       PORT_HANDLING,
        'transportation_estimate': TRANSPORTATION,
        'total_estimate_low':      total_low,
        'total_estimate_high':     total_high,
        'age_restriction_warning': age_warning,
        'breakdown': {
            'car_price':      {'label': 'Car price (converted)',          'amount': car_price_sar},
            'customs':        {'label': 'Customs duty (5%)',              'amount': customs_duty},
            'vat':            {'label': 'VAT (15%)',                      'amount': vat_amount},
            'shipping_low':   {'label': 'Shipping (estimate low)',        'amount': shipping_low},
            'shipping_high':  {'label': 'Shipping (estimate high)',       'amount': shipping_high},
            'inspection':     {'label': 'SASO Inspection',                'amount': INSPECTION_FEE},
            'port_handling':  {'label': 'Port handling',                  'amount': PORT_HANDLING},
            'transportation': {'label': 'Transportation (port to city)',  'amount': TRANSPORTATION},
        },
    }

from rest_framework import serializers
from .models import ExchangeRate

VALID_COUNTRIES = ['usa', 'uae', 'japan', 'korea', 'qatar', 'europe', 'canada', 'other']

DEFAULT_CURRENCY = {
    'usa':    'usd',
    'uae':    'aed',
    'japan':  'jpy',
    'korea':  'krw',
    'qatar':  'qar',
    'europe': 'eur',
    'canada': 'cad',
    'other':  'usd',
}


class ExchangeRateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ExchangeRate
        fields = ('currency', 'rate_to_sar', 'updated_at')


class CalculatorInputSerializer(serializers.Serializer):
    source_country  = serializers.ChoiceField(choices=VALID_COUNTRIES)
    car_value       = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=1)
    source_currency = serializers.CharField(max_length=5, required=False, allow_blank=True)
    year            = serializers.IntegerField(required=False, allow_null=True,
                                               min_value=1900, max_value=2100)
    engine_size_cc  = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate(self, data):
        # Fill default currency from country if not supplied
        if not data.get('source_currency'):
            data['source_currency'] = DEFAULT_CURRENCY.get(data['source_country'], 'usd')
        data['source_currency'] = data['source_currency'].lower()
        return data


class BreakdownItemSerializer(serializers.Serializer):
    label  = serializers.CharField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class CalculatorResultSerializer(serializers.Serializer):
    source_country          = serializers.CharField()
    car_value               = serializers.DecimalField(max_digits=14, decimal_places=2)
    source_currency         = serializers.CharField()
    exchange_rate           = serializers.DecimalField(max_digits=10, decimal_places=4)
    car_price_sar           = serializers.DecimalField(max_digits=14, decimal_places=2)
    customs_duty_rate       = serializers.DecimalField(max_digits=4,  decimal_places=2)
    customs_duty_amount     = serializers.DecimalField(max_digits=14, decimal_places=2)
    vat_rate                = serializers.DecimalField(max_digits=4,  decimal_places=2)
    vat_amount              = serializers.DecimalField(max_digits=14, decimal_places=2)
    shipping_estimate_low   = serializers.DecimalField(max_digits=14, decimal_places=2)
    shipping_estimate_high  = serializers.DecimalField(max_digits=14, decimal_places=2)
    inspection_fee          = serializers.DecimalField(max_digits=14, decimal_places=2)
    port_handling_fee       = serializers.DecimalField(max_digits=14, decimal_places=2)
    transportation_estimate = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_estimate_low      = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_estimate_high     = serializers.DecimalField(max_digits=14, decimal_places=2)
    age_restriction_warning = serializers.CharField(allow_null=True)
    breakdown               = serializers.DictField(child=BreakdownItemSerializer())

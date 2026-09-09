from django.db import migrations

RATES = [
    ('usd', '3.7500'),
    ('aed', '1.0200'),
    ('jpy', '0.0250'),
    ('krw', '0.0029'),
    ('eur', '4.1000'),
    ('cad', '2.7500'),
    ('gbp', '4.7500'),
    ('qar', '1.0300'),
]


def seed_rates(apps, schema_editor):
    ExchangeRate = apps.get_model('calculator', 'ExchangeRate')
    for currency, rate in RATES:
        ExchangeRate.objects.get_or_create(
            currency=currency,
            defaults={'rate_to_sar': rate, 'source': 'seed'},
        )


def unseed_rates(apps, schema_editor):
    ExchangeRate = apps.get_model('calculator', 'ExchangeRate')
    ExchangeRate.objects.filter(
        currency__in=[c for c, _ in RATES],
        source='seed',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('calculator', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_rates, unseed_rates),
    ]

from django.db import migrations


def seed_config(apps, schema_editor):
    AppConfig = apps.get_model('appconfig', 'AppConfig')
    AppConfig.objects.get_or_create(pk=1, defaults={
        'min_supported_version': '1.0.0',
        'latest_version': '1.0.0',
        'maintenance_mode': False,
        'maintenance_message': None,
        'features': {
            'ai_assistant': True,
            'reservations': True,
            'promotions': True,
            'reviews': True,
        },
    })


class Migration(migrations.Migration):
    dependencies = [
        ('appconfig', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_config, migrations.RunPython.noop),
    ]

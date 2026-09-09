# Generated manually on 2026-04-08

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('dealer_applications', '0001_initial'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='DealerApplication',
            new_name='ImporterApplication',
        ),
        # Update related_name on the applicant FK
        migrations.AlterField(
            model_name='importerapplication',
            name='applicant',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='importer_applications',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Update verbose_name
        migrations.AlterModelOptions(
            name='importerapplication',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Importer Application',
                'verbose_name_plural': 'Importer Applications',
            },
        ),
    ]

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('favorites', '0001_initial'),
        ('cars', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Remove old unique_together that references 'car'
        migrations.AlterUniqueTogether(
            name='favorite',
            unique_together=set(),
        ),
        # Remove old car index
        migrations.RemoveIndex(
            model_name='favorite',
            name='favorites_car_id_244dce_idx',
        ),
        # Remove old car FK
        migrations.RemoveField(model_name='favorite', name='car'),
        # Add new listing FK
        migrations.AddField(
            model_name='favorite',
            name='listing',
            field=models.ForeignKey(
                default=1,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='favorites',
                to='cars.listing',
            ),
            preserve_default=False,
        ),
        # Set new unique_together
        migrations.AlterUniqueTogether(
            name='favorite',
            unique_together={('user', 'listing')},
        ),
        # Add index on listing
        migrations.AddIndex(
            model_name='favorite',
            index=models.Index(fields=['listing'], name='favorites_listing_idx'),
        ),
    ]

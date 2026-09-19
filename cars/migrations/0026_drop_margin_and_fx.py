"""
Drops the columns the formula needed.

`margin_sar` was an input to the server-side price, which no longer exists.
`fx_rate_used` / `fx_rate_date` stamped the rate that price was computed at;
the landed cost still converts the source price, but it is informational and
recomputed on every save, so a stored rate would only go stale. Nothing reads
any of the three after 0025 — the price is the importer's number now.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('cars', '0025_restore_importer_entered_price')]

    operations = [
        migrations.RemoveField(model_name='listing', name='margin_sar'),
        migrations.RemoveField(model_name='listing', name='fx_rate_used'),
        migrations.RemoveField(model_name='listing', name='fx_rate_date'),
    ]

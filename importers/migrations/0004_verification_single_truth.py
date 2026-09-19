"""
`ImporterProfile.is_verified` becomes a property of `user.is_business_verified`.

Two booleans meant two answers to "is this importer verified". The values are
merged first — a profile ticked verified marks its user verified, never the
other way round, so no verification is lost — and only then is the column
dropped.
"""
from django.db import migrations


def merge_into_user(apps, schema_editor):
    ImporterProfile = apps.get_model('importers', 'ImporterProfile')
    User = apps.get_model('accounts', 'User')

    verified_user_ids = list(
        ImporterProfile.objects
        .filter(is_verified=True)
        .values_list('user_id', flat=True)
    )
    if verified_user_ids:
        User.objects.filter(pk__in=verified_user_ids).update(is_business_verified=True)


def back_to_profile(apps, schema_editor):
    ImporterProfile = apps.get_model('importers', 'ImporterProfile')
    ImporterProfile.objects.filter(user__is_business_verified=True).update(is_verified=True)


class Migration(migrations.Migration):

    dependencies = [
        ('importers', '0003_importerprofile_cr_document_and_more'),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(merge_into_user, back_to_profile),
        migrations.RemoveField(model_name='importerprofile', name='is_verified'),
    ]

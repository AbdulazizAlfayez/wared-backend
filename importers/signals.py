from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='accounts.User')
def create_importer_profile(sender, instance, created, **kwargs):
    """Auto-create ImporterProfile when user becomes an importer."""
    if getattr(instance, 'role', '') == 'importer':
        from .models import ImporterProfile
        ImporterProfile.objects.get_or_create(
            user=instance,
            defaults={'business_name': instance.name or instance.email.split('@')[0]},
        )

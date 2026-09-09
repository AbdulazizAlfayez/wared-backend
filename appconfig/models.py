from django.db import models


class AppConfig(models.Model):
    """Singleton configuration — only one row ever exists."""

    min_supported_version = models.CharField(max_length=20, default='1.0.0')
    latest_version = models.CharField(max_length=20, default='1.0.0')
    maintenance_mode = models.BooleanField(default=False)
    maintenance_message = models.TextField(blank=True, null=True)
    features = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'App Configuration'
        verbose_name_plural = 'App Configuration'

    def __str__(self):
        return f"App Config (v{self.latest_version})"

    def save(self, *args, **kwargs):
        # Enforce singleton: always use pk=1
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

from django.apps import AppConfig


class ImportersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'importers'

    def ready(self):
        import importers.signals  # noqa

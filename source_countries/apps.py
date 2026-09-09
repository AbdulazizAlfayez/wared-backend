from django.apps import AppConfig


class SourceCountriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "source_countries"
    verbose_name = "Source Countries"

    def ready(self):
        from .signals import register_signals

        register_signals()

from django.apps import AppConfig


class CarsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cars'

    def ready(self):
        # Registers the Listing post_save/post_delete hooks that bust the
        # filter-options cache. Idempotent — see `cars.signals`.
        from .signals import register_signals

        register_signals()




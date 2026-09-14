"""
Cache invalidation for the filter facets.

Follows `source_countries.signals`: registration is idempotent and the model is
imported lazily, so importing this module cannot pull `cars.models` in before
the app registry is ready.
"""

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save

from .filter_options import CACHE_KEY

_registered = False


def _bust_filter_options(sender, **kwargs):
    cache.delete(CACHE_KEY)


def register_signals():
    global _registered
    if _registered:
        return
    _registered = True

    # Lazy: this module is imported from AppConfig.ready().
    from .models import Listing

    post_save.connect(
        _bust_filter_options, sender=Listing, dispatch_uid='bust_filter_options_save'
    )
    post_delete.connect(
        _bust_filter_options, sender=Listing, dispatch_uid='bust_filter_options_delete'
    )

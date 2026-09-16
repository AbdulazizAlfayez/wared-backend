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


def _bust_market_caches(sender, **kwargs):
    """
    A reservation or order changing can take a car off (or back onto) the
    market without saving the Listing — e.g. an order created or cancelled
    on a car that was never reserved. Drop both shared market caches.
    """
    from source_countries.signals import CACHE_KEY_BY_COUNTRY
    cache.delete_many([CACHE_KEY, CACHE_KEY_BY_COUNTRY])


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

    for label in ('orders.Reservation', 'orders.ImportOrder'):
        for signal, kind in ((post_save, 'save'), (post_delete, 'delete')):
            signal.connect(
                _bust_market_caches, sender=label,
                dispatch_uid=f'bust_market_caches_{label}_{kind}',
            )

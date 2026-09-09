from django.core.cache import cache
from django.db.models.signals import post_save, post_delete

CACHE_KEY_BY_COUNTRY = "imported_cars:by_country:v1"

_registered = False


def _bust_cache(sender, **kwargs):
    cache.delete(CACHE_KEY_BY_COUNTRY)


def register_signals():
    global _registered
    if _registered:
        return
    _registered = True

    # Lazy import to avoid circular dependency
    from cars.models import Listing

    post_save.connect(_bust_cache, sender=Listing, dispatch_uid="bust_country_cache_save")
    post_delete.connect(_bust_cache, sender=Listing, dispatch_uid="bust_country_cache_delete")

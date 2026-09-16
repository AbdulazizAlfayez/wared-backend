from django.db.models import Q
from django.shortcuts import get_object_or_404

from cars.models import Listing
from cars.visibility import public_market_q


def get_public_listing_or_404(listing_id, user=None):
    """
    The listing, if *user* may see it at all (the public, or a party to a
    reserved car — its buyer, its importer, staff).

    Deliberately routed through `cars.visibility.public_market_q()` rather than
    a bare `status='approved'` check: that module is the single source of truth
    for what is on-market, and it also excludes reserved and sold cars and any
    car locked into a live deal. Commenting on those would leak the existence
    of a private transaction, which is the very thing that module prevents.
    """
    return get_object_or_404(Listing.objects.filter(public_market_q(user)), pk=listing_id)


def visible_comments_q():
    """Comments a non-staff reader may see."""
    return Q(is_deleted=False) & Q(is_hidden=False)

"""
What an importer's own listing has done: views, saves, likes, messages,
reservations.

One function so the listing serializer, `/api/listings/my/` and the importer
desk all report the same numbers. Every count is scoped to a single listing
and is only ever shown to that listing's owner or to staff — see
`ListingSerializer.to_representation`.
"""

#: The keys of `owner_stats`, in the order the importer's card reads them.
OWNER_STAT_KEYS = ('views', 'saves', 'likes', 'messages', 'reservations')


def owner_stats(listing):
    """
    The five counts for one listing.

    `views` is the denormalised counter on the listing (see `_track_view`),
    not a COUNT over the log: the log is trimmed and the counter is the number
    the importer has been watching go up.
    """
    from favorites.models import Favorite
    from messaging.models import Conversation
    from orders.models import Reservation
    from social.models import ListingLike

    return {
        'views': listing.view_count or 0,
        'saves': Favorite.objects.filter(listing=listing).count(),
        'likes': ListingLike.objects.filter(listing=listing).count(),
        'messages': Conversation.objects.filter(listing=listing).count(),
        'reservations': Reservation.objects.filter(car=listing).count(),
    }


def owner_stats_bulk(listings):
    """
    `owner_stats` for several listings in a fixed number of queries.

    The desk shows six cards at once; one-at-a-time would be thirty round
    trips. Returns {listing_id: stats}.
    """
    from django.db.models import Count

    from favorites.models import Favorite
    from messaging.models import Conversation
    from orders.models import Reservation
    from social.models import ListingLike

    ids = [listing.pk for listing in listings]
    if not ids:
        return {}

    def tally(queryset, field='listing'):
        return dict(
            queryset.filter(**{f'{field}__in': ids})
            .values_list(field)
            .annotate(n=Count('id'))
            .values_list(field, 'n')
        )

    saves = tally(Favorite.objects)
    likes = tally(ListingLike.objects)
    messages = tally(Conversation.objects)
    reservations = tally(Reservation.objects, field='car')

    return {
        listing.pk: {
            'views': listing.view_count or 0,
            'saves': saves.get(listing.pk, 0),
            'likes': likes.get(listing.pk, 0),
            'messages': messages.get(listing.pk, 0),
            'reservations': reservations.get(listing.pk, 0),
        }
        for listing in listings
    }

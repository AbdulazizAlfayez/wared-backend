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


#: The annotation names `annotate_owner_stats` adds, per stat.
ANNOTATION = {
    'saves': 'stats_saves',
    'likes': 'stats_likes',
    'messages': 'stats_messages',
    'reservations': 'stats_reservations',
}


def annotate_owner_stats(queryset):
    """
    The four counted stats, as scalar subqueries on the listing query itself.

    `owner_stats_bulk` can answer from these without asking the database
    anything further, which turns a page of twenty cards from five queries
    (or, unbatched, a hundred) into none beyond the page itself.

    Subqueries rather than `Count(..., distinct=True)` joins: four joins onto
    one row multiply each other, and the `distinct` that fixes the arithmetic
    makes the query slower than the four it replaced. A scalar subquery per
    relation stays independent, and the planner runs each once per row of the
    page — twenty, not twenty thousand.

    Also what makes `?sort=attention` possible: ordering by a Python dict is
    not something the database can paginate.
    """
    from django.db.models import Count, IntegerField, OuterRef, Subquery
    from django.db.models.functions import Coalesce

    from favorites.models import Favorite
    from messaging.models import Conversation
    from orders.models import Reservation
    from social.models import ListingLike

    def counted(model, field='listing'):
        rows = (
            model.objects
            .filter(**{field: OuterRef('pk')})
            .order_by()
            .values(field)
            .annotate(total=Count('id'))
            .values('total')[:1]
        )
        return Coalesce(Subquery(rows, output_field=IntegerField()), 0)

    return queryset.annotate(
        stats_saves=counted(Favorite),
        stats_likes=counted(ListingLike),
        stats_messages=counted(Conversation),
        stats_reservations=counted(Reservation, field='car'),
    )


#: The annotations `annotate_view_stats` adds, per window.
VIEW_ANNOTATION = {
    'views_today': 'views_today_total',
    'views_this_week': 'views_week_total',
    'views_this_month': 'views_month_total',
}


def annotate_view_stats(queryset, now=None):
    """
    Today's, this week's and this month's views, as subqueries.

    `ListingSerializer.view_stats` counts the view log three times per row.
    That is invisible on a detail page and three-score queries on a page of
    twenty cards, which is what `/api/listings/my/` renders.
    """
    from datetime import timedelta

    from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
    from django.db.models.functions import Coalesce
    from django.utils import timezone

    from .models import ViewLog

    now = now or timezone.now()
    windows = {
        'views_today_total': now.replace(hour=0, minute=0, second=0, microsecond=0),
        'views_week_total': now - timedelta(days=7),
        'views_month_total': now - timedelta(days=30),
    }

    def since(moment):
        rows = (
            ViewLog.objects
            .filter(listing=OuterRef('pk'), viewed_at__gte=moment)
            .order_by()
            .values('listing')
            .annotate(total=Count('id'))
            .values('total')[:1]
        )
        return Coalesce(Subquery(rows, output_field=IntegerField()), 0)

    return queryset.annotate(**{name: since(moment) for name, moment in windows.items()})


def view_stats_from_annotations(listing):
    """The three windows off an annotated row, or None when not annotated."""
    values = {}
    for key, name in VIEW_ANNOTATION.items():
        value = getattr(listing, name, None)
        if value is None:
            return None
        values[key] = value
    return values


def stats_from_annotations(listing):
    """The five counts off an annotated row, or None when it is not annotated."""
    values = {}
    for key, name in ANNOTATION.items():
        value = getattr(listing, name, None)
        if value is None:
            return None
        values[key] = value
    return {'views': listing.view_count or 0, **values}


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

    # Annotated by `annotate_owner_stats`: the counts are already on the rows,
    # so the four queries below are not needed at all.
    annotated = {}
    for listing in listings:
        stats = stats_from_annotations(listing)
        if stats is None:
            annotated = {}
            break
        annotated[listing.pk] = stats
    if annotated:
        return annotated

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

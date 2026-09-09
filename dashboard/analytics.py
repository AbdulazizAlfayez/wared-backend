"""
Phase 4.4 — Dealer Analytics: complex query logic.

All expensive functions cache their results for 5 minutes via Django's cache
framework (Redis in production, LocMem in dev/test).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import (
    Avg, Case, Count, DurationField, ExpressionWrapper,
    F, FloatField, Q, Sum, Value, When,
)
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone

User = get_user_model()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_rate(numerator, denominator) -> float:
    """Return (numerator / denominator) * 100, or 0.0 on division-by-zero."""
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 2)


def _fill_date_gaps(raw_dict: dict, days: int, value_key: str = 'count') -> list:
    """
    Return a list covering the last *days* calendar days.
    Dates missing from *raw_dict* are filled with 0.
    """
    today = timezone.now().date()
    return [
        {'date': str(today - timedelta(days=i)), value_key: raw_dict.get(today - timedelta(days=i), 0)}
        for i in range(days - 1, -1, -1)
    ]


def _primary_image_url(listing) -> str | None:
    """Return the URL of the primary image for a listing, or None."""
    images = list(listing.images.all())
    img = next((i for i in images if i.is_primary), None) or (images[0] if images else None)
    if img and img.image:
        try:
            return img.image.url
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# 1. Dealer overview analytics
# ---------------------------------------------------------------------------

def get_dealer_analytics_overview(user) -> dict:
    from cars.models import Listing, ViewLog
    from leads.models import Lead
    from favorites.models import Favorite
    from bookings.models import Appointment
    from messaging.models import Conversation

    cache_key = f'dealer_analytics_overview_{user.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    listing_qs  = Listing.objects.filter(owner=user, is_active=True)
    listing_ids = list(listing_qs.values_list('id', flat=True))

    agg = listing_qs.aggregate(
        total_views=Sum('view_count'),
        total_unique_views=Sum('unique_view_count'),
    )
    total_views        = agg['total_views'] or 0
    total_unique_views = agg['total_unique_views'] or 0

    total_leads     = Lead.objects.filter(listing_id__in=listing_ids).count()
    contacted_leads = Lead.objects.filter(listing_id__in=listing_ids, status='contacted').count()
    closed_leads    = Lead.objects.filter(listing_id__in=listing_ids, status='closed').count()
    total_favorites = Favorite.objects.filter(listing__owner=user).count()
    total_appts     = Appointment.objects.filter(listing_id__in=listing_ids).count()
    total_messages  = Conversation.objects.filter(listing_id__in=listing_ids).count()

    summary = {
        'total_listings':     listing_qs.count(),
        'active_listings':    listing_qs.filter(status='approved').count(),
        'sold_listings':      listing_qs.filter(status='sold').count(),
        'draft_listings':     listing_qs.filter(status='draft').count(),
        'total_views':        total_views,
        'total_unique_views': total_unique_views,
        'total_leads':        total_leads,
        'total_favorites':    total_favorites,
        'total_appointments': total_appts,
        'total_messages':     total_messages,
    }

    conversion_rates = {
        'view_to_lead':      _safe_rate(total_leads, total_views),
        'lead_to_contacted': _safe_rate(contacted_leads, total_leads),
        'lead_to_closed':    _safe_rate(closed_leads, total_leads),
        'view_to_favorite':  _safe_rate(total_favorites, total_views),
    }

    vlog_qs = ViewLog.objects.filter(listing_id__in=listing_ids)
    lead_qs = Lead.objects.filter(listing_id__in=listing_ids)

    def _view_trend(days: int) -> list:
        since = timezone.now() - timedelta(days=days)
        raw = (
            vlog_qs
            .filter(viewed_at__gte=since)
            .annotate(date=TruncDate('viewed_at'))
            .values('date')
            .annotate(views=Count('id'))
            .order_by('date')
        )
        return _fill_date_gaps({r['date']: r['views'] for r in raw}, days, value_key='views')

    def _lead_trend(days: int) -> list:
        since = timezone.now() - timedelta(days=days)
        raw = (
            lead_qs
            .filter(created_at__gte=since)
            .annotate(date=TruncDate('created_at'))
            .values('date')
            .annotate(leads=Count('id'))
            .order_by('date')
        )
        return _fill_date_gaps({r['date']: r['leads'] for r in raw}, days, value_key='leads')

    trends = {
        'views_last_7_days':  _view_trend(7),
        'views_last_30_days': _view_trend(30),
        'leads_last_7_days':  _lead_trend(7),
        'leads_last_30_days': _lead_trend(30),
    }

    views_by_source = list(
        vlog_qs.values('source').annotate(count=Count('id')).order_by('-count')
    )

    hour_dict = {
        r['hour']: r['views']
        for r in (
            vlog_qs
            .annotate(hour=ExtractHour('viewed_at'))
            .values('hour')
            .annotate(views=Count('id'))
        )
    }
    peak_hours = [{'hour': h, 'views': hour_dict.get(h, 0)} for h in range(24)]

    result = {
        'summary':          summary,
        'conversion_rates': conversion_rates,
        'trends':           trends,
        'views_by_source':  views_by_source,
        'peak_hours':       peak_hours,
    }
    cache.set(cache_key, result, 300)
    return result


# ---------------------------------------------------------------------------
# 2. Per-listing analytics queryset
# ---------------------------------------------------------------------------

def get_dealer_listing_analytics(user, ordering='-view_count', status_filter=None,
                                  created_after=None, created_before=None):
    from cars.models import Listing

    now      = timezone.now()
    since_7  = now - timedelta(days=7)
    since_30 = now - timedelta(days=30)

    qs = (
        Listing.objects
        .filter(owner=user, is_active=True)
        .annotate(
            lead_count=Count('leads', distinct=True),
            appointment_count=Count('appointments', distinct=True),
            message_count=Count('conversations', distinct=True),
            views_last_7=Count(
                'view_logs', distinct=True,
                filter=Q(view_logs__viewed_at__gte=since_7),
            ),
            views_last_30=Count(
                'view_logs', distinct=True,
                filter=Q(view_logs__viewed_at__gte=since_30),
            ),
            leads_last_7=Count(
                'leads', distinct=True,
                filter=Q(leads__created_at__gte=since_7),
            ),
            leads_last_30=Count(
                'leads', distinct=True,
                filter=Q(leads__created_at__gte=since_30),
            ),
        )
        .prefetch_related('images')
    )

    if status_filter:
        qs = qs.filter(status=status_filter)
    if created_after:
        qs = qs.filter(created_at__gte=created_after)
    if created_before:
        qs = qs.filter(created_at__lte=created_before)

    ordering_map = {
        'views':       'view_count',
        '-views':      '-view_count',
        'leads':       'lead_count',
        '-leads':      '-lead_count',
        'created_at':  'created_at',
        '-created_at': '-created_at',
    }
    qs = qs.order_by(ordering_map.get(ordering, '-view_count'))
    return qs


def serialize_listing_analytics(listing) -> dict:
    today       = timezone.now().date()
    days_listed = max((today - listing.created_at.date()).days, 1)
    view_count  = listing.view_count or 0
    lead_count  = listing.lead_count

    return {
        'id':                listing.pk,
        'make':              listing.make,
        'model':             listing.model,
        'year':              listing.year,
        'price':             str(listing.price),
        'status':            listing.status,
        'primary_image_url': _primary_image_url(listing),
        'created_at':        listing.created_at.date().isoformat(),
        'stats': {
            'view_count':        view_count,
            'unique_view_count': listing.unique_view_count or 0,
            'lead_count':        lead_count,
            'favorite_count':    0,
            'appointment_count': listing.appointment_count,
            'message_count':     listing.message_count,
            'conversion_rate':   _safe_rate(lead_count, view_count),
            'days_listed':       days_listed,
            'views_per_day':     round(view_count / days_listed, 1),
        },
        'trends': {
            'views_last_7_days':  listing.views_last_7,
            'views_last_30_days': listing.views_last_30,
            'leads_last_7_days':  listing.leads_last_7,
            'leads_last_30_days': listing.leads_last_30,
        },
    }


# ---------------------------------------------------------------------------
# 3. Single listing deep analytics
# ---------------------------------------------------------------------------

def get_single_listing_analytics(listing) -> dict:
    from cars.models import ViewLog
    from leads.models import Lead
    from bookings.models import Appointment
    from messaging.models import Conversation

    cache_key = f'listing_analytics_{listing.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    now   = timezone.now()
    today = now.date()

    leads = Lead.objects.filter(listing=listing)
    leads_by_status = {
        s: leads.filter(status=s).count()
        for s in ('new', 'contacted', 'closed', 'spam')
    }

    first_lead = leads.order_by('created_at').first()
    if first_lead:
        d = (first_lead.created_at.date() - listing.created_at.date()).days
        avg_time_to_first_lead = f"{d} day{'s' if d != 1 else ''}"
    else:
        avg_time_to_first_lead = None

    days_listed  = max((today - listing.created_at.date()).days, 1)
    total_views  = listing.view_count or 0
    total_leads  = leads.count()

    lifetime_stats = {
        'total_views':             total_views,
        'unique_views':            listing.unique_view_count or 0,
        'total_leads':             total_leads,
        'leads_by_status':         leads_by_status,
        'total_favorites':         0,
        'total_appointments':      Appointment.objects.filter(listing=listing).count(),
        'total_messages':          Conversation.objects.filter(listing=listing).count(),
        'conversion_rate':         _safe_rate(total_leads, total_views),
        'avg_time_to_first_lead':  avg_time_to_first_lead,
        'days_listed':             days_listed,
    }

    # Daily views — last 30 days
    since_30 = now - timedelta(days=30)
    vlog_qs  = ViewLog.objects.filter(listing=listing)

    daily_raw = (
        vlog_qs
        .filter(viewed_at__gte=since_30)
        .annotate(date=TruncDate('viewed_at'))
        .values('date')
        .annotate(views=Count('id'), unique_views=Count('user', distinct=True))
        .order_by('date')
    )
    daily_dict = {r['date']: (r['views'], r['unique_views']) for r in daily_raw}
    daily_views = [
        {
            'date':         str(today - timedelta(days=i)),
            'views':        daily_dict.get(today - timedelta(days=i), (0, 0))[0],
            'unique_views': daily_dict.get(today - timedelta(days=i), (0, 0))[1],
        }
        for i in range(29, -1, -1)
    ]

    views_by_source = list(
        vlog_qs.values('source').annotate(count=Count('id')).order_by('-count')
    )

    hour_dict = {
        r['hour']: r['count']
        for r in (
            vlog_qs
            .annotate(hour=ExtractHour('viewed_at'))
            .values('hour')
            .annotate(count=Count('id'))
        )
    }
    views_by_hour = [{'hour': h, 'count': hour_dict.get(h, 0)} for h in range(24)]

    lead_timeline = [
        {
            'date':       str(r['created_at__date']),
            'buyer_name': r['buyer__name'] or r['buyer__email'],
            'status':     r['status'],
        }
        for r in (
            leads.select_related('buyer')
            .order_by('-created_at')[:20]
            .values('created_at__date', 'buyer__name', 'buyer__email', 'status')
        )
    ]

    result = {
        'listing': {
            'id':                listing.pk,
            'make':              listing.make,
            'model':             listing.model,
            'year':              listing.year,
            'price':             str(listing.price),
            'status':            listing.status,
            'primary_image_url': _primary_image_url(listing),
        },
        'lifetime_stats':  lifetime_stats,
        'daily_views':     daily_views,
        'views_by_source': views_by_source,
        'views_by_hour':   views_by_hour,
        'lead_timeline':   lead_timeline,
    }
    cache.set(cache_key, result, 300)
    return result


# ---------------------------------------------------------------------------
# 4. Comparative analytics
# ---------------------------------------------------------------------------

def get_comparative_analytics(user) -> dict:
    from cars.models import Listing
    from leads.models import Lead

    cache_key = f'comparative_analytics_{user.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    dealer_listings = Listing.objects.filter(owner=user, is_active=True)
    dealer_count    = dealer_listings.count()

    d_agg = dealer_listings.aggregate(total_views=Sum('view_count'))
    dealer_total_views = d_agg['total_views'] or 0
    dealer_avg_views   = round(dealer_total_views / dealer_count, 1) if dealer_count else 0.0

    dealer_ids         = list(dealer_listings.values_list('id', flat=True))
    dealer_total_leads = Lead.objects.filter(listing_id__in=dealer_ids).count()
    dealer_avg_leads   = round(dealer_total_leads / dealer_count, 1) if dealer_count else 0.0
    dealer_avg_conv    = _safe_rate(dealer_total_leads, dealer_total_views)

    sold_qs = dealer_listings.filter(status='sold', status_changed_at__isnull=False)
    sold_dur = sold_qs.annotate(
        dur=ExpressionWrapper(F('status_changed_at') - F('created_at'), output_field=DurationField())
    ).aggregate(avg=Avg('dur'))['avg']
    dealer_avg_days = round(sold_dur.total_seconds() / 86400) if sold_dur else None

    # Platform averages
    platform_qs = Listing.objects.filter(status='approved', is_active=True)
    p_count     = platform_qs.count()
    p_agg       = platform_qs.aggregate(total_views=Sum('view_count'))
    p_views     = p_agg['total_views'] or 0
    p_avg_views = round(p_views / p_count, 1) if p_count else 0.0
    p_ids       = list(platform_qs.values_list('id', flat=True))
    p_leads     = Lead.objects.filter(listing_id__in=p_ids).count()
    p_avg_leads = round(p_leads / p_count, 1) if p_count else 0.0
    p_avg_conv  = _safe_rate(p_leads, p_views)

    p_sold_dur = (
        Listing.objects
        .filter(status='sold', status_changed_at__isnull=False)
        .annotate(dur=ExpressionWrapper(F('status_changed_at') - F('created_at'), output_field=DurationField()))
        .aggregate(avg=Avg('dur'))['avg']
    )
    platform_avg_days = round(p_sold_dur.total_seconds() / 86400) if p_sold_dur else None

    # Performance score: rank dealer's avg views among all dealers
    all_dealer_avgs = []
    for d in User.objects.filter(role='importer'):
        d_qs = Listing.objects.filter(owner=d, is_active=True)
        d_n  = d_qs.count()
        if not d_n:
            continue
        d_v = d_qs.aggregate(tv=Sum('view_count'))['tv'] or 0
        all_dealer_avgs.append(d_v / d_n)

    all_dealer_avgs.sort()
    n = len(all_dealer_avgs)
    if n == 0:
        score = 'average'
    else:
        rank       = sum(1 for v in all_dealer_avgs if v <= dealer_avg_views)
        percentile = rank / n
        if percentile >= 0.90:
            score = 'top_performer'
        elif percentile >= 0.50:
            score = 'above_average'
        else:
            score = 'below_average'

    result = {
        'dealer_avg_views_per_listing':   dealer_avg_views,
        'platform_avg_views_per_listing': p_avg_views,
        'dealer_avg_leads_per_listing':   dealer_avg_leads,
        'platform_avg_leads_per_listing': p_avg_leads,
        'dealer_avg_conversion_rate':     dealer_avg_conv,
        'platform_avg_conversion_rate':   p_avg_conv,
        'dealer_avg_days_to_sell':        dealer_avg_days,
        'platform_avg_days_to_sell':      platform_avg_days,
        'performance_score':              score,
    }
    cache.set(cache_key, result, 300)
    return result


# ---------------------------------------------------------------------------
# 5. Top performing listings
# ---------------------------------------------------------------------------

def get_top_performing_listings(user) -> dict:
    from cars.models import Listing

    cache_key = f'top_performing_{user.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    base_qs = list(
        Listing.objects
        .filter(owner=user, is_active=True)
        .annotate(lead_count=Count('leads', distinct=True))
        .prefetch_related('images')
    )

    def _mini(listing) -> dict:
        return {
            'id':              listing.pk,
            'make':            listing.make,
            'model':           listing.model,
            'year':            listing.year,
            'price':           str(listing.price),
            'status':          listing.status,
            'view_count':      listing.view_count,
            'lead_count':      listing.lead_count,
            'conversion_rate': _safe_rate(listing.lead_count, listing.view_count or 0),
        }

    def _conv(l):
        return _safe_rate(l.lead_count, l.view_count or 0)

    most_viewed     = [_mini(l) for l in sorted(base_qs, key=lambda l: l.view_count, reverse=True)[:5]]
    most_leads      = [_mini(l) for l in sorted(base_qs, key=lambda l: l.lead_count, reverse=True)[:5]]
    best_conversion = [_mini(l) for l in sorted(
        [l for l in base_qs if (l.view_count or 0) >= 10], key=_conv, reverse=True
    )[:5]]
    needs_attention = [_mini(l) for l in sorted(
        [l for l in base_qs if (l.view_count or 0) > 0 and l.lead_count == 0],
        key=lambda l: l.view_count, reverse=True
    )[:5]]

    result = {
        'most_viewed':      most_viewed,
        'most_leads':       most_leads,
        'most_favorited':   [],   # Favorite FK points to Car, not Listing
        'best_conversion':  best_conversion,
        'needs_attention':  needs_attention,
    }
    cache.set(cache_key, result, 300)
    return result


# ---------------------------------------------------------------------------
# 6. Admin platform analytics
# ---------------------------------------------------------------------------

def get_admin_platform_analytics() -> dict:
    from cars.models import Listing, ViewLog
    from leads.models import Lead

    cache_key = 'admin_platform_analytics'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    now         = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    platform_summary = {
        'total_users':            User.objects.count(),
        'total_importers':          User.objects.filter(role='importer').count(),
        'total_listings':         Listing.objects.count(),
        'total_active_listings':  Listing.objects.filter(status='approved', is_active=True).count(),
        'total_leads':            Lead.objects.count(),
        'total_views_today':      ViewLog.objects.filter(viewed_at__gte=today_start).count(),
        'total_views_this_week':  ViewLog.objects.filter(viewed_at__gte=now - timedelta(days=7)).count(),
        'total_views_this_month': ViewLog.objects.filter(viewed_at__gte=now - timedelta(days=30)).count(),
    }

    def _trend(model, date_field: str, days: int = 7) -> list:
        since = now - timedelta(days=days)
        raw = (
            model.objects
            .filter(**{f'{date_field}__gte': since})
            .annotate(date=TruncDate(date_field))
            .values('date')
            .annotate(count=Count('id'))
            .order_by('date')
        )
        return _fill_date_gaps({r['date']: r['count'] for r in raw}, days)

    growth = {
        'new_users_last_7_days':    _trend(User, 'date_joined'),
        'new_listings_last_7_days': _trend(Listing, 'created_at'),
        'new_leads_last_7_days':    _trend(Lead, 'created_at'),
    }

    total_active = Listing.objects.filter(is_active=True).count()

    def _top_field(field, n=10):
        raw = (
            Listing.objects.filter(is_active=True)
            .values(field)
            .annotate(count=Count('id'))
            .order_by('-count')[:n]
        )
        return [
            {
                field:        r[field],
                'count':      r['count'],
                'percentage': round(r['count'] / total_active * 100, 1) if total_active else 0.0,
            }
            for r in raw
        ]

    total_leads    = Lead.objects.count()
    contacted_cnt  = Lead.objects.filter(status='contacted').count()
    closed_cnt     = Lead.objects.filter(status='closed').count()

    result = {
        'platform_summary': platform_summary,
        'growth':           growth,
        'top_makes':        _top_field('make'),
        'top_cities':       _top_field('city'),
        'lead_funnel': {
            'total':           total_leads,
            'contacted':       contacted_cnt,
            'closed':          closed_cnt,
            'conversion_rate': _safe_rate(closed_cnt, total_leads),
        },
    }
    cache.set(cache_key, result, 300)
    return result

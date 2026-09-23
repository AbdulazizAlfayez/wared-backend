"""
Facets for the Filters screen — every value the user can actually pick, with a
count, computed from publicly visible listings only.

Bilingual labels are used where they genuinely exist and are never invented:

  * source_countries — `SourceCountry` carries `name_ar`, so these are real.
  * cities           — `City` carries `name_ar`. Listings store only a free-text
                       `city` string (`city_obj` is null on every public row
                       today), so the string is matched against `City.name_en`
                       to recover the Arabic. An unmatched string falls back to
                       itself.
  * makes / models   — `Listing.make_ar` / `model_ar` are used when populated;
                       they are empty on every public row today, so these fall
                       back to English.
  * the enum facets  — `Listing`'s choice tuples are English-only 2-tuples.
                       There is no Arabic for them anywhere in the backend, so
                       `label_ar` repeats the English label.

`value` is always what the matching filter accepts, not a prettier slug: the
city facet emits the lower-cased listing string because the `city` filter
matches that column, and a slug like `al-ahsa` would match nothing.
"""

from django.core.cache import cache
from django.db.models import Count, Max, Min

from locations.models import City
from source_countries.models import SourceCountry

from .models import Listing
from .visibility import public_market_q

# Bumped to v2 when `mileage` was added: a client that expects the new key
# would otherwise read a cached v1 payload without it for up to CACHE_TTL.
CACHE_KEY = 'listings:filter_options:v2'
CACHE_TTL = 300  # 5 minutes

#: The slider's granularity. A constant, not derived from the data.
PRICE_STEP = 5000

#: Mileage moves in 5,000 km notches. Deriving a step from the data would make
#: the handle behave differently on every visit.
MILEAGE_STEP = 5000


def _counted(qs, field):
    """`[{field: value, 'count': n}, …]`, most common first, blanks dropped."""
    return (
        qs.exclude(**{field: ''})
        .exclude(**{f'{field}__isnull': True})
        .values(field)
        .annotate(count=Count('id', distinct=True))
        .order_by('-count', field)
    )


def _enum_facet(qs, field, choices):
    """A facet whose labels come from a model choice tuple (English only)."""
    labels = dict(choices)
    out = []
    for row in _counted(qs, field):
        value = row[field]
        if not value or not row['count']:
            continue
        label = labels.get(value, value)
        out.append(
            {
                'value': value,
                'label_en': label,
                # No Arabic exists for these choices; see the module docstring.
                'label_ar': label,
                'count': row['count'],
            }
        )
    return out


def _makes_facet(qs):
    """Makes, each with the models available *within that make*."""
    # One query for the whole Arabic lookup rather than one per make.
    make_ar = dict(qs.exclude(make_ar='').values_list('make', 'make_ar').distinct())
    model_ar = dict(qs.exclude(model_ar='').values_list('model', 'model_ar').distinct())

    out = []
    for row in _counted(qs, 'make'):
        make = row['make']
        models = [
            {
                'value': m['model'],
                'label_en': m['model'],
                'label_ar': model_ar.get(m['model'], m['model']),
                'count': m['count'],
            }
            for m in _counted(qs.filter(make=make), 'model')
            if m['count']
        ]
        out.append(
            {
                'value': make,
                'label_en': make,
                'label_ar': make_ar.get(make, make),
                'count': row['count'],
                'models': models,
            }
        )
    return out


def cities_facet(qs):
    """
    Cities, labelled from the locations app.

    Returns `(facet, unmatched)` — `unmatched` is every listing city string with
    no `City` row, which is the data gap worth knowing about rather than hiding.
    """
    by_name = {city.name_en.casefold(): city for city in City.objects.all()}

    facet, unmatched = [], []
    for row in _counted(qs, 'city'):
        raw = row['city']
        match = by_name.get(raw.casefold())
        if match is None:
            unmatched.append(raw)
        facet.append(
            {
                # Lower-cased, because that is what the `city` filter matches.
                'value': raw.casefold(),
                'label_en': match.name_en if match else raw,
                'label_ar': match.name_ar if match else raw,
                'count': row['count'],
            }
        )
    return facet, unmatched


def _source_countries_facet(qs):
    """Countries, labelled from `SourceCountry` where a row exists."""
    by_code = {c.code: c for c in SourceCountry.objects.all()}
    choice_labels = dict(Listing.SOURCE_COUNTRY_CHOICES)

    out = []
    for row in _counted(qs, 'source_country'):
        code = row['source_country']
        country = by_code.get(code)
        label_en = country.name_en if country else choice_labels.get(code, code)
        out.append(
            {
                'value': code,
                'label_en': label_en,
                # Falls back to English when the code has no SourceCountry row.
                'label_ar': country.name_ar if country else label_en,
                'count': row['count'],
            }
        )
    return out


def _ceil_to_step(value, step, *, fallback=150_000):
    """
    Round up to the next whole step.

    `fallback` is what an empty market reports: a slider still has to have a
    right-hand end, and 150,000 km is the round number the labels were written
    against.
    """
    if value is None:
        return fallback
    notches = -(-int(value) // step)  # ceiling division
    return max(step, notches * step)


def compute_filter_options():
    """The uncached payload. Public listings only — see `cars.visibility`."""
    qs = Listing.objects.filter(public_market_q()).distinct()

    cities, _unmatched = cities_facet(qs)
    bounds = qs.aggregate(
        price_min=Min('price'),
        price_max=Max('price'),
        final_min=Min('final_price_sar'),
        final_max=Max('final_price_sar'),
        year_min=Min('year'),
        year_max=Max('year'),
        mileage_max=Max('mileage'),
    )

    return {
        'makes': _makes_facet(qs),
        'cities': cities,
        'source_countries': _source_countries_facet(qs),
        # Distinct from source_country — see the note in the report. Included so
        # a client can filter on whichever vocabulary its cards display.
        'imported_from': _enum_facet(qs, 'imported_from', Listing.IMPORT_SOURCE_CHOICES),
        'condition': _enum_facet(qs, 'condition', Listing.CONDITION_CHOICES),
        'body_type': _enum_facet(qs, 'body_type', Listing.BODY_TYPE_CHOICES),
        'transmission': _enum_facet(qs, 'transmission', Listing.TRANSMISSION_CHOICES),
        'fuel_type': _enum_facet(qs, 'fuel_type', Listing.FUEL_TYPE_CHOICES),
        'drive_type': _enum_facet(qs, 'drive_type', Listing.DRIVE_TYPE_CHOICES),
        'import_status': _enum_facet(qs, 'import_status', Listing.IMPORT_STATUS_CHOICES),
        # Over `price`, because that is the column `price_min`/`price_max`
        # filter. `final_price_sar` is carried separately for the filters that
        # target it.
        'price': {
            'min': _number(bounds['price_min']),
            'max': _number(bounds['price_max']),
            'step': PRICE_STEP,
        },
        'final_price_sar': {
            'min': _number(bounds['final_min']),
            'max': _number(bounds['final_max']),
        },
        'year': {'min': bounds['year_min'], 'max': bounds['year_max']},
        # Mileage always starts at zero — a brand-new import is the bottom of
        # the range whether or not one is listed today, and a slider whose
        # left edge wandered between visits would be unusable. Only the top
        # comes from the data, rounded UP to a whole step so the highest car on
        # the market is inside the track rather than sitting on its edge.
        'mileage': {
            'min': 0,
            'max': _ceil_to_step(bounds['mileage_max'], MILEAGE_STEP),
            'step': MILEAGE_STEP,
        },
    }


def _number(value):
    """Decimals are not JSON; None stays None so a client can tell "no data"."""
    return None if value is None else float(value)


def get_filter_options():
    """Cached for five minutes; the cache is busted whenever a Listing saves."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    payload = compute_filter_options()
    cache.set(CACHE_KEY, payload, CACHE_TTL)
    return payload

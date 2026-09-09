"""
Management command: import_real_cars
------------------------------------
Reads the Teoalida GCC Car Database Excel file and updates existing Listing
records (plus creates extras) with real make/model/spec data.

Safe to run multiple times (idempotent via a stable ordering of both the Excel
rows and the Listing queryset — each run produces the same mapping).

Usage:
    python manage.py import_real_cars
    python manage.py import_real_cars --file /path/to/other.xlsx
"""

import os
import random
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cars.models import Listing

# ---------------------------------------------------------------------------
# Column indices in "Engine Specs" sheet (0-based after row[0] which is col A)
# ---------------------------------------------------------------------------
COL_MAKE      = 2   # C  — Make
COL_MAKE_AR   = 3   # D  — Make Arabic
COL_MODEL     = 4   # E  — Model
COL_MODEL_AR  = 5   # F  — Model Arabic
COL_YEAR      = 6   # G  — Year
COL_PRICE_KSA = 10  # K  — Price KSA
COL_ORIGIN    = 11  # L  — Country of Origin
COL_BODY      = 13  # N  — Body Styles
COL_ENGINE    = 23  # X  — Engine
COL_GEARBOX   = 24  # Y  — Gearbox
COL_HP        = 25  # Z  — Power (hp)

DATA_START_ROW = 16  # first data row (1-based)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_ksa_price(raw):
    """
    Extract the LOWEST SAR price from strings like:
      "SAR 175,000 - 185,000 (320I M Sport);SAR 199,000 - 235,000 (330i);"
    Returns an int or None if no SAR price found.
    """
    if not raw:
        return None
    raw = str(raw)
    # Find all SAR amounts
    matches = re.findall(r'SAR\s*([\d,]+)', raw)
    if not matches:
        return None
    prices = []
    for m in matches:
        try:
            prices.append(int(m.replace(',', '')))
        except ValueError:
            pass
    return min(prices) if prices else None


def _map_body_type(raw):
    if not raw:
        return 'other'
    s = raw.lower()
    # Take only the first style when multiple are listed (e.g. "4-door sedan, 2-door coupe")
    first = s.split(',')[0].strip()
    if 'sedan' in first:
        return 'sedan'
    if 'suv' in first or 'sport utility' in first:
        return 'suv'
    if 'pickup' in first or 'truck' in first:
        return 'truck'
    if 'van' in first or 'minivan' in first:
        return 'van'
    if 'coupe' in first:
        return 'coupe'
    if 'hatchback' in first:
        return 'hatchback'
    if 'wagon' in first or 'estate' in first:
        return 'wagon'
    if 'convertible' in first or 'cabriolet' in first or 'roadster' in first:
        return 'convertible'
    if 'crossover' in first:
        return 'crossover'
    return 'other'


def _map_transmission(gearbox_raw):
    if not gearbox_raw:
        return 'automatic'
    g = str(gearbox_raw).upper()
    if 'CVT' in g:
        return 'cvt'
    # If only manual codes present (M) with no A → manual
    # If contains A → automatic (most common for GCC new cars)
    has_a = bool(re.search(r'\dA', g))
    has_m = bool(re.search(r'\dM', g))
    if has_a:
        return 'automatic'
    if has_m:
        return 'manual'
    return 'automatic'


def _parse_engine_size(engine_raw):
    """Extract displacement, e.g. '2.0TC I4 RWD' → Decimal('2.0'), '1.5TC I3 RWD' → Decimal('1.5')"""
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    if not engine_raw:
        return None
    m = re.match(r'([\d.]+)', str(engine_raw).strip())
    if m:
        try:
            d = Decimal(m.group(1)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
            return d
        except (InvalidOperation, ValueError):
            pass
    return None


def _parse_drive_type(engine_raw):
    if not engine_raw:
        return ''
    e = str(engine_raw).upper()
    # Take the FIRST drive token found
    if 'AWD' in e:
        return 'awd'
    if '4WD' in e:
        return 'awd'
    if 'RWD' in e:
        return 'rwd'
    if 'FWD' in e:
        return 'fwd'
    return ''


def _parse_cylinders(engine_raw):
    if not engine_raw:
        return None
    e = str(engine_raw).upper()
    patterns = [
        (r'I(\d)', lambda m: int(m.group(1))),
        (r'V(\d+)', lambda m: int(m.group(1))),
        (r'W(\d+)', lambda m: int(m.group(1))),
        (r'H(\d+)', lambda m: int(m.group(1))),  # Boxer
    ]
    for pat, extractor in patterns:
        m = re.search(pat, e)
        if m:
            return extractor(m)
    return None


def _parse_fuel_type(engine_raw):
    if not engine_raw:
        return 'petrol'
    e = str(engine_raw).upper()
    if 'EV' in e or 'BEV' in e or 'ELECTRIC' in e:
        return 'electric'
    if 'PHEV' in e or 'MHEV' in e or '+E' in e or 'HYBRID' in e or e.endswith('H'):
        return 'hybrid'
    # "2.5H" pattern (Toyota hybrid shorthand)
    if re.search(r'\dH\b', e):
        return 'hybrid'
    if 'DIESEL' in e or 'D ' in e or re.search(r'\dD\b', e):
        return 'diesel'
    return 'petrol'


def _map_origin(country_raw):
    if not country_raw:
        return 'local'
    c = str(country_raw).lower()
    gcc = {'saudi', 'uae', 'kuwait', 'qatar', 'bahrain', 'oman'}
    if any(g in c for g in gcc):
        return 'gcc'
    if 'us' in c or 'america' in c:
        return 'american'
    if c in ('germany', 'france', 'italy', 'uk', 'britain', 'sweden', 'spain'):
        return 'european'
    if 'korea' in c:
        return 'korean'
    if 'japan' in c:
        return 'japanese'
    return 'other'


def _make_description(year, make, model, hp, engine_size, transmission, fuel_type):
    trans_label = {'automatic': 'automatic', 'manual': 'manual', 'cvt': 'CVT'}.get(transmission, 'automatic')
    fuel_label  = {'petrol': 'petrol', 'diesel': 'diesel', 'hybrid': 'hybrid',
                   'electric': 'electric'}.get(fuel_type, 'petrol')
    engine_str  = f'{engine_size}L ' if engine_size else ''
    hp_str      = f'{hp}hp ' if hp else ''
    return (
        f"Well maintained {year} {make} {model} in excellent condition. "
        f"{hp_str}{engine_str}{fuel_label} engine with {trans_label} transmission. "
        f"GCC specs, full service history, single owner. "
        f"Clean interior, no accidents, ready to drive."
    )


def _make_description_ar(year, make_ar, model_ar, hp, engine_size, transmission):
    trans_ar = {'automatic': 'أوتوماتيك', 'manual': 'يدوي', 'cvt': 'CVT'}.get(transmission, 'أوتوماتيك')
    engine_str = f'{engine_size} لتر ' if engine_size else ''
    hp_str     = f'{hp} حصان ' if hp else ''
    make_str   = make_ar or ''
    model_str  = model_ar or ''
    return (
        f"{make_str} {model_str} {year} بحالة ممتازة. "
        f"محرك {engine_str}{hp_str}ناقل حركة {trans_ar}. "
        f"مواصفات خليجية، سجل صيانة كامل، مالك واحد. لا حوادث."
    )


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Update existing Listing records with real car data from the Teoalida GCC Excel file.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            default=os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                '..', '..', '..', 'Middle-East-GCC-Car-Database-by-Teoalida-SAMPLE.xlsx'
            ),
            help='Path to the Excel file (default: Middle-East-GCC-Car-Database-by-Teoalida-SAMPLE.xlsx in project root)',
        )

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError:
            raise CommandError('openpyxl is required. Install it with: pip install openpyxl')

        file_path = os.path.abspath(options['file'])
        if not os.path.exists(file_path):
            raise CommandError(f'Excel file not found: {file_path}')

        self.stdout.write(f'Reading {file_path} …')
        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        ws = wb['Engine Specs']

        # ---------------------------------------------------------------
        # Step 1: collect one record per unique (make, model, year)
        # ---------------------------------------------------------------
        unique_cars = {}   # key=(make,model,year) → dict of parsed fields
        for row in ws.iter_rows(min_row=DATA_START_ROW, values_only=True):
            make  = row[COL_MAKE]
            model = row[COL_MODEL]
            year  = row[COL_YEAR]

            if not make or not model or not year:
                continue
            # Only accept rows with a numeric year
            try:
                year = int(year)
            except (ValueError, TypeError):
                continue

            key = (str(make).strip(), str(model).strip(), year)
            if key in unique_cars:
                continue  # keep the first engine variant only

            make_ar  = row[COL_MAKE_AR]  or ''
            model_ar = row[COL_MODEL_AR] or ''
            price_ksa = _parse_ksa_price(row[COL_PRICE_KSA])
            body_raw  = row[COL_BODY]
            engine_raw = row[COL_ENGINE]
            gearbox_raw = row[COL_GEARBOX]
            hp_raw    = row[COL_HP]
            origin_raw = row[COL_ORIGIN]

            hp = None
            if hp_raw is not None:
                try:
                    hp = int(float(str(hp_raw)))
                except (ValueError, TypeError):
                    pass

            transmission = _map_transmission(gearbox_raw)
            engine_size  = _parse_engine_size(engine_raw)
            fuel_type    = _parse_fuel_type(engine_raw)
            drive_type   = _parse_drive_type(engine_raw)
            cylinders    = _parse_cylinders(engine_raw)
            body_type    = _map_body_type(body_raw)
            imported_from = _map_origin(origin_raw)

            unique_cars[key] = dict(
                make=str(make).strip(),
                make_ar=str(make_ar).strip() if make_ar else '',
                model=str(model).strip(),
                model_ar=str(model_ar).strip() if model_ar else '',
                year=year,
                price=price_ksa,
                body_type=body_type,
                transmission=transmission,
                engine_size=engine_size,
                horsepower=hp,
                drive_type=drive_type,
                cylinders=cylinders,
                fuel_type=fuel_type,
                imported_from=imported_from,
                description=_make_description(year, make, model, hp, engine_size, transmission, fuel_type),
                description_ar=_make_description_ar(year, make_ar or make, model_ar or model, hp, engine_size, transmission),
            )

        wb.close()

        car_list = list(unique_cars.values())
        self.stdout.write(f'Unique cars from Excel: {len(car_list)}')

        # ---------------------------------------------------------------
        # Step 2: fetch existing listings ordered by pk (stable)
        # ---------------------------------------------------------------
        listings = list(Listing.objects.order_by('pk'))
        n_existing = len(listings)
        self.stdout.write(f'Existing listings in DB: {n_existing}')

        # ---------------------------------------------------------------
        # Step 3: apply data
        # ---------------------------------------------------------------
        from django.contrib.auth import get_user_model
        User = get_user_model()
        dealer_ids = list(
            User.objects.filter(role__in=('importer', 'admin'))
                        .values_list('pk', flat=True)
        )
        if not dealer_ids:
            dealer_ids = list(User.objects.values_list('pk', flat=True))

        updated = 0
        created = 0

        with transaction.atomic():
            # Update existing listings (cycle through car_list if needed)
            for i, listing in enumerate(listings):
                car = car_list[i % len(car_list)]
                _apply_car_data(listing, car)
                updated += 1

            # Create new listings if Excel has more unique cars
            if len(car_list) > n_existing:
                from locations.models import City
                cities = list(City.objects.all())
                city_names_en = ['Riyadh', 'Jeddah', 'Makkah', 'Madinah', 'Dammam', 'Al Khobar', 'Taif', 'Abha']
                colors = ['White', 'Black', 'Silver', 'Gray', 'Red', 'Blue', 'Beige', 'Brown']

                for car in car_list[n_existing:]:
                    city_name = random.choice(city_names_en)
                    city_obj  = next((c for c in cities if city_name.lower() in c.name_en.lower()), None)
                    owner_id  = random.choice(dealer_ids)
                    mileage   = random.randint(5000, 120000)
                    color     = random.choice(colors)

                    listing = Listing(
                        owner_id=owner_id,
                        city=city_name,
                        city_obj=city_obj,
                        status='approved',
                        is_active=True,
                        mileage=mileage,
                        color=color,
                        condition='used',
                        negotiable=True,
                        service_history=True,
                        customs_cleared=True,
                    )
                    _apply_car_data(listing, car, is_new=True)
                    created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. Updated {updated} listings, Created {created} new listings.'
            )
        )


# ---------------------------------------------------------------------------
# Helper: write parsed fields onto a Listing instance and save
# ---------------------------------------------------------------------------

def _apply_car_data(listing, car, is_new=False):
    """Write all parseable Excel fields onto *listing* and call save()."""
    make  = car['make']
    model = car['model']
    year  = car['year']

    listing.make     = make
    listing.make_ar  = car['make_ar']
    listing.model    = model
    listing.model_ar = car['model_ar']
    listing.year     = year
    listing.title    = f"{year} {make} {model}"

    if car['price']:
        listing.price = car['price']
    elif is_new:
        listing.price = random.randint(50_000, 350_000)

    if car['body_type']:
        listing.body_type = car['body_type']
    if car['transmission']:
        listing.transmission = car['transmission']
    if car['fuel_type']:
        listing.fuel_type = car['fuel_type']
    if car['drive_type']:
        listing.drive_type = car['drive_type']
    if car['engine_size'] is not None:
        listing.engine_size = car['engine_size']
    if car['horsepower'] is not None:
        listing.horsepower = car['horsepower']
    if car['cylinders'] is not None:
        listing.cylinders = car['cylinders']
    if car['imported_from']:
        listing.imported_from = car['imported_from']
    if car['description']:
        listing.description = car['description']
    if car['description_ar']:
        listing.description_ar = car['description_ar']

    # For existing listings: keep mileage if already set; otherwise randomise
    if is_new or not listing.mileage:
        listing.mileage = random.randint(5000, 120000)
    # Keep existing city/owner/status intact for existing listings

    # Bypass the status-transition validator (we're not changing status)
    # by using update() on existing records
    if not is_new and listing.pk:
        fields = [
            'title', 'make', 'make_ar', 'model', 'model_ar', 'year',
            'body_type', 'transmission', 'fuel_type', 'drive_type',
            'engine_size', 'horsepower', 'cylinders', 'imported_from',
            'description', 'description_ar',
        ]
        if car['price']:
            fields.append('price')
        Listing.objects.filter(pk=listing.pk).update(
            **{f: getattr(listing, f) for f in fields}
        )
    else:
        # New listing — full save
        listing.full_clean(exclude=['status'])
        listing.save()

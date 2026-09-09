"""
Management command: import_car_images
--------------------------------------
Downloads car images and uploads them to Cloudinary, then attaches them to
Listing records as ListingImage entries.

Image URLs are read from the "Engine Specs" sheet of the Teoalida Excel file:
  - Column H (index 7)  → Image 1  (is_primary=True,  order=0)
  - Column I (index 8)  → Image 2  (is_primary=False, order=1)

Behaviour:
  - Skips listings that already have images (idempotent).
  - Tries the Excel URL first; falls back to a per-model placeholder if the
    source returns a non-200 status (drivearabia.com is known to block 403).
  - Waits 1 second between downloads to be polite.
  - Accepts --limit N to process only the first N listings (for testing).

Usage:
    python manage.py import_car_images
    python manage.py import_car_images --limit 5
    python manage.py import_car_images --file /path/to/other.xlsx
"""

import io
import os
import time

import cloudinary.uploader
import requests
from django.core.management.base import BaseCommand, CommandError

from cars.models import Listing, ListingImage

# ---------------------------------------------------------------------------
# Column indices in "Engine Specs" sheet (0-based)
# ---------------------------------------------------------------------------
COL_MAKE  = 2   # C — Make
COL_MODEL = 4   # E — Model
COL_YEAR  = 6   # G — Year
COL_IMG1  = 7   # H — Image 1 URL
COL_IMG2  = 8   # I — Image 2 URL

DATA_START_ROW = 16  # first data row (1-based), same as import_real_cars

# ---------------------------------------------------------------------------
# Fallback image URLs — used when the Excel URL is unreachable.
# These are direct CDN links known to return HTTP 200.
# ---------------------------------------------------------------------------
FALLBACK_IMAGES = {
    # BMW 3-Series
    ('bmw', '3-series'):  [
        'https://upload.wikimedia.org/wikipedia/commons/thumb/4/44/BMW_3_Series_sedan_%28F30%2C_facelift%2C_2015%29%2C_front_8.21.19.jpg/1280px-BMW_3_Series_sedan_%28F30%2C_facelift%2C_2015%29%2C_front_8.21.19.jpg',
        'https://upload.wikimedia.org/wikipedia/commons/thumb/0/0c/BMW_3_Series_Gran_Turismo_%28F34%29_%E2%80%93_Frontansicht%2C_29._August_2013%2C_D%C3%BCsseldorf.jpg/1280px-BMW_3_Series_Gran_Turismo_%28F34%29_%E2%80%93_Frontansicht%2C_29._August_2013%2C_D%C3%BCsseldorf.jpg',
    ],
    # Ford F-150
    ('ford', 'f-150'):  [
        'https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/2018_Ford_F-150_XLT%2C_front_10.6.18.jpg/1280px-2018_Ford_F-150_XLT%2C_front_10.6.18.jpg',
        'https://upload.wikimedia.org/wikipedia/commons/thumb/5/53/2021_Ford_F-150_XLT_SuperCrew%2C_front_4.20.21.jpg/1280px-2021_Ford_F-150_XLT_SuperCrew%2C_front_4.20.21.jpg',
    ],
    # Toyota Camry
    ('toyota', 'camry'): [
        'https://upload.wikimedia.org/wikipedia/commons/thumb/9/97/2018_Toyota_Camry_%28ASV70R%29_Ascent_sedan_%282018-10-02%29_01.jpg/1280px-2018_Toyota_Camry_%28ASV70R%29_Ascent_sedan_%282018-10-02%29_01.jpg',
        'https://upload.wikimedia.org/wikipedia/commons/thumb/e/e6/2018_Toyota_Camry_%28ASV70R%29_SL_sedan_%282018-11-02%29_01.jpg/1280px-2018_Toyota_Camry_%28ASV70R%29_SL_sedan_%282018-11-02%29_01.jpg',
    ],
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
}

TIMEOUT = 15  # seconds per HTTP request


def _fetch_image(url: str) -> bytes | None:
    """Download image bytes from *url*. Returns None on any error."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        if resp.status_code == 200 and resp.headers.get('Content-Type', '').startswith('image'):
            return resp.content
        return None
    except Exception:
        return None


def _get_fallback_urls(make: str, model: str) -> list[str]:
    """Return fallback URLs for a given make/model (case-insensitive)."""
    key = (make.lower().strip(), model.lower().strip())
    return FALLBACK_IMAGES.get(key, [])


def _upload_to_cloudinary(image_bytes: bytes, listing_id: int, slot: int) -> str | None:
    """
    Upload raw image bytes to Cloudinary.

    Returns the Cloudinary public_id string on success, or None on failure.
    """
    try:
        public_id = f'saudicarsale/listings/listing_{listing_id}_img{slot}'
        result = cloudinary.uploader.upload(
            io.BytesIO(image_bytes),
            public_id=public_id,
            overwrite=True,
            resource_type='image',
            transformation=[
                {'width': 800, 'height': 600, 'crop': 'fill', 'quality': 'auto'},
            ],
        )
        return result.get('public_id')
    except Exception as exc:
        return None


class Command(BaseCommand):
    help = 'Download car images from Excel URLs and attach them to Listings via Cloudinary.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            default=os.path.join(
                os.path.dirname(__file__),
                '..', '..', '..', 'Middle-East-GCC-Car-Database-by-Teoalida-SAMPLE.xlsx',
            ),
            help='Path to the Excel file.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Process only the first N unique car rows (for testing).',
        )

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError:
            raise CommandError('openpyxl is required. Run: pip install openpyxl')

        file_path = os.path.abspath(options['file'])
        if not os.path.exists(file_path):
            raise CommandError(f'Excel file not found: {file_path}')

        limit = options['limit']

        self.stdout.write(f'Reading {file_path} …')
        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)

        # Locate the "Engine Specs" sheet (first sheet as fallback)
        sheet_name = next(
            (n for n in wb.sheetnames if 'engine' in n.lower()),
            wb.sheetnames[0],
        )
        ws = wb[sheet_name]
        self.stdout.write(f'Using sheet: {sheet_name}')

        # -----------------------------------------------------------------------
        # Collect unique (make, model, year, img1, img2) rows from the Excel.
        # We deduplicate on (make, model, year) — same as import_real_cars.
        # -----------------------------------------------------------------------
        seen = set()
        car_rows = []
        for i, row in enumerate(ws.iter_rows(min_row=DATA_START_ROW, values_only=True)):
            make  = str(row[COL_MAKE]  or '').strip()
            model = str(row[COL_MODEL] or '').strip()
            year  = row[COL_YEAR]

            if not make or not model or not year:
                continue
            try:
                year = int(year)
            except (TypeError, ValueError):
                continue

            key = (make.lower(), model.lower(), year)
            if key in seen:
                continue
            seen.add(key)

            img1 = str(row[COL_IMG1] or '').strip() or None
            img2 = str(row[COL_IMG2] or '').strip() or None

            car_rows.append({
                'make': make, 'model': model, 'year': year,
                'img1': img1, 'img2': img2,
            })

            if limit and len(car_rows) >= limit:
                break

        wb.close()
        self.stdout.write(f'Found {len(car_rows)} unique car rows in Excel.')

        # -----------------------------------------------------------------------
        # Match each Excel row to Listing records (same stable ordering as
        # import_real_cars so the mapping is consistent across runs).
        # -----------------------------------------------------------------------
        listings = list(
            Listing.objects.order_by('make', 'model', 'year', 'id')
        )

        # Group listings by (make, model, year)
        from collections import defaultdict
        listing_map: dict[tuple, list] = defaultdict(list)
        for lst in listings:
            listing_map[(lst.make.lower(), lst.model.lower(), lst.year)].append(lst)

        stats = {'skipped_has_images': 0, 'skipped_no_url': 0,
                 'skipped_download_fail': 0, 'uploaded': 0, 'errors': 0}

        for car in car_rows:
            key = (car['make'].lower(), car['model'].lower(), car['year'])
            matched = listing_map.get(key, [])

            if not matched:
                self.stdout.write(
                    self.style.WARNING(
                        f"  No listing found for {car['make']} {car['model']} {car['year']} — skipping."
                    )
                )
                continue

            for listing in matched:
                # Skip listings that already have images
                if listing.images.exists():
                    stats['skipped_has_images'] += 1
                    continue

                self._process_listing(listing, car, stats)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done.\n'
            f'  Uploaded images  : {stats["uploaded"]}\n'
            f'  Already had imgs : {stats["skipped_has_images"]}\n'
            f'  No URL in Excel  : {stats["skipped_no_url"]}\n'
            f'  Download failures: {stats["skipped_download_fail"]}\n'
            f'  Upload errors    : {stats["errors"]}'
        ))

    def _process_listing(self, listing, car: dict, stats: dict):
        """Download & upload up to 2 images for *listing*."""
        slots = [
            (car['img1'], 0, True),   # (url, order, is_primary)
            (car['img2'], 1, False),
        ]
        any_uploaded = False

        for excel_url, order, is_primary in slots:
            # Build a list of URLs to try: Excel URL first, then fallback
            candidates = []
            if excel_url:
                candidates.append(excel_url)
            candidates.extend(_get_fallback_urls(car['make'], car['model']))

            if not candidates:
                stats['skipped_no_url'] += 1
                continue

            image_bytes = None
            used_url = None
            for url in candidates:
                self.stdout.write(f'  [{listing.id}] Fetching {url[:80]}…', ending=' ')
                image_bytes = _fetch_image(url)
                time.sleep(1)
                if image_bytes:
                    used_url = url
                    self.stdout.write(self.style.SUCCESS('OK'))
                    break
                else:
                    self.stdout.write(self.style.WARNING('FAIL'))

            if not image_bytes:
                self.stdout.write(
                    self.style.WARNING(
                        f'  [{listing.id}] All sources failed for slot {order} — skipping.'
                    )
                )
                stats['skipped_download_fail'] += 1
                continue

            public_id = _upload_to_cloudinary(image_bytes, listing.id, order)
            if not public_id:
                self.stdout.write(
                    self.style.ERROR(
                        f'  [{listing.id}] Cloudinary upload failed for slot {order}.'
                    )
                )
                stats['errors'] += 1
                continue

            try:
                ListingImage.objects.create(
                    listing=listing,
                    image=public_id,
                    is_primary=is_primary,
                    order=order,
                )
                stats['uploaded'] += 1
                any_uploaded = True
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  [{listing.id}] Saved ListingImage order={order} public_id={public_id}'
                    )
                )
            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(
                        f'  [{listing.id}] DB save failed for slot {order}: {exc}'
                    )
                )
                stats['errors'] += 1

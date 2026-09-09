"""
Management command: fetch_unsplash_images
------------------------------------------
Downloads images from loremflickr.com (free, no API key required) and uploads
them to Cloudinary, then attaches them to Listing records as ListingImage entries.

Note: source.unsplash.com has been deprecated and returns 503. loremflickr.com
is used instead — it returns real photos from Flickr matching the search query.

For every listing without images the command:
  1. Downloads an exterior shot  → is_primary=True,  order=0
  2. Downloads an interior/alt   → is_primary=False, order=1

Image sources tried in order:
  1. https://loremflickr.com/800/600/{make}+{model}+car
  2. https://loremflickr.com/800/600/{make}+car
  3. https://loremflickr.com/800/600/car+{body_type}
  4. https://loremflickr.com/800/600/car                  (last resort)

Usage:
    python manage.py fetch_unsplash_images
    python manage.py fetch_unsplash_images --limit 5
"""

import time
from io import BytesIO

import requests
import cloudinary.uploader
from django.core.management.base import BaseCommand

from cars.models import Listing, ListingImage


TIMEOUT = 15          # seconds per request
MIN_BYTES = 5_000     # anything smaller is likely an error page, not a real image
DELAY = 2             # seconds between downloads (Unsplash rate-limit courtesy)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candidate_urls(make: str, model: str, body_type: str, slot: str) -> list[str]:
    """
    Build an ordered list of image URLs to try for *make*/*model*.

    slot — 'exterior' or 'interior'
    """
    m = make.lower().replace(" ", "+")
    mo = model.lower().replace(" ", "+")
    bt = (body_type or "car").lower().replace(" ", "+")

    if slot == "exterior":
        return [
            f"https://loremflickr.com/800/600/{m}+{mo}+car",
            f"https://loremflickr.com/800/600/{m}+car",
            f"https://loremflickr.com/800/600/car+{bt}",
            f"https://loremflickr.com/800/600/car",
        ]
    else:  # interior / second image
        return [
            f"https://loremflickr.com/800/600/{m}+{mo}+interior",
            f"https://loremflickr.com/800/600/{m}+interior",
            f"https://loremflickr.com/800/600/car+interior",
            f"https://loremflickr.com/800/600/car",
        ]


def _fetch(url: str) -> bytes | None:
    """Download image bytes; return None on any failure."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) >= MIN_BYTES:
            ct = resp.headers.get("Content-Type", "")
            if "image" in ct or len(resp.content) > 10_000:
                return resp.content
    except Exception:
        pass
    return None


def _upload(image_bytes: bytes, listing_id: int, slot: int) -> str | None:
    """Upload raw bytes to Cloudinary; return public_id or None."""
    try:
        result = cloudinary.uploader.upload(
            BytesIO(image_bytes),
            folder="saudicarsale/listings",
            public_id=f"listing_{listing_id}_img{slot}",
            overwrite=True,
            resource_type="image",
        )
        return result.get("public_id")
    except Exception as exc:
        return None


def _download_and_save(listing, slot_index: int, slot_name: str,
                       is_primary: bool, stdout) -> bool:
    """
    Try each candidate URL for *listing*, upload to Cloudinary, create
    ListingImage.  Returns True on success, False on total failure.
    """
    candidates = _candidate_urls(
        listing.make, listing.model,
        listing.body_type or "car",
        slot_name,
    )

    for url in candidates:
        image_bytes = _fetch(url)
        time.sleep(DELAY)
        if image_bytes is None:
            continue

        public_id = _upload(image_bytes, listing.id, slot_index)
        if public_id is None:
            stdout.write("    Cloudinary upload failed, trying next source…")
            continue

        try:
            ListingImage.objects.create(
                listing=listing,
                image=public_id,
                is_primary=is_primary,
                order=slot_index,
            )
        except Exception as exc:
            # e.g. unique_together violation on a retry — treat as success-ish
            stdout.write(f"    DB save warning: {exc}")

        return True

    return False


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Fetch images from Unsplash/loremflickr and upload to Cloudinary for listings without images."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process only the first N listings (for testing).",
        )

    def handle(self, *args, **options):
        limit = options["limit"]

        # Listings that have NO images yet
        has_images = set(
            ListingImage.objects.values_list("listing_id", flat=True).distinct()
        )
        qs = (
            Listing.objects.filter(is_active=True)
            .exclude(id__in=has_images)
            .order_by("make", "model", "year", "id")
        )
        if limit:
            qs = qs[:limit]

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("All listings already have images. Nothing to do."))
            return

        self.stdout.write(f"Processing {total} listings without images…\n")

        done = 0
        img_count = 0
        fail_count = 0

        for idx, listing in enumerate(qs, start=1):
            label = f"{listing.year} {listing.make} {listing.model}"
            self.stdout.write(f"{idx}/{total}: {label}")

            # --- Slot 0: exterior (primary) ---
            self.stdout.write("  exterior… ", ending="")
            ok0 = _download_and_save(listing, 0, "exterior", True, self.stdout)
            if ok0:
                self.stdout.write(self.style.SUCCESS("✅"))
                img_count += 1
            else:
                self.stdout.write(self.style.ERROR("❌  (all sources failed)"))
                fail_count += 1

            # --- Slot 1: interior / second shot ---
            self.stdout.write("  interior… ", ending="")
            ok1 = _download_and_save(listing, 1, "interior", False, self.stdout)
            if ok1:
                self.stdout.write(self.style.SUCCESS("✅"))
                img_count += 1
            else:
                self.stdout.write(self.style.WARNING("⚠️   (skipped — no fallback worked)"))

            if ok0:
                done += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done!  {done} listings updated, "
            f"{img_count} images uploaded, "
            f"{fail_count} primary-image failures."
        ))

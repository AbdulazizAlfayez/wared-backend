"""
Management command: upload_local_images
-----------------------------------------
Uploads real car images from a local directory to Cloudinary, then assigns
each image to all matching Listing records.

Each image is uploaded ONCE and the resulting public_id is reused across all
listings that match that make/model — avoids redundant uploads.

Usage:
    python manage.py upload_local_images
    python manage.py upload_local_images --dir /path/to/other/dir
    python manage.py upload_local_images --overwrite   # reassign even if listing has images
"""

import os

import cloudinary.uploader
from django.core.management.base import BaseCommand, CommandError

from cars.models import Listing, ListingImage

# ---------------------------------------------------------------------------
# Filename stem → (make, model) mapping
# Matching is done case-insensitively against the file stem.
# ---------------------------------------------------------------------------
IMAGE_MAP: dict[str, tuple[str, str]] = {
    "bmw-3-series":       ("BMW",    "3-Series"),
    "bmw-m3":             ("BMW",    "M3"),
    "ford-f150":          ("Ford",   "F-150"),
    "ford-f150-raptor":   ("Ford",   "F-150 Raptor"),
    "toyota-camry":       ("Toyota", "Camry"),
}

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}

DEFAULT_IMAGE_DIR = os.path.expanduser(
    "~/Desktop/SaudiCarSale/public/car-images"
)


def _stem(filename: str) -> str:
    """Return the filename stem (no extension), lowercased."""
    return os.path.splitext(filename)[0].lower()


def _match_make_model(stem: str) -> tuple[str, str] | None:
    """Return (make, model) for a filename stem, or None if unrecognised."""
    # Exact match first
    if stem in IMAGE_MAP:
        return IMAGE_MAP[stem]
    # Loose: check if any key is contained in the stem or vice-versa
    for key, pair in IMAGE_MAP.items():
        if key in stem or stem in key:
            return pair
    return None


class Command(BaseCommand):
    help = "Upload local car images to Cloudinary and assign them to matching listings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default=DEFAULT_IMAGE_DIR,
            help=f"Directory containing image files (default: {DEFAULT_IMAGE_DIR})",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Reassign images even for listings that already have one.",
        )

    def handle(self, *args, **options):
        image_dir = os.path.abspath(os.path.expanduser(options["dir"]))
        overwrite = options["overwrite"]

        if not os.path.isdir(image_dir):
            raise CommandError(f"Image directory not found: {image_dir}")

        # Collect candidate files
        files = [
            f for f in os.listdir(image_dir)
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS
               and not f.startswith(".")
        ]
        if not files:
            raise CommandError(f"No supported image files found in {image_dir}")

        self.stdout.write(
            f"Found {len(files)} image file(s) in {image_dir}\n"
        )

        total_uploaded = 0
        total_assigned = 0
        total_skipped = 0
        unmatched_files = []

        # ------------------------------------------------------------------
        # Track which listings already have images (skip unless --overwrite)
        # ------------------------------------------------------------------
        listings_with_images: set[int] = set(
            ListingImage.objects.values_list("listing_id", flat=True).distinct()
        )

        for filename in sorted(files):
            stem = _stem(filename)
            pair = _match_make_model(stem)

            if pair is None:
                self.stdout.write(
                    self.style.WARNING(f"⚠️  {filename} — no matching make/model, skipping.")
                )
                unmatched_files.append(filename)
                continue

            make, model = pair
            file_path = os.path.join(image_dir, filename)

            # --- Upload to Cloudinary once ---
            self.stdout.write(
                f"Uploading {filename} → Cloudinary… ", ending=""
            )
            try:
                result = cloudinary.uploader.upload(
                    file_path,
                    public_id=f"saudicarsale/car-models/{stem}",
                    overwrite=True,
                    resource_type="image",
                )
                public_id = result["public_id"]
                self.stdout.write(self.style.SUCCESS("✅"))
                self.stdout.write(f"  public_id: {public_id}")
                total_uploaded += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"❌  Cloudinary error: {exc}"))
                continue

            # --- Assign to all matching listings ---
            listings = list(
                Listing.objects.filter(
                    make__iexact=make,
                    model__iexact=model,
                    is_active=True,
                ).order_by("id")
            )

            if not listings:
                self.stdout.write(
                    self.style.WARNING(
                        f"  No active listings found for {make} {model}."
                    )
                )
                continue

            # Filter out already-imaged listings unless --overwrite
            to_assign = [
                l for l in listings
                if overwrite or l.id not in listings_with_images
            ]
            already_done = len(listings) - len(to_assign)

            self.stdout.write(
                f"  Assigning to {len(to_assign)} {make} {model} listing(s)"
                + (f" ({already_done} already had images, skipped)" if already_done else "")
                + "… ",
                ending="",
            )

            assigned = 0
            errors = 0
            for listing in to_assign:
                try:
                    # Remove existing images for this listing if overwriting
                    if overwrite:
                        ListingImage.objects.filter(listing=listing).delete()

                    ListingImage.objects.create(
                        listing=listing,
                        image=public_id,
                        is_primary=True,
                        order=0,
                    )
                    listings_with_images.add(listing.id)
                    assigned += 1
                except Exception as exc:
                    self.stdout.write(
                        self.style.ERROR(f"\n    Error on listing #{listing.id}: {exc}")
                    )
                    errors += 1

            if errors == 0:
                self.stdout.write(self.style.SUCCESS("✅"))
            else:
                self.stdout.write(self.style.WARNING(f"⚠️  {errors} errors"))

            total_assigned += assigned
            total_skipped += already_done

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Summary: {total_uploaded} image(s) uploaded to Cloudinary, "
            f"{total_assigned} listing(s) updated, "
            f"{total_skipped} listing(s) skipped (already had images)."
        ))
        if unmatched_files:
            self.stdout.write(
                self.style.WARNING(
                    f"Unmatched files (add to IMAGE_MAP to handle): "
                    + ", ".join(unmatched_files)
                )
            )

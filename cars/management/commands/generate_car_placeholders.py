"""
Management command: generate_car_placeholders
----------------------------------------------
Generates placeholder car images using Pillow and uploads them to Cloudinary.
No external image fetching — 100% local generation, guaranteed to work.

Each image is a gradient-style colored rectangle with the car make/model/year
rendered in clean white text. Colors are keyed by car make for consistency.

Two images are created per listing:
  - order=0 is_primary=True  → exterior view label
  - order=1 is_primary=False → interior view label

Usage:
    python manage.py generate_car_placeholders
    python manage.py generate_car_placeholders --limit 5
    python manage.py generate_car_placeholders --overwrite   # replace existing
"""

import io
import hashlib

import cloudinary.uploader
from django.core.management.base import BaseCommand

from cars.models import Listing, ListingImage

# ---------------------------------------------------------------------------
# Color palette — per make (hex fill + accent)
# ---------------------------------------------------------------------------
MAKE_COLORS: dict[str, tuple[tuple, tuple]] = {
    # (primary_rgb, accent_rgb)
    "bmw":     ((15, 30, 80),   (0, 100, 200)),
    "toyota":  ((180, 0, 0),    (220, 60, 0)),
    "ford":    ((0, 40, 120),   (0, 80, 200)),
    "mercedes":(30, 30, 30),
    "honda":   ((180, 10, 10),  (220, 60, 60)),
    "nissan":  ((180, 0, 50),   (220, 50, 80)),
    "hyundai": ((0, 70, 160),   (30, 120, 200)),
    "kia":     ((180, 0, 0),    (200, 50, 0)),
    "chevrolet":((180, 0, 0),   (210, 50, 0)),
    "jeep":    ((20, 80, 20),   (40, 120, 40)),
    "lexus":   ((20, 20, 20),   (60, 60, 80)),
    "audi":    ((20, 20, 20),   (180, 30, 30)),
    "volkswagen":((0, 60, 150), (0, 100, 200)),
    "volvo":   ((0, 40, 80),    (0, 100, 160)),
    "land rover":((30, 80, 30), (60, 140, 60)),
    "porsche": ((60, 20, 20),   (160, 60, 0)),
    "mitsubishi":((160, 0, 0),  (200, 50, 50)),
    "default": ((30, 50, 90),   (60, 100, 160)),
}

W, H = 800, 600   # output image dimensions


def _make_colors(make: str) -> tuple[tuple, tuple]:
    key = make.lower().strip()
    for k, v in MAKE_COLORS.items():
        if k in key:
            # v might be stored as a tuple-of-tuple or just one tuple — normalise
            if isinstance(v[0], tuple):
                return v  # already (primary, accent)
            return (v, v)  # duplicate if only one was provided
    return MAKE_COLORS["default"]


def _generate_image(listing, slot: int) -> bytes:
    """
    Render a 800×600 placeholder image for *listing*.
    slot=0 → exterior  /  slot=1 → interior
    Returns raw PNG bytes.
    """
    from PIL import Image, ImageDraw, ImageFont

    primary_col, accent_col = _make_colors(listing.make)
    label = "EXTERIOR VIEW" if slot == 0 else "INTERIOR VIEW"

    # --- Canvas ---
    img = Image.new("RGB", (W, H), primary_col)
    draw = ImageDraw.Draw(img)

    # Gradient-ish bands
    for y in range(H):
        ratio = y / H
        r = int(primary_col[0] + (accent_col[0] - primary_col[0]) * ratio * 0.6)
        g = int(primary_col[1] + (accent_col[1] - primary_col[1]) * ratio * 0.6)
        b = int(primary_col[2] + (accent_col[2] - primary_col[2]) * ratio * 0.6)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Decorative diagonal stripe
    draw.polygon(
        [(W * 0.55, 0), (W, 0), (W, H * 0.45)],
        fill=(*accent_col, 40),
    )

    # Bottom bar
    draw.rectangle([(0, H - 70), (W, H)], fill=(0, 0, 0, 180))

    # --- Load font (use default if truetype not available) ---
    def _font(size: int):
        try:
            from PIL import ImageFont
            return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
        except Exception:
            try:
                return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
            except Exception:
                return ImageFont.load_default()

    # Make name (large)
    make_text = listing.make.upper()
    f_make = _font(72)
    bbox = draw.textbbox((0, 0), make_text, font=f_make)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2, 80), make_text, font=f_make, fill=(255, 255, 255, 220))

    # Model name
    model_text = listing.model
    f_model = _font(44)
    bbox = draw.textbbox((0, 0), model_text, font=f_model)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2, 175), model_text, font=f_model, fill=(220, 220, 220))

    # Year
    year_text = str(listing.year)
    f_year = _font(36)
    bbox = draw.textbbox((0, 0), year_text, font=f_year)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2, 240), year_text, font=f_year, fill=(180, 180, 180))

    # Body type tag
    if listing.body_type:
        bt = listing.body_type.upper()
        f_bt = _font(20)
        bbox = draw.textbbox((0, 0), bt, font=f_bt)
        tw = bbox[2] - bbox[0]
        pad = 12
        rx, ry = (W - tw) / 2 - pad, 295
        draw.rounded_rectangle(
            [rx, ry, rx + tw + pad * 2, ry + 36],
            radius=8,
            fill=(*accent_col, 180),
        )
        draw.text((rx + pad, ry + 8), bt, font=f_bt, fill=(255, 255, 255))

    # Bottom label
    f_label = _font(18)
    bbox = draw.textbbox((0, 0), label, font=f_label)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2, H - 50), label, font=f_label, fill=(160, 160, 160))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _upload(image_bytes: bytes, listing_id: int, slot: int) -> str | None:
    try:
        public_id = f"saudicarsale/listings/listing_{listing_id}_img{slot}"
        result = cloudinary.uploader.upload(
            io.BytesIO(image_bytes),
            public_id=public_id,
            overwrite=True,
            resource_type="image",
        )
        return result.get("public_id")
    except Exception as exc:
        return None


class Command(BaseCommand):
    help = "Generate Pillow placeholder images and upload to Cloudinary for listings without images."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None,
                            help="Process only the first N listings.")
        parser.add_argument("--overwrite", action="store_true",
                            help="Replace images even if a listing already has some.")

    def handle(self, *args, **options):
        overwrite = options["overwrite"]
        limit = options["limit"]

        has_images = set(
            ListingImage.objects.values_list("listing_id", flat=True).distinct()
        )
        qs = Listing.objects.filter(is_active=True).order_by("make", "model", "year", "id")
        if not overwrite:
            qs = qs.exclude(id__in=has_images)
        if limit:
            qs = qs[:limit]

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("All listings already have images."))
            return

        self.stdout.write(f"Generating placeholder images for {total} listings…\n")
        uploaded = 0
        errors = 0

        for idx, listing in enumerate(qs, 1):
            label = f"{listing.year} {listing.make} {listing.model}"
            self.stdout.write(f"{idx}/{total}: {label}")

            for slot, is_primary in ((0, True), (1, False)):
                slot_name = "exterior" if slot == 0 else "interior"
                self.stdout.write(f"  {slot_name}… ", ending="")
                try:
                    img_bytes = _generate_image(listing, slot)
                    public_id = _upload(img_bytes, listing.id, slot)
                    if not public_id:
                        self.stdout.write(self.style.ERROR("❌ Cloudinary upload failed"))
                        errors += 1
                        continue
                    ListingImage.objects.update_or_create(
                        listing=listing,
                        order=slot,
                        defaults={"image": public_id, "is_primary": is_primary},
                    )
                    self.stdout.write(self.style.SUCCESS("✅"))
                    uploaded += 1
                except Exception as exc:
                    self.stdout.write(self.style.ERROR(f"❌ {exc}"))
                    errors += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done!  {uploaded} images uploaded, {errors} errors."
        ))

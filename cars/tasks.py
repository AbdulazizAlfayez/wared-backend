import cloudinary.uploader
from celery import shared_task


@shared_task(name='cars.tasks.compress_image')
def compress_image(image_id):
    """
    Compress and optimize an uploaded ListingImage via Cloudinary eager transformations.
    Generates two pre-built variants: a web-optimised full size and a thumbnail.
    """
    from cars.models import ListingImage

    try:
        listing_image = ListingImage.objects.get(id=image_id)
    except ListingImage.DoesNotExist:
        return f"Image {image_id} not found"

    public_id = listing_image.image.public_id
    cloudinary.uploader.explicit(
        public_id,
        type='upload',
        eager=[
            # Full web size
            {
                'width': 800, 'height': 600, 'crop': 'limit',
                'quality': 'auto:good', 'fetch_format': 'auto',
            },
            # Thumbnail
            {
                'width': 400, 'height': 300, 'crop': 'limit',
                'quality': 'auto:good', 'fetch_format': 'auto',
            },
        ],
    )
    return f"Image {image_id} optimized"


@shared_task(name='cars.tasks.check_duplicate_vin')
def check_duplicate_vin(listing_id):
    """Placeholder: Async VIN duplicate check"""
    print(f"TODO: Check VIN for listing {listing_id}")
    return f"VIN check complete for listing {listing_id}"


@shared_task(name='cars.tasks.auto_expire_featured')
def auto_expire_featured():
    """
    Phase 4.6 — Expire active ListingPromotion records whose expires_at has passed,
    then recalculate the denormalized promotion flags on the affected Listing rows.

    Runs hourly via Celery Beat.
    """
    from django.db.models import Max
    from django.utils import timezone
    from cars.models import Listing, ListingPromotion

    now = timezone.now()

    # Find active promotions that have expired
    expired_qs = ListingPromotion.objects.filter(status='active', expires_at__lte=now)
    expired_listing_ids = list(expired_qs.values_list('listing_id', flat=True).distinct())
    expired_count = expired_qs.update(status='expired')

    # Recalculate flags for every affected listing
    for listing_id in expired_listing_ids:
        remaining = (
            ListingPromotion.objects
            .filter(listing_id=listing_id, status='active', expires_at__gt=now)
            .select_related('package')
        )

        is_featured    = remaining.filter(package__promotion_type='featured').exists()
        is_highlighted = remaining.filter(package__promotion_type='highlighted').exists()
        is_top_search  = remaining.filter(package__promotion_type='top_search').exists()
        is_homepage    = remaining.filter(package__promotion_type='homepage').exists()
        max_priority   = remaining.aggregate(mp=Max('package__priority'))['mp'] or 0
        next_expiry    = (
            remaining.order_by('expires_at').values_list('expires_at', flat=True).first()
        )

        Listing.objects.filter(pk=listing_id).update(
            is_featured=is_featured,
            is_highlighted=is_highlighted,
            is_top_search=is_top_search,
            is_homepage=is_homepage,
            promotion_priority=max_priority,
            promotion_expires_at=next_expiry,
        )

    return (
        f"auto_expire_featured: expired {expired_count} promotion(s) "
        f"across {len(expired_listing_ids)} listing(s)."
    )


@shared_task(name='cars.tasks.recalculate_view_counts')
def recalculate_view_counts():
    """
    Maintenance task: recalculates view_count and unique_view_count for every
    listing from raw ViewLog data, correcting any drift in the denormalized counters.

    Schedule via Celery Beat (e.g. nightly) to keep counters accurate.
    """
    from cars.models import Listing, ViewLog

    listing_ids = list(
        ViewLog.objects.values_list('listing_id', flat=True).distinct()
    )
    updated = 0

    for listing_id in listing_ids:
        qs    = ViewLog.objects.filter(listing_id=listing_id)
        total = qs.count()

        # Unique = distinct authenticated users + distinct anonymous IPs
        unique_users    = qs.filter(user__isnull=False).values('user_id').distinct().count()
        unique_anon_ips = qs.filter(user__isnull=True).values('ip_address').distinct().count()
        unique          = unique_users + unique_anon_ips

        Listing.objects.filter(pk=listing_id).update(
            view_count=total,
            unique_view_count=unique,
        )
        updated += 1

    return f"Recalculated view counts for {updated} listing(s)"


@shared_task(bind=True, name='cars.tasks.process_bulk_upload')
def process_bulk_upload(self, bulk_upload_id):
    """
    Parse a dealer-uploaded CSV and create Listing records.

    CSV columns (all required unless noted):
      make, model, year, price, mileage, city, title,
      fuel_type, transmission, condition, body_type (optional),
      description (optional), color (optional), vin (optional)
    """
    import csv
    import io
    from django.utils import timezone

    from cars.models import BulkUpload, Listing

    try:
        bulk = BulkUpload.objects.get(pk=bulk_upload_id)
    except BulkUpload.DoesNotExist:
        return f"BulkUpload {bulk_upload_id} not found"

    bulk.status = 'processing'
    bulk.save(update_fields=['status'])

    REQUIRED = {'make', 'model', 'year', 'price', 'mileage', 'city', 'title'}

    errors = []
    successful = 0
    total = 0

    try:
        bulk.file.seek(0)
        content = bulk.file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(content))

        # Validate headers
        if reader.fieldnames is None:
            raise ValueError("CSV file is empty or has no header row.")

        headers = {h.strip().lower() for h in reader.fieldnames}
        missing = REQUIRED - headers
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

        rows = list(reader)
        total = len(rows)
        bulk.total_rows = total
        bulk.save(update_fields=['total_rows'])

        for i, row in enumerate(rows, start=2):  # row 1 = header
            row_errors = []
            # Normalise keys
            row = {k.strip().lower(): v.strip() for k, v in row.items() if k}

            # --- Required field validation ---
            for field in REQUIRED:
                if not row.get(field):
                    row_errors.append(f"'{field}' is required")

            # Type coercions
            try:
                year = int(row.get('year', 0))
                if not (1900 <= year <= 2100):
                    row_errors.append("'year' must be between 1900 and 2100")
            except ValueError:
                row_errors.append("'year' must be an integer")
                year = None

            try:
                price = float(row.get('price', ''))
                if price < 0:
                    row_errors.append("'price' must be non-negative")
            except ValueError:
                row_errors.append("'price' must be a number")
                price = None

            try:
                mileage = int(row.get('mileage', ''))
                if mileage < 0:
                    row_errors.append("'mileage' must be non-negative")
            except ValueError:
                row_errors.append("'mileage' must be an integer")
                mileage = None

            # Choice validation
            fuel_type = row.get('fuel_type', '').lower()
            valid_fuels = {c[0] for c in Listing.FUEL_TYPE_CHOICES}
            if fuel_type and fuel_type not in valid_fuels:
                row_errors.append(f"'fuel_type' must be one of: {', '.join(sorted(valid_fuels))}")

            transmission = row.get('transmission', '').lower()
            valid_trans = {c[0] for c in Listing.TRANSMISSION_CHOICES}
            if transmission and transmission not in valid_trans:
                row_errors.append(f"'transmission' must be one of: {', '.join(sorted(valid_trans))}")

            condition = row.get('condition', '').lower()
            valid_cond = {c[0] for c in Listing.CONDITION_CHOICES}
            if condition and condition not in valid_cond:
                row_errors.append(f"'condition' must be one of: {', '.join(sorted(valid_cond))}")

            if row_errors:
                errors.append({'row': i, 'errors': row_errors})
                continue

            # --- VIN uniqueness ---
            vin = row.get('vin') or None
            if vin and Listing.objects.filter(vin=vin).exists():
                errors.append({'row': i, 'errors': [f"VIN '{vin}' already exists"]})
                continue

            try:
                Listing.objects.create(
                    owner=bulk.dealer,
                    title=row['title'],
                    make=row['make'],
                    model=row['model'],
                    year=year,
                    price=price,
                    mileage=mileage,
                    city=row['city'],
                    fuel_type=fuel_type or 'petrol',
                    transmission=transmission or 'automatic',
                    condition=condition or 'used',
                    body_type=row.get('body_type', '').lower() or '',
                    description=row.get('description', ''),
                    color=row.get('color', ''),
                    vin=vin,
                    status='draft',
                )
                successful += 1
            except Exception as exc:
                errors.append({'row': i, 'errors': [str(exc)]})

    except Exception as exc:
        bulk.status = 'failed'
        bulk.errors = [{'row': 'N/A', 'errors': [str(exc)]}]
        bulk.completed_at = timezone.now()
        bulk.save(update_fields=['status', 'errors', 'completed_at'])
        return f"BulkUpload {bulk_upload_id} failed: {exc}"

    bulk.successful_rows = successful
    bulk.failed_rows = total - successful
    bulk.errors = errors
    bulk.status = 'done'
    bulk.completed_at = timezone.now()
    bulk.save(update_fields=[
        'successful_rows', 'failed_rows', 'errors', 'status', 'completed_at',
    ])

    # Send completion email (best-effort)
    try:
        from django.core.mail import send_mail
        from django.template.loader import render_to_string
        from django.conf import settings as django_settings

        email = bulk.dealer.email
        if email:
            body = render_to_string('emails/bulk_upload_complete.html', {
                'dealer':     bulk.dealer,
                'bulk':       bulk,
                'successful': successful,
                'failed':     total - successful,
                'total':      total,
            })
            send_mail(
                subject='Bulk upload complete — WARED',
                message='',
                html_message=body,
                from_email=getattr(django_settings, 'DEFAULT_FROM_EMAIL', 'noreply@wared.sa'),
                recipient_list=[email],
                fail_silently=True,
            )
    except Exception:
        pass

    return (
        f"BulkUpload {bulk_upload_id} done: "
        f"{successful}/{total} rows created, {total - successful} failed"
    )

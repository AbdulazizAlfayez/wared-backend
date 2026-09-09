"""
Management command: seed_demo_data
-----------------------------------
Seeds realistic demo data for local development:
  - 3 verified importer users with ImporterProfiles
  - 12 imported-car Listings (mixed import statuses)
  - 1 verified test buyer
  - 2 orders with timeline entries

Idempotent — running twice does NOT duplicate data.

Usage:
    python manage.py seed_demo_data
    python manage.py seed_demo_data --clear   # wipe seeded data first
"""

import random
import string
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from cars.models import Listing, ListingImage
from importers.models import ImporterProfile
from orders.models import ImportOrder, ImportTimeline

User = get_user_model()

# ---------------------------------------------------------------------------
# Tag used in admin_notes to identify seeded data for cleanup
# ---------------------------------------------------------------------------
SEED_TAG = "SEED_DEMO_V2"

# ---------------------------------------------------------------------------
# Cloudinary public IDs already uploaded (reuse, no uploads needed)
# ---------------------------------------------------------------------------
CLOUD_IMAGES = [
    "image/upload/listings/demo/wzfek83mcfqkosiviwil",
    "image/upload/listings/demo/aua12qfwbbthrsfib2ov",
    "image/upload/listings/demo/aypetzbpawqrxkv4dbsm",
    "image/upload/listings/demo/cyetvkz5ytpmvfo08yvw",
    "image/upload/listings/demo/e38rwc4khaswediivowj",
    "image/upload/listings/demo/isaldsjlpmtp9sifpixv",
    "image/upload/listings/demo/m2xtak9wo6zxezufugnj",
    "image/upload/listings/demo/mawkuxlncvkxvno9i0t0",
    "image/upload/listings/demo/mkyxjevez4xab9esuvne",
    "image/upload/listings/demo/nzjvb0cf0xthfos5h4a8",
    "image/upload/listings/demo/offjsnq3y5wobpingk2m",
    "image/upload/listings/demo/otsedrpxg2ub9edkp5e3",
]

# ---------------------------------------------------------------------------
# Importer definitions
# ---------------------------------------------------------------------------
IMPORTERS = [
    {
        "email": "importer.riyadh@wared.test",
        "name": "واردات الرشيد | Al-Rashid Imports",
        "phone": "+966501234001",
        "profile": {
            "business_name": "واردات الرشيد | Al-Rashid Imports",
            "business_name_ar": "واردات الرشيد",
            "commercial_registration": "1010123456",
            "customs_broker_license": "CB-2021-0012",
            "specializations": ["USA Cars", "Copart Auctions", "Muscle Cars"],
            "source_countries": ["usa", "canada"],
            "years_in_business": 8,
            "total_cars_imported": 340,
            "success_rate": Decimal("97.5"),
            "average_delivery_days": 55,
            "description": "Specializing in American imports — Copart, IAAI auctions. Full transparency, Carfax reports, live shipment tracking.",
            "description_ar": "متخصصون في استيراد السيارات الأمريكية من مزادات كوبارت وIAAI مع تقارير كارفاكس وتتبع الشحنات.",
            "phone": "+966501234001",
            "whatsapp": "+966501234001",
            "email": "info@alrashid-imports.test",
            "address": "Al-Olaya District, Riyadh",
            "address_ar": "حي العليا، الرياض",
            "is_verified": True,
            "average_rating": Decimal("4.8"),
            "total_reviews": 87,
        },
    },
    {
        "email": "importer.jeddah@wared.test",
        "name": "معرض جدة للسيارات | Jeddah Auto Gallery",
        "phone": "+966512345002",
        "profile": {
            "business_name": "معرض جدة للسيارات | Jeddah Auto Gallery",
            "business_name_ar": "معرض جدة للسيارات",
            "commercial_registration": "4030234567",
            "customs_broker_license": "CB-2019-0034",
            "specializations": ["European Luxury", "BMW", "Mercedes-Benz", "Porsche"],
            "source_countries": ["europe"],
            "years_in_business": 11,
            "total_cars_imported": 520,
            "success_rate": Decimal("98.0"),
            "average_delivery_days": 45,
            "description": "Premium European vehicle imports — BMW, Mercedes, Porsche. Direct from German auctions and dealers.",
            "description_ar": "استيراد سيارات أوروبية فاخرة — بي إم دبليو، مرسيدس، بورش. مباشرة من المزادات والوكلاء الألمان.",
            "phone": "+966512345002",
            "whatsapp": "+966512345002",
            "email": "info@jeddah-auto.test",
            "address": "Al-Andalus District, Jeddah",
            "address_ar": "حي الأندلس، جدة",
            "is_verified": True,
            "average_rating": Decimal("4.9"),
            "total_reviews": 143,
        },
    },
    {
        "email": "importer.dammam@wared.test",
        "name": "سيارات الدمام اليابانية | Dammam JDM Cars",
        "phone": "+966551234003",
        "profile": {
            "business_name": "سيارات الدمام اليابانية | Dammam JDM Cars",
            "business_name_ar": "سيارات الدمام اليابانية",
            "commercial_registration": "2050345678",
            "customs_broker_license": "CB-2020-0056",
            "specializations": ["Japanese Cars", "Toyota", "Lexus", "Nissan"],
            "source_countries": ["japan", "korea"],
            "years_in_business": 6,
            "total_cars_imported": 210,
            "success_rate": Decimal("96.5"),
            "average_delivery_days": 60,
            "description": "Japanese and Korean car specialists — Toyota, Lexus, Hyundai, Kia. USS Japan auctions.",
            "description_ar": "متخصصون في السيارات اليابانية والكورية — تويوتا، لكزس، هيونداي، كيا.",
            "phone": "+966551234003",
            "whatsapp": "+966551234003",
            "email": "info@dammam-jdm.test",
            "address": "Al-Faisaliah District, Dammam",
            "address_ar": "حي الفيصلية، الدمام",
            "is_verified": True,
            "average_rating": Decimal("4.7"),
            "total_reviews": 64,
        },
    },
]

# ---------------------------------------------------------------------------
# Car definitions (12 cars across import statuses)
# ---------------------------------------------------------------------------
CARS = [
    # 6 available
    {"make": "Toyota", "model": "Land Cruiser", "year": 2024, "price": 385000, "mileage": 8000, "source": "uae", "spec": "gcc", "body": "suv", "status": "available", "importer_idx": 2},
    {"make": "BMW", "model": "X5 M60i", "year": 2024, "price": 420000, "mileage": 5200, "source": "europe", "spec": "european", "body": "suv", "status": "available", "importer_idx": 1},
    {"make": "Ford", "model": "Mustang GT", "year": 2023, "price": 195000, "mileage": 18000, "source": "usa", "spec": "american", "body": "coupe", "status": "available", "importer_idx": 0},
    {"make": "Lexus", "model": "LX 600", "year": 2024, "price": 480000, "mileage": 3500, "source": "japan", "spec": "japanese", "body": "suv", "status": "available", "importer_idx": 2},
    {"make": "Mercedes-Benz", "model": "G 63 AMG", "year": 2023, "price": 750000, "mileage": 12000, "source": "europe", "spec": "european", "body": "suv", "status": "available", "importer_idx": 1},
    {"make": "Chevrolet", "model": "Tahoe RST", "year": 2024, "price": 245000, "mileage": 9500, "source": "usa", "spec": "american", "body": "suv", "status": "available", "importer_idx": 0},
    # 2 shipping
    {"make": "Toyota", "model": "Supra GR", "year": 2024, "price": 310000, "mileage": 2800, "source": "japan", "spec": "japanese", "body": "coupe", "status": "shipping", "importer_idx": 2},
    {"make": "Range Rover", "model": "Sport HSE", "year": 2023, "price": 520000, "mileage": 15000, "source": "europe", "spec": "european", "body": "suv", "status": "shipping", "importer_idx": 1},
    # 2 reserved
    {"make": "Dodge", "model": "Charger Hellcat", "year": 2023, "price": 340000, "mileage": 11000, "source": "usa", "spec": "american", "body": "sedan", "status": "reserved", "importer_idx": 0},
    {"make": "Hyundai", "model": "Ioniq 5 N", "year": 2024, "price": 215000, "mileage": 1200, "source": "korea", "spec": "korean", "body": "suv", "status": "reserved", "importer_idx": 2},
    # 1 at_port
    {"make": "Porsche", "model": "Cayenne Turbo GT", "year": 2024, "price": 890000, "mileage": 4800, "source": "europe", "spec": "european", "body": "suv", "status": "at_port", "importer_idx": 1},
    # 1 ready_for_delivery
    {"make": "Nissan", "model": "GT-R Nismo", "year": 2023, "price": 680000, "mileage": 6500, "source": "japan", "spec": "japanese", "body": "coupe", "status": "ready_for_delivery", "importer_idx": 2},
]

# ---------------------------------------------------------------------------
# Buyer
# ---------------------------------------------------------------------------
BUYER = {
    "email": "buyer@wared.test",
    "name": "Ahmed Al-Saud",
    "password": "BuyerTest2026",
}


def _vin():
    """Random 17-char VIN."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=17))


class Command(BaseCommand):
    help = "Seed demo data for local testing (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Remove seeded data before re-seeding.")

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()

        if options["clear"]:
            self._clear()

        # ── Importers ─────────────────────────────────────────────────────
        importer_users = []
        for imp_def in IMPORTERS:
            user, created = User.objects.get_or_create(
                email=imp_def["email"],
                defaults={
                    "name": imp_def["name"],
                    "phone": imp_def["phone"],
                    "role": "importer",
                    "is_email_verified": True,
                    "email_verified_at": now,
                },
            )
            if created:
                user.set_password("ImporterTest2026")
                user.save(update_fields=["password"])

            profile, _ = ImporterProfile.objects.get_or_create(
                user=user,
                defaults=imp_def["profile"],
            )
            if not _:
                # Update existing profile fields
                for k, v in imp_def["profile"].items():
                    setattr(profile, k, v)
                profile.save()

            importer_users.append(user)
            tag = "created" if created else "exists"
            self.stdout.write(f"  Importer: {user.email} [{tag}]")

        # ── Buyer ─────────────────────────────────────────────────────────
        buyer, buyer_created = User.objects.get_or_create(
            email=BUYER["email"],
            defaults={
                "name": BUYER["name"],
                "role": "user",
                "is_email_verified": True,
                "email_verified_at": now,
            },
        )
        if buyer_created:
            buyer.set_password(BUYER["password"])
            buyer.save(update_fields=["password"])
        self.stdout.write(f"  Buyer: {buyer.email} [{'created' if buyer_created else 'exists'}]")

        # ── Cars ──────────────────────────────────────────────────────────
        created_listings = []
        for idx, car in enumerate(CARS):
            owner = importer_users[car["importer_idx"]]
            title = f"{car['year']} {car['make']} {car['model']}"
            price = Decimal(str(car["price"]))
            source_price = price * Decimal("0.6")
            shipping = Decimal("6000")
            customs = price * Decimal("0.05")
            vat = (source_price + customs + shipping) * Decimal("0.15")

            listing, l_created = Listing.objects.get_or_create(
                title=title,
                owner=owner,
                defaults={
                    "make": car["make"],
                    "model": car["model"],
                    "year": car["year"],
                    "price": price,
                    "final_price_sar": price,
                    "mileage": car["mileage"],
                    "city": "Riyadh",
                    "body_type": car["body"],
                    "fuel_type": "petrol",
                    "transmission": "automatic",
                    "condition": "used",
                    "source_country": car["source"],
                    "spec_origin": car["spec"],
                    "gcc_specs": car["spec"] == "gcc",
                    "import_status": car["status"],
                    "source_price": source_price,
                    "source_currency": "usd",
                    "shipping_cost": shipping,
                    "customs_duty_amount": customs,
                    "vat_amount": vat,
                    "inspection_fee": Decimal("1200"),
                    "port_of_entry": "jeddah_islamic_port",
                    "vin": _vin(),
                    "status": "approved",
                    "is_active": True,
                    "admin_notes": SEED_TAG,
                    "description": f"{title} — imported from {car['source'].upper()}. {car['spec'].capitalize()} spec, {car['mileage']:,} km.",
                },
            )

            if l_created:
                # Assign an image from the pool
                img_id = CLOUD_IMAGES[idx % len(CLOUD_IMAGES)]
                ListingImage.objects.get_or_create(
                    listing=listing,
                    image=img_id,
                    defaults={"is_primary": True, "order": 0},
                )
                created_listings.append(listing)

            self.stdout.write(f"  Car: {title} [{car['status']}] [{'created' if l_created else 'exists'}]")

        # ── Orders (for buyer) ────────────────────────────────────────────
        # Order 1: confirmed, linked to first available car
        available_cars = [c for c in created_listings if c.import_status == "available"]
        if len(available_cars) >= 1:
            order1, o1_created = ImportOrder.objects.get_or_create(
                buyer=buyer,
                car=available_cars[0],
                defaults={
                    "importer": available_cars[0].owner,
                    "status": "confirmed",
                    "total_price": available_cars[0].final_price_sar,
                    "remaining_balance": available_cars[0].final_price_sar,
                    "deposit_amount": Decimal("5000"),
                    "deposit_paid": True,
                    "deposit_paid_at": now - timedelta(days=3),
                },
            )
            if o1_created:
                for evt in [
                    ("order_placed", "تم إنشاء الطلب", -3),
                    ("deposit_paid", "تم دفع العربون", -3),
                    ("order_confirmed", "تم تأكيد الطلب", -2),
                ]:
                    ImportTimeline.objects.get_or_create(
                        order=order1,
                        event_type=evt[0],
                        defaults={
                            "title": evt[1],
                            "date": now + timedelta(days=evt[2]),
                            "is_public": True,
                        },
                    )
            self.stdout.write(f"  Order: {order1.order_number} [confirmed] [{'created' if o1_created else 'exists'}]")

        # Order 2: shipped, linked to a different car
        if len(available_cars) >= 2:
            order2, o2_created = ImportOrder.objects.get_or_create(
                buyer=buyer,
                car=available_cars[1],
                defaults={
                    "importer": available_cars[1].owner,
                    "status": "shipped",
                    "total_price": available_cars[1].final_price_sar,
                    "remaining_balance": available_cars[1].final_price_sar,
                    "deposit_amount": Decimal("5000"),
                    "deposit_paid": True,
                    "deposit_paid_at": now - timedelta(days=10),
                },
            )
            if o2_created:
                for evt in [
                    ("order_placed", "تم إنشاء الطلب", -10),
                    ("deposit_paid", "تم دفع العربون", -10),
                    ("order_confirmed", "تم تأكيد الطلب", -9),
                    ("sourcing_started", "بدأ البحث عن السيارة", -8),
                    ("car_purchased", "تم شراء السيارة", -6),
                    ("shipped", "تم شحن السيارة", -3),
                ]:
                    ImportTimeline.objects.get_or_create(
                        order=order2,
                        event_type=evt[0],
                        defaults={
                            "title": evt[1],
                            "date": now + timedelta(days=evt[2]),
                            "is_public": True,
                        },
                    )
            self.stdout.write(f"  Order: {order2.order_number} [shipped] [{'created' if o2_created else 'exists'}]")

        # ── Summary ───────────────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Seed Summary ==="))
        self.stdout.write(f"  Importers:  {ImporterProfile.objects.filter(is_verified=True).count()}")
        for st in ["available", "shipping", "reserved", "at_port", "ready_for_delivery"]:
            c = Listing.objects.filter(import_status=st, is_active=True).count()
            self.stdout.write(f"  Cars [{st}]: {c}")
        self.stdout.write(f"  Orders:     {ImportOrder.objects.count()}")
        self.stdout.write(f"  Buyer:      {BUYER['email']} / {BUYER['password']}")

    def _clear(self):
        """Remove seeded data (identified by SEED_TAG in admin_notes)."""
        seeded = Listing.objects.filter(admin_notes=SEED_TAG)
        count = seeded.count()
        # Delete orders linked to seeded listings
        ImportOrder.objects.filter(car__in=seeded).delete()
        ListingImage.objects.filter(listing__in=seeded).delete()
        seeded.delete()
        # Remove seeded users (but not existing accounts)
        seed_emails = [i["email"] for i in IMPORTERS] + [BUYER["email"]]
        User.objects.filter(email__in=seed_emails).delete()
        self.stdout.write(self.style.WARNING(f"  Cleared {count} seeded listings + associated users/orders."))

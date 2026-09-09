"""
Management command: python manage.py load_arabic_makes

Populates make_ar on existing Listing rows where make matches a known brand.
Safe to run multiple times (idempotent per listing).
"""

from django.core.management.base import BaseCommand

from cars.models import Listing

# Arabic translations for common car brands sold in Saudi Arabia.
MAKE_AR_MAP = {
    'Toyota':       'تويوتا',
    'Nissan':       'نيسان',
    'Hyundai':      'هيونداي',
    'Honda':        'هوندا',
    'Chevrolet':    'شيفروليه',
    'Ford':         'فورد',
    'BMW':          'بي إم دبليو',
    'Mercedes':     'مرسيدس',
    'Mercedes-Benz':'مرسيدس بنز',
    'Lexus':        'لكزس',
    'GMC':          'جي إم سي',
    'Kia':          'كيا',
    'Mazda':        'مازدا',
    'Mitsubishi':   'ميتسوبيشي',
    'Jeep':         'جيب',
    'Land Rover':   'لاند روفر',
    'Audi':         'أودي',
    'Volkswagen':   'فولكس واجن',
    'Porsche':      'بورشه',
    'Infiniti':     'إنفينيتي',
    'Cadillac':     'كاديلاك',
    'Dodge':        'دودج',
    'Chrysler':     'كرايسلر',
    'Isuzu':        'إيسوزو',
    'Suzuki':       'سوزوكي',
    'Geely':        'جيلي',
    'Changan':      'شانجان',
    'Haval':        'هافال',
    'MG':           'إم جي',
    'Chery':        'شيري',
    'JAC':          'جاك',
    'Jetour':       'جيتور',
    'Tank':         'تانك',
    'Subaru':       'سوبارو',
    'Volvo':        'فولفو',
    'Renault':      'رينو',
    'Peugeot':      'بيجو',
    'Ferrari':      'فيراري',
    'Lamborghini':  'لامبورغيني',
    'Maserati':     'مازيراتي',
    'Bentley':      'بنتلي',
    'Rolls-Royce':  'رولز رويس',
}


class Command(BaseCommand):
    help = 'Populate make_ar on Listing rows that match a known brand in MAKE_AR_MAP.'

    def handle(self, *args, **options):
        total_updated = 0

        for make_en, make_ar in MAKE_AR_MAP.items():
            updated = Listing.objects.filter(
                make__iexact=make_en,
            ).exclude(
                make_ar=make_ar,  # skip rows already correct
            ).update(make_ar=make_ar)

            if updated:
                self.stdout.write(
                    f"  {make_en} → {make_ar} : {updated} listing(s) updated"
                )
                total_updated += updated

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {total_updated} listing(s) had make_ar set."
            )
        )

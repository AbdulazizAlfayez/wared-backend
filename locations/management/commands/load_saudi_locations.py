"""
Management command: python manage.py load_saudi_locations

Idempotent — safe to run multiple times (uses get_or_create).
Loads all 13 Saudi regions with their major cities and approximate coordinates.
"""
from django.core.management.base import BaseCommand

from locations.models import City, Region


REGIONS_DATA = [
    {
        'name_en': 'Riyadh',
        'name_ar': 'الرياض',
        'slug': 'riyadh',
        'cities': [
            {'name_en': 'Riyadh',           'name_ar': 'الرياض',       'slug': 'riyadh',            'lat': '24.713600', 'lng': '46.675300'},
            {'name_en': 'Al Kharj',          'name_ar': 'الخرج',        'slug': 'al-kharj',          'lat': '24.155200', 'lng': '47.314700'},
            {'name_en': 'Ad Dawadimi',        'name_ar': 'الدوادمي',     'slug': 'ad-dawadimi',       'lat': '24.500000', 'lng': '44.400000'},
            {'name_en': "Al Majma'ah",        'name_ar': 'المجمعة',      'slug': 'al-majmaah',        'lat': '25.900000', 'lng': '45.350000'},
            {'name_en': 'Wadi ad-Dawasir',    'name_ar': 'وادي الدواسر', 'slug': 'wadi-ad-dawasir',   'lat': '20.456100', 'lng': '45.015000'},
        ],
    },
    {
        'name_en': 'Makkah',
        'name_ar': 'مكة المكرمة',
        'slug': 'makkah',
        'cities': [
            {'name_en': 'Makkah',            'name_ar': 'مكة',          'slug': 'makkah-city',       'lat': '21.389100', 'lng': '39.857900'},
            {'name_en': 'Jeddah',            'name_ar': 'جدة',          'slug': 'jeddah',            'lat': '21.543300', 'lng': '39.172800'},
            {'name_en': 'Taif',              'name_ar': 'الطائف',       'slug': 'taif',              'lat': '21.270300', 'lng': '40.415800'},
            {'name_en': 'Rabigh',            'name_ar': 'رابغ',         'slug': 'rabigh',            'lat': '22.802800', 'lng': '39.031700'},
            {'name_en': 'Al Qunfudhah',      'name_ar': 'القنفذة',      'slug': 'al-qunfudhah',      'lat': '19.126800', 'lng': '41.079600'},
        ],
    },
    {
        'name_en': 'Eastern Province',
        'name_ar': 'المنطقة الشرقية',
        'slug': 'eastern-province',
        'cities': [
            {'name_en': 'Dammam',            'name_ar': 'الدمام',       'slug': 'dammam',            'lat': '26.392700', 'lng': '49.977700'},
            {'name_en': 'Dhahran',           'name_ar': 'الظهران',      'slug': 'dhahran',           'lat': '26.287600', 'lng': '50.050400'},
            {'name_en': 'Al Khobar',         'name_ar': 'الخبر',        'slug': 'al-khobar',         'lat': '26.217200', 'lng': '50.197100'},
            {'name_en': 'Jubail',            'name_ar': 'الجبيل',       'slug': 'jubail',            'lat': '27.017400', 'lng': '49.658000'},
            {'name_en': 'Hafar Al-Batin',    'name_ar': 'حفر الباطن',   'slug': 'hafar-al-batin',    'lat': '28.433300', 'lng': '45.966700'},
            {'name_en': 'Al Ahsa',           'name_ar': 'الأحساء',      'slug': 'al-ahsa',           'lat': '25.385400', 'lng': '49.586800'},
            {'name_en': 'Qatif',             'name_ar': 'القطيف',       'slug': 'qatif',             'lat': '26.520000', 'lng': '50.010000'},
        ],
    },
    {
        'name_en': 'Madinah',
        'name_ar': 'المدينة المنورة',
        'slug': 'madinah',
        'cities': [
            {'name_en': 'Madinah',           'name_ar': 'المدينة',      'slug': 'madinah-city',      'lat': '24.453900', 'lng': '39.614200'},
            {'name_en': 'Yanbu',             'name_ar': 'ينبع',         'slug': 'yanbu',             'lat': '24.087800', 'lng': '38.063800'},
            {'name_en': 'Al Ula',            'name_ar': 'العلا',        'slug': 'al-ula',            'lat': '26.621000', 'lng': '37.922600'},
            {'name_en': 'Badr',              'name_ar': 'بدر',          'slug': 'badr',              'lat': '23.783300', 'lng': '38.783300'},
        ],
    },
    {
        'name_en': 'Asir',
        'name_ar': 'عسير',
        'slug': 'asir',
        'cities': [
            {'name_en': 'Abha',              'name_ar': 'أبها',         'slug': 'abha',              'lat': '18.216400', 'lng': '42.505300'},
            {'name_en': 'Khamis Mushait',    'name_ar': 'خميس مشيط',   'slug': 'khamis-mushait',    'lat': '18.303800', 'lng': '42.728900'},
            {'name_en': 'Bisha',             'name_ar': 'بيشة',         'slug': 'bisha',             'lat': '19.998500', 'lng': '42.604600'},
            {'name_en': 'An Namas',          'name_ar': 'النماص',       'slug': 'an-namas',          'lat': '19.123500', 'lng': '42.125400'},
        ],
    },
    {
        'name_en': 'Qassim',
        'name_ar': 'القصيم',
        'slug': 'qassim',
        'cities': [
            {'name_en': 'Buraydah',          'name_ar': 'بريدة',        'slug': 'buraydah',          'lat': '26.366700', 'lng': '43.966700'},
            {'name_en': 'Unaizah',           'name_ar': 'عنيزة',        'slug': 'unaizah',           'lat': '26.088700', 'lng': '43.994700'},
            {'name_en': 'Ar Rass',           'name_ar': 'الرس',         'slug': 'ar-rass',           'lat': '25.866800', 'lng': '43.491700'},
        ],
    },
    {
        'name_en': 'Tabuk',
        'name_ar': 'تبوك',
        'slug': 'tabuk',
        'cities': [
            {'name_en': 'Tabuk',             'name_ar': 'تبوك',         'slug': 'tabuk-city',        'lat': '28.383800', 'lng': '36.566200'},
            {'name_en': 'Duba',              'name_ar': 'ضبا',          'slug': 'duba',              'lat': '27.350000', 'lng': '35.696700'},
            {'name_en': 'Haql',              'name_ar': 'حقل',          'slug': 'haql',              'lat': '29.295000', 'lng': '34.940700'},
            {'name_en': 'Tayma',             'name_ar': 'تيماء',        'slug': 'tayma',             'lat': '27.616700', 'lng': '38.533300'},
        ],
    },
    {
        'name_en': "Ha'il",
        'name_ar': 'حائل',
        'slug': 'hail',
        'cities': [
            {'name_en': "Ha'il",             'name_ar': 'حائل',         'slug': 'hail-city',         'lat': '27.511400', 'lng': '41.690000'},
        ],
    },
    {
        'name_en': 'Jazan',
        'name_ar': 'جازان',
        'slug': 'jazan',
        'cities': [
            {'name_en': 'Jazan',             'name_ar': 'جازان',        'slug': 'jazan-city',        'lat': '16.889200', 'lng': '42.551100'},
            {'name_en': 'Sabya',             'name_ar': 'صبيا',         'slug': 'sabya',             'lat': '17.153700', 'lng': '42.625700'},
            {'name_en': 'Abu Arish',         'name_ar': 'أبو عريش',     'slug': 'abu-arish',         'lat': '16.970000', 'lng': '42.830000'},
        ],
    },
    {
        'name_en': 'Najran',
        'name_ar': 'نجران',
        'slug': 'najran',
        'cities': [
            {'name_en': 'Najran',            'name_ar': 'نجران',        'slug': 'najran-city',       'lat': '17.492400', 'lng': '44.127700'},
            {'name_en': 'Sharurah',          'name_ar': 'شرورة',        'slug': 'sharurah',          'lat': '17.485100', 'lng': '47.106500'},
        ],
    },
    {
        'name_en': 'Al Bahah',
        'name_ar': 'الباحة',
        'slug': 'al-bahah',
        'cities': [
            {'name_en': 'Al Bahah',          'name_ar': 'الباحة',       'slug': 'al-bahah-city',     'lat': '20.012900', 'lng': '41.467700'},
            {'name_en': 'Baljurashi',        'name_ar': 'بلجرشي',       'slug': 'baljurashi',        'lat': '20.300000', 'lng': '41.550000'},
        ],
    },
    {
        'name_en': 'Northern Borders',
        'name_ar': 'الحدود الشمالية',
        'slug': 'northern-borders',
        'cities': [
            {'name_en': 'Arar',              'name_ar': 'عرعر',         'slug': 'arar',              'lat': '30.975000', 'lng': '41.018300'},
            {'name_en': 'Rafha',             'name_ar': 'رفحاء',        'slug': 'rafha',             'lat': '29.626800', 'lng': '43.494600'},
            {'name_en': 'Turaif',            'name_ar': 'طريف',         'slug': 'turaif',            'lat': '31.675300', 'lng': '38.655800'},
        ],
    },
    {
        'name_en': 'Al Jawf',
        'name_ar': 'الجوف',
        'slug': 'al-jawf',
        'cities': [
            {'name_en': 'Sakaka',            'name_ar': 'سكاكا',        'slug': 'sakaka',            'lat': '29.969700', 'lng': '40.200100'},
            {'name_en': 'Dumat Al-Jandal',   'name_ar': 'دومة الجندل',  'slug': 'dumat-al-jandal',   'lat': '29.816700', 'lng': '39.866700'},
            {'name_en': 'Al Qurayyat',       'name_ar': 'القريات',      'slug': 'al-qurayyat',       'lat': '31.329200', 'lng': '37.350000'},
        ],
    },
]


class Command(BaseCommand):
    help = 'Load all 13 Saudi regions and their major cities (idempotent).'

    def handle(self, *args, **options):
        regions_created = 0
        cities_created  = 0

        for r_data in REGIONS_DATA:
            region, created = Region.objects.get_or_create(
                slug=r_data['slug'],
                defaults={
                    'name_en': r_data['name_en'],
                    'name_ar': r_data['name_ar'],
                },
            )
            if created:
                regions_created += 1
                self.stdout.write(f'  + Region: {region.name_en}')

            for c_data in r_data['cities']:
                city, created = City.objects.get_or_create(
                    slug=c_data['slug'],
                    defaults={
                        'region':    region,
                        'name_en':   c_data['name_en'],
                        'name_ar':   c_data['name_ar'],
                        'latitude':  c_data['lat'],
                        'longitude': c_data['lng'],
                    },
                )
                if created:
                    cities_created += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. Created {regions_created} region(s) and {cities_created} city/cities.\n'
            f'Total in DB: {Region.objects.count()} regions, {City.objects.count()} cities.'
        ))

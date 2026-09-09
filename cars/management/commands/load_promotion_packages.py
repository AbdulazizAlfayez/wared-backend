from django.core.management.base import BaseCommand

from cars.models import PromotionPackage

PACKAGES = [
    {
        'name': '3-Day Boost',
        'slug': '3-day-boost',
        'description': 'Highlight your listing for 3 days to get more visibility in search results.',
        'duration_days': 3,
        'price': 49,
        'promotion_type': 'highlighted',
        'priority': 1,
        'display_order': 1,
    },
    {
        'name': '7-Day Featured',
        'slug': '7-day-featured',
        'description': 'Feature your listing on the homepage for 7 days.',
        'duration_days': 7,
        'price': 99,
        'promotion_type': 'featured',
        'priority': 2,
        'display_order': 2,
    },
    {
        'name': '7-Day Top Search',
        'slug': '7-day-top-search',
        'description': 'Pin your listing at the top of search results for 7 days.',
        'duration_days': 7,
        'price': 149,
        'promotion_type': 'top_search',
        'priority': 3,
        'display_order': 3,
    },
    {
        'name': '30-Day Featured',
        'slug': '30-day-featured',
        'description': 'Feature your listing on the homepage for 30 days.',
        'duration_days': 30,
        'price': 249,
        'promotion_type': 'featured',
        'priority': 2,
        'display_order': 4,
    },
    {
        'name': '30-Day Premium',
        'slug': '30-day-premium',
        'description': 'Large homepage banner placement for 30 days — maximum visibility.',
        'duration_days': 30,
        'price': 449,
        'promotion_type': 'homepage',
        'priority': 4,
        'display_order': 5,
    },
]


class Command(BaseCommand):
    help = 'Load default promotion packages (idempotent — safe to run multiple times)'

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for pkg_data in PACKAGES:
            _, created = PromotionPackage.objects.update_or_create(
                slug=pkg_data['slug'],
                defaults=pkg_data,
            )
            if created:
                created_count += 1
                self.stdout.write(f"  Created: {pkg_data['name']}")
            else:
                updated_count += 1
                self.stdout.write(f"  Updated: {pkg_data['name']}")

        self.stdout.write(
            self.style.SUCCESS(
                f'\nDone — {created_count} created, {updated_count} updated.'
            )
        )

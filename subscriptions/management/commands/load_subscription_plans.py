from django.core.management.base import BaseCommand

from subscriptions.models import SubscriptionPlan

PLANS = [
    {
        'name':                  'Free',
        'slug':                  'free',
        'description':           'Get started with basic listings at no cost.',
        'description_ar':        'ابدأ بنشر إعلاناتك مجاناً.',
        'monthly_price':         0,
        'annual_price':          0,
        'max_listings':          5,
        'max_images_per_listing': 5,
        'max_featured_listings': 0,
        'can_access_analytics':  False,
        'can_bulk_upload':       False,
        'can_have_showroom':     False,
        'can_have_workshop':     False,
        'priority_support':      False,
        'badge_type':            '',
        'display_order':         0,
    },
    {
        'name':                  'Basic',
        'slug':                  'basic',
        'description':           'For growing dealers who need more listings and analytics.',
        'description_ar':        'للتجار الذين يحتاجون إلى مزيد من الإعلانات والتحليلات.',
        'monthly_price':         99,
        'annual_price':          990,
        'max_listings':          50,
        'max_images_per_listing': 15,
        'max_featured_listings': 3,
        'can_access_analytics':  True,
        'can_bulk_upload':       False,
        'can_have_showroom':     True,
        'can_have_workshop':     True,
        'priority_support':      False,
        'badge_type':            'basic',
        'display_order':         1,
    },
    {
        'name':                  'Pro',
        'slug':                  'pro',
        'description':           'Unlimited listings, bulk upload, and priority support for professional dealers.',
        'description_ar':        'إعلانات غير محدودة ورفع جماعي ودعم فوري للتجار المحترفين.',
        'monthly_price':         299,
        'annual_price':          2990,
        'max_listings':          0,
        'max_images_per_listing': 20,
        'max_featured_listings': 10,
        'can_access_analytics':  True,
        'can_bulk_upload':       True,
        'can_have_showroom':     True,
        'can_have_workshop':     True,
        'priority_support':      True,
        'badge_type':            'pro',
        'display_order':         2,
    },
]


class Command(BaseCommand):
    help = 'Load default subscription plans (idempotent — updates existing plans by slug).'

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for plan_data in PLANS:
            slug = plan_data.pop('slug')
            plan, created = SubscriptionPlan.objects.update_or_create(
                slug=slug,
                defaults={**plan_data, 'slug': slug},
            )
            # restore slug for next iteration safety
            plan_data['slug'] = slug

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'  Created plan: {plan.name}'))
            else:
                updated_count += 1
                self.stdout.write(f'  Updated plan: {plan.name}')

        self.stdout.write(
            self.style.SUCCESS(
                f'\nDone. {created_count} created, {updated_count} updated.'
            )
        )

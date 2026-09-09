from django.core.management.base import BaseCommand
from importers.models import ImporterProfile


class Command(BaseCommand):
    help = 'Notify existing importers without CR data to submit their CR details'

    def handle(self, *args, **options):
        profiles = ImporterProfile.objects.filter(
            cr_verification_status='pending_initial', cr_expiry_date__isnull=True,
        ).select_related('user')
        count = profiles.count()
        self.stdout.write(f"Found {count} importer(s) without CR data.")

        for ip in profiles:
            try:
                from notifications.utils import notify
                notify(
                    recipient=ip.user,
                    notification_type='system',
                    title='Action Required: Submit Your CR Details',
                    message='Please submit your Commercial Registration details to continue using WARED.',
                )
                self.stdout.write(self.style.SUCCESS(f"  Notified: {ip.business_name} ({ip.user.email})"))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  Failed for {ip.business_name}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"\nDone. {count} importer(s) processed."))

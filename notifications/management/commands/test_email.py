"""
Quick email health check.

Usage:
    python3 manage.py test_email user@example.com
    python3 manage.py test_email           # sends to DEFAULT_FROM_EMAIL

Sends a single test email via the configured SMTP backend and reports
success or the exact error. Takes ~5 seconds on a healthy setup.
"""
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Send a test email to verify SMTP configuration.'

    def add_arguments(self, parser):
        parser.add_argument(
            'recipient',
            nargs='?',
            default=None,
            help='Email address to send to (default: DEFAULT_FROM_EMAIL)',
        )

    def handle(self, *args, **options):
        recipient = options['recipient'] or settings.DEFAULT_FROM_EMAIL
        # Strip display name if present (e.g. "Wared <x@y.com>" → "x@y.com")
        if '<' in recipient and '>' in recipient:
            recipient = recipient.split('<')[1].rstrip('>')

        self.stdout.write(f'Backend:   {settings.EMAIL_BACKEND}')
        self.stdout.write(f'Host:      {settings.EMAIL_HOST}:{settings.EMAIL_PORT}')
        self.stdout.write(f'From:      {settings.DEFAULT_FROM_EMAIL}')
        self.stdout.write(f'To:        {recipient}')
        self.stdout.write(f'Eager:     {getattr(settings, "CELERY_TASK_ALWAYS_EAGER", "N/A")}')
        self.stdout.write('')

        try:
            result = send_mail(
                subject='Wared email health check ✓',
                message='If you can read this, the email system is working.\n\n— Wared (وارد)',
                from_email=None,  # uses DEFAULT_FROM_EMAIL
                recipient_list=[recipient],
                fail_silently=False,
            )
            if result:
                self.stdout.write(self.style.SUCCESS(f'✓ Email dispatched to {recipient}'))
            else:
                self.stdout.write(self.style.WARNING(f'⚠ send_mail returned 0 — check spam or backend config'))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'✗ FAILED: {type(exc).__name__}: {exc}'))
            raise

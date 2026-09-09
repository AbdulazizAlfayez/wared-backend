"""
Centralised email-sending helper for WARED.

Usage:
    from notifications.emails import send_templated_email
    send_templated_email(
        to_email='user@example.com',
        subject='Hello',
        template_name='welcome',   # resolves to templates/emails/welcome.html
        context={'name': 'Aziz'},
    )

Always includes frontend_url and current_year in the template context.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def send_templated_email(to_email: str, subject: str, template_name: str, context: dict) -> None:
    """
    Render *template_name* (under templates/emails/) and send as a
    multipart HTML + plain-text email to *to_email*.

    Raises exceptions on send failure so the caller (a Celery task)
    can decide whether to retry or log.
    """
    ctx = dict(context)
    ctx.setdefault('frontend_url', getattr(settings, 'FRONTEND_URL', 'http://localhost:3000'))
    from django.utils import timezone
    ctx.setdefault('current_year', timezone.now().year)

    html_content = render_to_string(f'emails/{template_name}.html', ctx)
    text_content = strip_tags(html_content)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    msg.attach_alternative(html_content, 'text/html')
    msg.send(fail_silently=False)
    logger.info("Email '%s' sent to %s", subject, to_email)

import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from notifications.emails import send_templated_email
from sms.utils import send_sms

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='accounts.tasks.send_welcome_email')
def send_welcome_email(self, user_id):
    """Send a welcome email to a newly registered user."""
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("send_welcome_email: user_id=%s not found", user_id)
        return f"User {user_id} not found"

    try:
        send_templated_email(
            to_email=user.email,
            subject="Welcome to WARED!",
            template_name='welcome',
            context={
                'name': user.name or user.email,
            },
        )
        logger.info("Welcome email sent to %s (user %s)", user.email, user_id)
        return f"Welcome email sent to {user.email}"
    except Exception as exc:
        logger.error("send_welcome_email failed for user %s: %s", user_id, exc)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='accounts.tasks.send_password_reset_email')
def send_password_reset_email(self, user_id, reset_url):
    """Send password reset email with reset link to the user."""
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("send_password_reset_email: user_id=%s not found", user_id)
        return f"User {user_id} not found"

    try:
        send_templated_email(
            to_email=user.email,
            subject="Reset Your Password — WARED",
            template_name='password_reset',
            context={
                'name':      user.name or user.email,
                'reset_url': reset_url,
            },
        )
        logger.info("Password reset email sent to %s (user %s)", user.email, user_id)
        return f"Password reset email sent to {user.email}"
    except Exception as exc:
        logger.error("send_password_reset_email failed for user %s: %s", user_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# OTP Email task
# ---------------------------------------------------------------------------

_OTP_EMAIL_SUBJECTS = {
    'email_verification': 'Verify Your Email — WARED',
    'password_reset':     'Password Reset Code — WARED',
}


@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='accounts.tasks.send_otp_email')
def send_otp_email(self, user_id: int, code: str, purpose: str = 'email_verification') -> str:
    """Send an OTP code via email to the user."""
    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("send_otp_email: user_id=%s not found", user_id)
        return f"User {user_id} not found"

    subject = _OTP_EMAIL_SUBJECTS.get(purpose, 'Your WARED Verification Code')
    try:
        send_templated_email(
            to_email=user.email,
            subject=subject,
            template_name='otp_verification',
            context={
                'first_name': user.name or user.email,
                'otp_code':   code,
                'purpose':    purpose,
            },
        )
        logger.info("OTP email [%s] sent to %s (user %s)", purpose, user.email, user_id)
        return f"OTP email sent to {user.email}"
    except Exception as exc:
        logger.error("send_otp_email [%s] failed for user %s: %s", purpose, user_id, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# OTP SMS task
# ---------------------------------------------------------------------------

_OTP_MESSAGES = {
    'phone_verification': 'Your WARED verification code is: {code}. Valid for 10 minutes.',
    'login':              'Your WARED login code is: {code}. Valid for 10 minutes.',
    'password_reset':     'Your WARED password reset code is: {code}. Valid for 10 minutes.',
}


@shared_task(bind=True, max_retries=3, default_retry_delay=60,
             name='accounts.tasks.send_otp_sms')
def send_otp_sms(self, user_id: int, phone: str, code: str, purpose: str) -> str:
    """Send an OTP code via SMS to *phone*."""
    template = _OTP_MESSAGES.get(purpose, 'Your WARED code is: {code}. Valid for 10 minutes.')
    message  = template.format(code=code)
    try:
        success = send_sms(phone, message)
        if success:
            logger.info("OTP SMS [%s] sent to %s (user %s)", purpose, phone, user_id)
            return f"OTP SMS sent to {phone}"
        logger.error("OTP SMS [%s] failed for user %s / %s", purpose, user_id, phone)
        return f"OTP SMS failed for {phone}"
    except Exception as exc:
        logger.error("OTP SMS [%s] exception for user %s / %s: %s", purpose, user_id, phone, exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Phase 5.5 — Account Deletion Task
# ---------------------------------------------------------------------------

@shared_task(name='accounts.tasks.process_account_deletions')
def process_account_deletions():
    """Daily task: permanently anonymize accounts past their 30-day grace period."""
    from .models import AccountDeletionRequest
    User = get_user_model()

    now = timezone.now()
    pending = AccountDeletionRequest.objects.filter(
        status='pending',
        scheduled_deletion_date__lte=now,
    ).select_related('user')

    count = 0
    for req in pending:
        user = req.user
        original_email = user.email

        # Anonymize user fields
        user.email = f"deleted_{user.pk}@deleted.wared.sa"
        user.name = "Deleted User"
        user.phone = ''
        user.bio = ''
        user.avatar = None
        user.is_active = False
        user.is_deletion_pending = False
        user.is_deleted = True
        user.deleted_at = now
        user.show_phone = False
        user.show_email = False
        user.save()

        # Archive all listings
        from cars.models import Listing
        Listing.objects.filter(owner=user).update(status='archived', is_active=False)

        # Messages: user.name is already anonymized to "Deleted User" above,
        # so messages will show the anonymized sender name via the FK.

        # Mark deletion complete
        req.status = 'completed'
        req.completed_at = now
        req.save()

        # Audit log (kept for legal compliance)
        from auditlog.utils import log_action
        log_action(
            user=None,
            action='delete',
            model_name='User',
            object_id=user.pk,
            old_value={'email': original_email},
            new_value={'email': user.email, 'status': 'anonymized'},
        )

        logger.info("Account deletion completed for user %s (was %s)", user.pk, original_email)
        count += 1

    return f"Processed {count} account deletion(s)."


# ---------------------------------------------------------------------------
# Phase 5.5 — Data Export Task
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, default_retry_delay=120,
             name='accounts.tasks.generate_user_data_export')
def generate_user_data_export(self, request_id):
    """Generate a JSON export of all user data."""
    import json
    import os
    import tempfile

    from django.utils import timezone as tz
    from .models import DataExportRequest

    try:
        export_req = DataExportRequest.objects.select_related('user').get(pk=request_id)
    except DataExportRequest.DoesNotExist:
        return f"Export request {request_id} not found."

    export_req.status = 'processing'
    export_req.save(update_fields=['status'])

    user = export_req.user

    try:
        # Gather user profile
        profile = {
            'id': user.pk,
            'email': user.email,
            'name': user.name,
            'phone': user.phone or '',
            'role': user.role,
            'bio': user.bio,
            'date_joined': user.date_joined.isoformat(),
            'is_email_verified': user.is_email_verified,
            'is_phone_verified': user.is_phone_verified,
            'verification_level': user.verification_level,
        }

        # Listings
        from cars.models import Listing
        listings = list(
            Listing.objects.filter(owner=user).values(
                'id', 'title', 'make', 'model', 'year', 'price',
                'status', 'created_at',
            )
        )

        # Orders as buyer
        from orders.models import ImportOrder
        buyer_orders = list(
            ImportOrder.objects.filter(buyer=user).values(
                'id', 'order_number', 'status',
                'created_at', 'updated_at',
            )
        )

        # Orders as importer
        importer_orders = list(
            ImportOrder.objects.filter(importer=user).values(
                'id', 'order_number', 'status',
                'created_at', 'updated_at',
            )
        )

        # Messages
        from messaging.models import Message
        messages = list(
            Message.objects.filter(sender=user).values(
                'id', 'content', 'created_at', 'conversation_id',
            )
        )

        # Favorites
        from favorites.models import Favorite
        favorites = list(
            Favorite.objects.filter(user=user).values('id', 'listing_id', 'created_at')
        )

        # Reviews
        from reviews.models import Review
        reviews_given = list(
            Review.objects.filter(reviewer=user).values(
                'id', 'review_type', 'rating', 'title', 'comment', 'created_at',
            )
        )
        reviews_received = list(
            Review.objects.filter(reviewed_user=user).values(
                'id', 'review_type', 'rating', 'title', 'comment', 'created_at',
            )
        )

        # Audit log (own actions)
        from auditlog.models import AuditLog
        audit_entries = list(
            AuditLog.objects.filter(user=user).values(
                'action', 'model_name', 'object_id', 'timestamp',
            )[:500]  # Cap at 500 for performance
        )

        export_data = {
            'exported_at': tz.now().isoformat(),
            'user': profile,
            'listings': listings,
            'orders_as_buyer': buyer_orders,
            'orders_as_importer': importer_orders,
            'messages': messages,
            'favorites': favorites,
            'reviews_given': reviews_given,
            'reviews_received': reviews_received,
            'audit_log_summary': audit_entries,
        }

        # Serialize with datetime handling
        def json_serial(obj):
            from datetime import datetime, date
            from decimal import Decimal
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            if isinstance(obj, Decimal):
                return str(obj)
            raise TypeError(f"Type {type(obj)} not serializable")

        json_str = json.dumps(export_data, indent=2, default=json_serial, ensure_ascii=False)
        file_size = len(json_str.encode('utf-8'))

        # Save to a local file (in production, upload to S3/Cloudinary)
        export_dir = os.path.join(tempfile.gettempdir(), 'wared_exports')
        os.makedirs(export_dir, exist_ok=True)
        file_path = os.path.join(export_dir, f"export_{user.pk}_{export_req.pk}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(json_str)

        # Update request
        export_req.status = 'ready'
        export_req.completed_at = tz.now()
        export_req.download_url = file_path  # In production: signed S3/Cloudinary URL
        export_req.expires_at = tz.now() + tz.timedelta(days=7)
        export_req.file_size_bytes = file_size
        export_req.save()

        # Send email notification
        try:
            send_templated_email(
                to_email=user.email,
                subject='Your Data Export is Ready — WARED',
                template_name='data_export_ready',
                context={
                    'name': user.name,
                    'download_url': file_path,
                    'expires_in': '7 days',
                },
            )
        except Exception:
            pass

        logger.info("Data export completed for user %s (request %s, %s bytes)", user.pk, request_id, file_size)
        return f"Export ready for user {user.pk}: {file_size} bytes"

    except Exception as exc:
        export_req.status = 'failed'
        export_req.save(update_fields=['status'])
        logger.error("Data export failed for request %s: %s", request_id, exc)
        raise self.retry(exc=exc)


@shared_task(name='accounts.tasks.cleanup_expired_exports')
def cleanup_expired_exports():
    """Daily task: mark expired exports and clean up files."""
    import os
    from .models import DataExportRequest

    now = timezone.now()
    expired = DataExportRequest.objects.filter(
        status='ready',
        expires_at__lt=now,
    )

    count = 0
    for export_req in expired:
        # Try to delete the file
        if export_req.download_url and os.path.exists(export_req.download_url):
            try:
                os.remove(export_req.download_url)
            except OSError:
                pass
        export_req.status = 'expired'
        export_req.save(update_fields=['status'])
        count += 1

    return f"Cleaned up {count} expired export(s)."

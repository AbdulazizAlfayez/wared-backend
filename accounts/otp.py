"""
OTP utilities for WARED.

Public API:
    generate_otp()         → random 6-digit string
    create_otp(user, phone, purpose) → (OTPCode | None, error_str | None)
    verify_otp(user, code, purpose)  → (bool, message_str)
"""

import random
import string
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import OTPCode


def generate_otp() -> str:
    length = getattr(settings, 'OTP_LENGTH', 6)
    return ''.join(random.choices(string.digits, k=length))


def create_otp(user, phone: str, purpose: str = 'phone_verification'):
    """
    Create and return a fresh OTPCode for *user* / *phone* / *purpose*.

    Returns (otp, None) on success or (None, error_message) when the
    cooldown window has not expired yet.
    """
    cooldown = getattr(settings, 'OTP_COOLDOWN_SECONDS', 60)
    since    = timezone.now() - timedelta(seconds=cooldown)

    if OTPCode.objects.filter(user=user, purpose=purpose, created_at__gte=since).exists():
        return None, 'Please wait before requesting another code.'

    # Invalidate all previous unused OTPs for the same purpose
    OTPCode.objects.filter(user=user, purpose=purpose, is_used=False).update(is_used=True)

    expiry = getattr(settings, 'OTP_EXPIRY_MINUTES', 10)
    code   = generate_otp()
    otp    = OTPCode.objects.create(
        user=user,
        phone=phone,
        code=code,
        purpose=purpose,
        expires_at=timezone.now() + timedelta(minutes=expiry),
    )
    return otp, None


def verify_otp(user, code: str, purpose: str = 'phone_verification'):
    """
    Verify *code* against the latest unused OTPCode for *user* / *purpose*.

    Returns (True, success_message) or (False, error_message).
    """
    try:
        otp = OTPCode.objects.filter(
            user=user, purpose=purpose, is_used=False,
        ).latest('created_at')
    except OTPCode.DoesNotExist:
        return False, 'No OTP code found. Please request a new one.'

    if otp.is_expired():
        return False, 'OTP code has expired. Please request a new one.'

    max_attempts = getattr(settings, 'OTP_MAX_ATTEMPTS', 5)
    if otp.attempts >= max_attempts:
        return False, 'Too many failed attempts. Please request a new code.'

    if otp.code != code:
        otp.attempts += 1
        otp.save(update_fields=['attempts'])
        remaining = max_attempts - otp.attempts
        return False, f'Invalid code. {remaining} attempt{"s" if remaining != 1 else ""} remaining.'

    otp.is_used = True
    otp.save(update_fields=['is_used'])
    return True, 'OTP verified successfully.'

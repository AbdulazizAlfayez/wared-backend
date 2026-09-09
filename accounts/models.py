from cloudinary.models import CloudinaryField
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    """Custom user manager for email-based authentication."""
    
    def create_user(self, email, password=None, **extra_fields):
        """Create and return a regular user with email and password."""
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        """Create and return a superuser with email and password."""
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'admin')
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        
        return self.create_user(email, password, **extra_fields)


class ActiveUserManager(BaseUserManager):
    """Manager that excludes soft-deleted users. For use as User.active_users."""
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model with email as username."""
    
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('importer', 'Importer'),
        ('user', 'User'),
    ]
    
    username = models.CharField(max_length=150, blank=True, null=True)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True, null=True)
    role  = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user')

    # Profile fields
    avatar     = CloudinaryField('avatar', folder='avatars/', blank=True, null=True)
    bio        = models.TextField(max_length=500, blank=True, default='')
    city_obj   = models.ForeignKey(
        'locations.City',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='users',
    )
    show_phone = models.BooleanField(default=False)
    show_email = models.BooleanField(default=False)

    is_email_verified  = models.BooleanField(default=False)
    email_verified_at  = models.DateTimeField(null=True, blank=True)
    is_phone_verified  = models.BooleanField(default=False)
    phone_verified_at  = models.DateTimeField(null=True, blank=True)

    # Verification fields (Phase 5.1)
    national_id = models.CharField(max_length=10, blank=True, default='')
    national_id_verified = models.BooleanField(default=False)
    national_id_verified_at = models.DateTimeField(null=True, blank=True)
    identity_document = models.FileField(upload_to='verification/ids/', null=True, blank=True)
    commercial_registration = models.CharField(max_length=20, blank=True, default='')
    commercial_registration_verified = models.BooleanField(default=False)
    cr_verified_at = models.DateTimeField(null=True, blank=True)
    cr_document = models.FileField(upload_to='verification/cr/', null=True, blank=True)
    rega_license = models.CharField(max_length=20, blank=True, default='')
    rega_verified = models.BooleanField(default=False)
    is_identity_verified = models.BooleanField(default=False)
    is_business_verified = models.BooleanField(default=False)
    VERIFICATION_LEVEL_CHOICES = [
        ('none', 'None'),
        ('email', 'Email Verified'),
        ('phone', 'Phone Verified'),
        ('identity', 'Identity Verified'),
        ('business', 'Business Verified'),
        ('full', 'Fully Verified'),
    ]
    verification_level = models.CharField(max_length=20, choices=VERIFICATION_LEVEL_CHOICES, default='none')

    # Ratings & Reviews aggregation (Phase 5.3)
    average_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    total_reviews  = models.PositiveIntegerField(default=0)

    # Moderation fields (Phase 5.2)
    is_suspended      = models.BooleanField(default=False)
    suspended_until   = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True, default='')
    is_banned         = models.BooleanField(default=False)
    ban_reason        = models.TextField(blank=True, default='')
    warning_count     = models.PositiveIntegerField(default=0)

    # Account deletion (Phase 5.5)
    is_deletion_pending = models.BooleanField(default=False)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    active_users = ActiveUserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']
    
    class Meta:
        db_table = 'users'
        ordering = ['-date_joined']
    
    def __str__(self):
        return self.email


def update_verification_level(user):
    """Recalculate and persist the user's verification_level."""
    if user.is_business_verified and user.is_identity_verified:
        level = 'full'
    elif user.is_business_verified:
        level = 'business'
    elif user.is_identity_verified:
        level = 'identity'
    elif getattr(user, 'is_phone_verified', False):
        level = 'phone'
    elif getattr(user, 'is_email_verified', False):
        level = 'email'
    else:
        level = 'none'
    user.verification_level = level
    user.save(update_fields=['verification_level'])
    return level


class VerificationRequest(models.Model):
    VERIFICATION_TYPES = [
        ('national_id', 'National ID'),
        ('commercial_registration', 'Commercial Registration'),
        ('rega_license', 'REGA License'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='verification_requests')
    verification_type = models.CharField(max_length=30, choices=VERIFICATION_TYPES)
    document_number = models.CharField(max_length=30)
    document_file = models.FileField(upload_to='verification/requests/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    rejection_reason = models.TextField(blank=True, default='')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_verifications',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user} — {self.verification_type} ({self.status})"


class OTPCode(models.Model):
    PURPOSE_CHOICES = [
        ('phone_verification', 'Phone Verification'),
        ('email_verification', 'Email Verification'),
        ('login',             'Login'),
        ('password_reset',    'Password Reset'),
    ]

    user       = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='otp_codes',
    )
    # Stores phone number for SMS OTPs or email address for email OTPs.
    phone      = models.CharField(max_length=255, blank=True, default='')
    code       = models.CharField(max_length=6)
    purpose    = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    is_used    = models.BooleanField(default=False)
    attempts   = models.IntegerField(default=0)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'otp_codes'
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['user']),
            models.Index(fields=['phone']),
            models.Index(fields=['code']),
            models.Index(fields=['purpose']),
            models.Index(fields=['expires_at']),
        ]

    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    def is_valid(self) -> bool:
        from django.conf import settings
        max_attempts = getattr(settings, 'OTP_MAX_ATTEMPTS', 5)
        return not self.is_used and not self.is_expired() and self.attempts < max_attempts

    def __str__(self):
        return f"OTP [{self.purpose}] for {self.user} — {'used' if self.is_used else 'active'}"


class AccountDeletionRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
    ]

    user = models.OneToOneField(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='deletion_request',
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    scheduled_deletion_date = models.DateTimeField()
    reason = models.TextField(blank=True, default='')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.TextField(blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"Deletion request for {self.user} ({self.status})"


class DataExportRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('ready', 'Ready'),
        ('failed', 'Failed'),
        ('expired', 'Expired'),
    ]

    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='data_export_requests',
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    download_url = models.CharField(max_length=500, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at']

    def __str__(self):
        return f"Data export for {self.user} ({self.status})"

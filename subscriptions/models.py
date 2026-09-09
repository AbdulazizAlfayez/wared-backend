from django.conf import settings
from django.db import models
from django.utils import timezone


class SubscriptionPlan(models.Model):
    name           = models.CharField(max_length=50, unique=True)
    slug           = models.SlugField(unique=True)
    description    = models.TextField(blank=True, default='')
    description_ar = models.TextField(blank=True, default='')

    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    annual_price  = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    max_listings           = models.PositiveIntegerField(default=5)   # 0 = unlimited
    max_images_per_listing = models.PositiveIntegerField(default=10)
    max_featured_listings  = models.PositiveIntegerField(default=0)

    can_access_analytics = models.BooleanField(default=False)
    can_bulk_upload      = models.BooleanField(default=False)
    can_have_showroom    = models.BooleanField(default=False)
    can_have_workshop    = models.BooleanField(default=False)
    priority_support     = models.BooleanField(default=False)
    badge_type           = models.CharField(max_length=20, blank=True, default='')

    is_active     = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'subscription_plans'
        ordering = ['display_order']

    def __str__(self):
        return self.name


class DealerSubscription(models.Model):
    BILLING_CYCLE_CHOICES = [
        ('monthly', 'Monthly'),
        ('annual',  'Annual'),
    ]
    STATUS_CHOICES = [
        ('active',    'Active'),
        ('expired',   'Expired'),
        ('cancelled', 'Cancelled'),
        ('past_due',  'Past Due'),
        ('trial',     'Trial'),
    ]

    dealer        = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription',
    )
    plan          = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name='subscriptions',
    )
    billing_cycle = models.CharField(max_length=10, choices=BILLING_CYCLE_CHOICES, default='monthly')
    status        = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    started_at    = models.DateTimeField(auto_now_add=True)
    expires_at    = models.DateTimeField()
    cancelled_at  = models.DateTimeField(null=True, blank=True)
    auto_renew    = models.BooleanField(default=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dealer_subscriptions'
        indexes = [
            models.Index(fields=['dealer']),
            models.Index(fields=['plan']),
            models.Index(fields=['status']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"{self.dealer} — {self.plan.name} ({self.status})"

    @property
    def is_expired(self):
        return self.status != 'cancelled' and timezone.now() > self.expires_at

    @property
    def days_remaining(self):
        delta = self.expires_at - timezone.now()
        return max(0, delta.days)

    @property
    def is_trial(self):
        return bool(self.trial_ends_at and timezone.now() < self.trial_ends_at)


class SubscriptionHistory(models.Model):
    ACTION_CHOICES = [
        ('subscribed',    'Subscribed'),
        ('upgraded',      'Upgraded'),
        ('downgraded',    'Downgraded'),
        ('renewed',       'Renewed'),
        ('cancelled',     'Cancelled'),
        ('expired',       'Expired'),
        ('trial_started', 'Trial Started'),
    ]

    dealer        = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription_history',
    )
    plan          = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
    )
    action        = models.CharField(max_length=30, choices=ACTION_CHOICES)
    old_plan      = models.ForeignKey(
        SubscriptionPlan,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    amount_paid   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    billing_cycle = models.CharField(max_length=10, default='monthly')
    notes         = models.TextField(blank=True, default='')
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'subscription_history'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.dealer} — {self.action} → {self.plan.name}"

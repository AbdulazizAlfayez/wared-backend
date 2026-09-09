from django.conf import settings
from django.db import models


class PaymentTransaction(models.Model):
    PAYMENT_TYPE_CHOICES = [
        ('deposit', 'Deposit / reservation'),
        ('balance', 'Final balance'),
        ('promotion', 'Listing promotion'),
        ('refund', 'Refund'),
    ]

    METHOD_CHOICES = [
        ('mada', 'Mada'),
        ('visa', 'Visa'),
        ('mastercard', 'Mastercard'),
        ('apple_pay', 'Apple Pay'),
        ('stc_pay', 'STC Pay'),
        # High-value payments (full car balance) are settled by bank transfer
        # to WARED's account, then verified manually by WARED staff.
        ('bank_transfer', 'Bank Transfer'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('succeeded', 'Succeeded'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]

    reservation = models.ForeignKey(
        'orders.Reservation', on_delete=models.PROTECT,
        related_name='payments', null=True, blank=True,
    )
    order = models.ForeignKey(
        'orders.ImportOrder', on_delete=models.PROTECT,
        related_name='payments', null=True, blank=True,
    )
    # Listing-promotion purchases (importer pays WARED to boost a listing)
    promotion = models.ForeignKey(
        'cars.ListingPromotion', on_delete=models.PROTECT,
        related_name='payments', null=True, blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='payment_transactions',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPE_CHOICES)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    provider = models.CharField(max_length=30, default='mock')
    provider_transaction_id = models.CharField(max_length=100, blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Payment {self.id} · SAR {self.amount} · {self.status}"

from django.db import models


class ExchangeRate(models.Model):
    currency             = models.CharField(max_length=5, unique=True)
    rate_to_sar          = models.DecimalField(max_digits=12, decimal_places=6)
    updated_at           = models.DateTimeField(auto_now=True)
    source               = models.CharField(max_length=20, default='manual')
    previous_rate        = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    last_api_attempt_at  = models.DateTimeField(null=True, blank=True)
    last_api_error       = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['currency']

    def __str__(self):
        return f"{self.currency.upper()} → {self.rate_to_sar} SAR"

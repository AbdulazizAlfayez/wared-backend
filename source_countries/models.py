from django.core.validators import MinValueValidator
from django.db import models


class SourceCountry(models.Model):
    """
    Reference table for countries from which vehicles are imported.
    Used by the World Map Browse feature for aggregation and display.
    """

    code = models.CharField(max_length=20, primary_key=True)
    name_en = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100)
    iso_code = models.CharField(max_length=2, unique=True)
    flag_emoji = models.CharField(max_length=8, blank=True, default="")

    # Geographic — country centroid for map pin placement
    latitude = models.DecimalField(max_digits=9, decimal_places=6, default=0)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, default=0)

    # Shipping defaults
    avg_shipping_cost_sar = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    avg_shipping_days = models.PositiveIntegerField(default=0)

    # Content
    description = models.TextField(blank=True, default="")
    description_ar = models.TextField(blank=True, default="")

    # Admin controls
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name_en"]
        indexes = [
            models.Index(fields=["is_active", "display_order"]),
        ]
        verbose_name = "Source Country"
        verbose_name_plural = "Source Countries"

    def __str__(self):
        return f"{self.flag_emoji} {self.name_en}"

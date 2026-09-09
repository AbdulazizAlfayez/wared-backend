from django.db import models


class Region(models.Model):
    name_en    = models.CharField(max_length=100)
    name_ar    = models.CharField(max_length=100)
    slug       = models.SlugField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'regions'
        ordering = ['name_en']

    def __str__(self):
        return self.name_en


class City(models.Model):
    region     = models.ForeignKey(Region, on_delete=models.CASCADE, related_name='cities')
    name_en    = models.CharField(max_length=100)
    name_ar    = models.CharField(max_length=100)
    slug       = models.SlugField(unique=True)
    latitude   = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table      = 'cities'
        ordering      = ['name_en']
        unique_together = [('region', 'name_en')]

    def __str__(self):
        return f"{self.name_en}, {self.region.name_en}"

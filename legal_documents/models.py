from django.conf import settings
from django.db import models


class LegalDocument(models.Model):
    DOCUMENT_TYPES = [
        ('terms_of_service', 'Terms of Service'),
        ('privacy_policy', 'Privacy Policy'),
        ('cookie_policy', 'Cookie Policy'),
    ]

    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPES)
    version = models.CharField(max_length=20)  # e.g. "1.0.0"
    content_en = models.TextField()
    content_ar = models.TextField()
    effective_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        unique_together = ('document_type', 'version')
        ordering = ['-effective_date', '-created_at']

    def __str__(self):
        return f"{self.get_document_type_display()} v{self.version} ({'active' if self.is_active else 'inactive'})"

    def save(self, *args, **kwargs):
        # When activating, deactivate others of the same type
        if self.is_active:
            LegalDocument.objects.filter(
                document_type=self.document_type, is_active=True
            ).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)


class UserLegalAcceptance(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='legal_acceptances')
    document = models.ForeignKey(LegalDocument, on_delete=models.PROTECT, related_name='acceptances')
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')

    class Meta:
        unique_together = ('user', 'document')
        ordering = ['-accepted_at']

    def __str__(self):
        return f"{self.user} accepted {self.document}"


class CookieConsent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='cookie_consents')
    session_key = models.CharField(max_length=100, null=True, blank=True)
    essential = models.BooleanField(default=True)
    analytics = models.BooleanField(default=False)
    marketing = models.BooleanField(default=False)
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-accepted_at']

    def __str__(self):
        who = self.user or f"session:{self.session_key}"
        return f"CookieConsent({who})"

# DEPRECATED: Subscription system disabled — WARED uses commission-only model.
# Auto-assign Free plan signal is no longer active.
# Keeping the file so the import in apps.py doesn't break.

# from datetime import timedelta
#
# from django.conf import settings
# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from django.utils import timezone
#
#
# @receiver(post_save, sender=settings.AUTH_USER_MODEL)
# def auto_assign_free_plan(sender, instance, **kwargs):
#     """
#     When a user's role becomes 'importer' and they have no subscription yet,
#     automatically assign the Free plan.
#     """
#     if instance.role != 'importer':
#         return
#
#     from subscriptions.models import DealerSubscription, SubscriptionPlan
#
#     if DealerSubscription.objects.filter(dealer=instance).exists():
#         return
#
#     try:
#         free_plan = SubscriptionPlan.objects.get(slug='free')
#     except SubscriptionPlan.DoesNotExist:
#         return
#
#     DealerSubscription.objects.create(
#         dealer=instance,
#         plan=free_plan,
#         billing_cycle='monthly',
#         status='active',
#         expires_at=timezone.now() + timedelta(days=3650),
#     )

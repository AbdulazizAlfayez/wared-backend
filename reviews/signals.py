from django.db.models import Avg, Count
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Review


def _update_user_stats(user):
    from django.db.models import Avg, Count
    qs = Review.objects.filter(reviewed_user=user, status='approved')
    agg = qs.aggregate(avg=Avg('rating'), cnt=Count('id'))
    user.average_rating = agg['avg'] or 0
    user.total_reviews  = agg['cnt'] or 0
    user.save(update_fields=['average_rating', 'total_reviews'])


def _update_showroom_stats(showroom):
    qs = Review.objects.filter(showroom=showroom, status='approved')
    agg = qs.aggregate(avg=Avg('rating'), cnt=Count('id'))
    showroom.average_rating = agg['avg'] or 0
    showroom.total_reviews  = agg['cnt'] or 0
    showroom.save(update_fields=['average_rating', 'total_reviews'])


def _update_workshop_stats(workshop):
    qs = Review.objects.filter(workshop=workshop, status='approved')
    agg = qs.aggregate(avg=Avg('rating'), cnt=Count('id'))
    workshop.average_rating = agg['avg'] or 0
    workshop.total_reviews  = agg['cnt'] or 0
    workshop.save(update_fields=['average_rating', 'total_reviews'])


def _update_importer_profile_stats(importer_user):
    """
    Recompute ImporterProfile aggregate ratings after an importer_order review
    is saved or deleted.  Also updates per-dimension averages.
    """
    try:
        from importers.models import ImporterProfile
        profile = ImporterProfile.objects.get(user=importer_user)
    except Exception:
        return  # no profile — nothing to update

    qs = Review.objects.filter(
        reviewed_user=importer_user,
        review_type='importer_order',
        status='approved',
    )
    agg = qs.aggregate(
        avg_rating=Avg('rating'),
        avg_comm=Avg('communication_rating'),
        avg_acc=Avg('accuracy_rating'),
        avg_del=Avg('delivery_speed_rating'),
        avg_overall=Avg('overall_rating'),
        cnt=Count('id'),
    )

    profile.average_rating  = agg['avg_rating']  or 0
    profile.total_reviews   = agg['cnt']          or 0

    # Store per-dimension averages if the model has the columns (added by migration)
    if hasattr(profile, 'avg_communication_rating'):
        profile.avg_communication_rating  = agg['avg_comm']    or 0
        profile.avg_accuracy_rating       = agg['avg_acc']     or 0
        profile.avg_delivery_speed_rating = agg['avg_del']     or 0
        profile.avg_overall_rating        = agg['avg_overall'] or 0
        profile.save(update_fields=[
            'average_rating', 'total_reviews',
            'avg_communication_rating', 'avg_accuracy_rating',
            'avg_delivery_speed_rating', 'avg_overall_rating',
            'updated_at',
        ])
    else:
        profile.save(update_fields=['average_rating', 'total_reviews', 'updated_at'])


@receiver([post_save, post_delete], sender=Review)
def recalculate_ratings(sender, instance, **kwargs):
    _update_user_stats(instance.reviewed_user)
    if instance.showroom_id:
        _update_showroom_stats(instance.showroom)
    if instance.workshop_id:
        _update_workshop_stats(instance.workshop)
    if instance.review_type == 'importer_order':
        _update_importer_profile_stats(instance.reviewed_user)

from datetime import timedelta

from django.db.models import Avg, Count, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet
from drf_spectacular.utils import extend_schema

from notifications.utils import notify

from .models import DealerSubscription, SubscriptionHistory, SubscriptionPlan
from .serializers import (
    AdminSubscriptionSerializer,
    DealerSubscriptionSerializer,
    SubscribeSerializer,
    SubscriptionHistorySerializer,
    SubscriptionPlanSerializer,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_free_plan():
    return SubscriptionPlan.objects.get(slug='free')


def _make_expiry(billing_cycle: str):
    if billing_cycle == 'annual':
        return timezone.now() + timedelta(days=365)
    return timezone.now() + timedelta(days=30)


# ---------------------------------------------------------------------------
# Public plans
# ---------------------------------------------------------------------------

@extend_schema(tags=['Subscriptions'])
class SubscriptionPlanViewSet(GenericViewSet):
    serializer_class   = SubscriptionPlanSerializer
    permission_classes = [AllowAny]
    queryset           = SubscriptionPlan.objects.filter(is_active=True)
    lookup_field       = 'slug'

    def list(self, request):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    def retrieve(self, request, slug=None):
        plan = self.get_object()
        return Response(self.get_serializer(plan).data)


# ---------------------------------------------------------------------------
# Dealer: own subscription
# ---------------------------------------------------------------------------

@extend_schema(tags=['Subscriptions'])
class DealerSubscriptionViewSet(GenericViewSet):
    permission_classes = [IsAuthenticated]

    def _require_dealer(self, request):
        if request.user.role not in ('importer', 'admin'):
            return Response(
                {'error': 'Only importers can manage subscriptions.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def list(self, request):
        err = self._require_dealer(request)
        if err:
            return err

        try:
            sub = DealerSubscription.objects.select_related('plan').get(dealer=request.user)
        except DealerSubscription.DoesNotExist:
            return Response(
                {'error': 'No subscription found. Please subscribe to a plan.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(DealerSubscriptionSerializer(sub).data)

    @action(detail=False, methods=['post'])
    def subscribe(self, request):
        err = self._require_dealer(request)
        if err:
            return err

        ser = SubscribeSerializer(data=request.data, context={'request': request})
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)

        new_plan      = ser.context['plan']
        billing_cycle = ser.validated_data['billing_cycle']
        expires_at    = _make_expiry(billing_cycle)

        # Determine price
        amount = (
            new_plan.annual_price if billing_cycle == 'annual' else new_plan.monthly_price
        )

        try:
            sub = DealerSubscription.objects.select_related('plan').get(dealer=request.user)
            old_plan = sub.plan

            # Determine action label
            if new_plan.pk == old_plan.pk:
                history_action = 'renewed'
            elif float(new_plan.monthly_price) > float(old_plan.monthly_price):
                history_action = 'upgraded'
            else:
                history_action = 'downgraded'

            sub.plan          = new_plan
            sub.billing_cycle = billing_cycle
            sub.status        = 'active'
            sub.expires_at    = expires_at
            sub.cancelled_at  = None
            sub.save()
        except DealerSubscription.DoesNotExist:
            old_plan       = None
            history_action = 'subscribed'
            sub = DealerSubscription.objects.create(
                dealer=request.user,
                plan=new_plan,
                billing_cycle=billing_cycle,
                status='active',
                expires_at=expires_at,
            )

        # History record
        SubscriptionHistory.objects.create(
            dealer=request.user,
            plan=new_plan,
            action=history_action,
            old_plan=old_plan,
            amount_paid=amount,
            billing_cycle=billing_cycle,
        )

        # In-app notification
        notify(
            recipient=request.user,
            notification_type='system',
            title=f"You're now on the {new_plan.name} plan!",
            message=(
                f"Your {new_plan.name} subscription is active. "
                f"It renews on {sub.expires_at.strftime('%d %b %Y')}."
            ),
        )

        # Email
        try:
            from notifications.emails import send_templated_email
            from django.conf import settings as django_settings
            template = 'subscription_upgraded' if history_action == 'upgraded' else 'subscription_welcome'
            send_templated_email(
                to_email=request.user.email,
                subject=f"Welcome to the {new_plan.name} plan!",
                template_name=template,
                context={
                    'user_name':    request.user.name,
                    'plan_name':    new_plan.name,
                    'billing_cycle': billing_cycle,
                    'expires_at':   sub.expires_at.strftime('%d %b %Y'),
                    'amount':       str(amount),
                    'frontend_url': getattr(django_settings, 'FRONTEND_URL', ''),
                },
            )
        except Exception:
            pass

        return Response(DealerSubscriptionSerializer(sub).data)

    @action(detail=False, methods=['post'])
    def cancel(self, request):
        err = self._require_dealer(request)
        if err:
            return err

        try:
            sub = DealerSubscription.objects.select_related('plan').get(dealer=request.user)
        except DealerSubscription.DoesNotExist:
            return Response({'error': 'No subscription found.'}, status=status.HTTP_404_NOT_FOUND)

        if sub.status == 'cancelled':
            return Response({'error': 'Subscription is already cancelled.'}, status=status.HTTP_400_BAD_REQUEST)

        sub.status       = 'cancelled'
        sub.cancelled_at = timezone.now()
        sub.auto_renew   = False
        sub.save()

        SubscriptionHistory.objects.create(
            dealer=request.user,
            plan=sub.plan,
            action='cancelled',
            billing_cycle=sub.billing_cycle,
            notes='Cancelled by dealer.',
        )

        notify(
            recipient=request.user,
            notification_type='system',
            title="Subscription cancelled",
            message=(
                f"Your {sub.plan.name} subscription has been cancelled. "
                f"It remains active until {sub.expires_at.strftime('%d %b %Y')}."
            ),
        )

        return Response(DealerSubscriptionSerializer(sub).data)

    @action(detail=False, methods=['get'])
    def history(self, request):
        err = self._require_dealer(request)
        if err:
            return err

        qs = SubscriptionHistory.objects.filter(
            dealer=request.user
        ).select_related('plan', 'old_plan')
        return Response(SubscriptionHistorySerializer(qs, many=True).data)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@extend_schema(tags=['Subscriptions'])
class AdminSubscriptionViewSet(ModelViewSet):
    serializer_class   = AdminSubscriptionSerializer
    permission_classes = [IsAuthenticated]
    queryset           = DealerSubscription.objects.select_related('dealer', 'plan').all()
    http_method_names  = ['get', 'patch', 'head', 'options']

    def get_permissions(self):
        return [IsAuthenticated()]

    def _check_admin(self, request):
        if request.user.role != 'admin':
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        return None

    def list(self, request, *args, **kwargs):
        err = self._check_admin(request)
        if err:
            return err
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        err = self._check_admin(request)
        if err:
            return err
        return super().retrieve(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        err = self._check_admin(request)
        if err:
            return err
        return super().partial_update(request, *args, **kwargs)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        err = self._check_admin(request)
        if err:
            return err

        # Subscribers by plan
        by_plan = list(
            DealerSubscription.objects.values('plan__name', 'plan__slug')
            .annotate(count=Count('id'))
            .order_by('-count')
        )

        # Revenue this month
        now         = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        revenue_month = (
            SubscriptionHistory.objects
            .filter(created_at__gte=month_start)
            .aggregate(total=Sum('amount_paid'))['total'] or 0
        )

        # New subscribers this week
        week_ago      = now - timedelta(days=7)
        new_this_week = SubscriptionHistory.objects.filter(
            action='subscribed', created_at__gte=week_ago
        ).count()

        # Status breakdown
        status_counts = {
            row['status']: row['count']
            for row in DealerSubscription.objects.values('status').annotate(count=Count('id'))
        }

        # Total cancellations ever (churn indicator)
        total_cancelled = SubscriptionHistory.objects.filter(action='cancelled').count()
        total_ever      = SubscriptionHistory.objects.filter(action='subscribed').count()
        churn_rate      = round(total_cancelled / total_ever * 100, 1) if total_ever else 0

        return Response({
            'by_plan':              by_plan,
            'revenue_this_month':   float(revenue_month),
            'new_subscribers_this_week': new_this_week,
            'status_counts':        status_counts,
            'churn_rate_pct':       churn_rate,
        })

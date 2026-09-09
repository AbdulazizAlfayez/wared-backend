from django.urls import path

from .views import AdminSubscriptionViewSet, DealerSubscriptionViewSet, SubscriptionPlanViewSet

# Plans (public)
plan_list     = SubscriptionPlanViewSet.as_view({'get': 'list'})
plan_detail   = SubscriptionPlanViewSet.as_view({'get': 'retrieve'})

# My subscription (dealer)
my_sub        = DealerSubscriptionViewSet.as_view({'get': 'list'})
my_subscribe  = DealerSubscriptionViewSet.as_view({'post': 'subscribe'})
my_cancel     = DealerSubscriptionViewSet.as_view({'post': 'cancel'})
my_history    = DealerSubscriptionViewSet.as_view({'get': 'history'})

# Admin
admin_list    = AdminSubscriptionViewSet.as_view({'get': 'list'})
admin_detail  = AdminSubscriptionViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update'})
admin_stats   = AdminSubscriptionViewSet.as_view({'get': 'stats'})

urlpatterns = [
    # Public plans
    path('subscription-plans/',         plan_list,    name='subscription-plan-list'),
    path('subscription-plans/<slug:slug>/', plan_detail, name='subscription-plan-detail'),

    # Dealer: own subscription
    path('my-subscription/',            my_sub,       name='my-subscription'),
    path('my-subscription/subscribe/',  my_subscribe, name='my-subscription-subscribe'),
    path('my-subscription/cancel/',     my_cancel,    name='my-subscription-cancel'),
    path('my-subscription/history/',    my_history,   name='my-subscription-history'),

    # Admin
    path('admin/subscriptions/',        admin_list,   name='admin-subscription-list'),
    path('admin/subscriptions/stats/',  admin_stats,  name='admin-subscription-stats'),
    path('admin/subscriptions/<int:pk>/', admin_detail, name='admin-subscription-detail'),
]

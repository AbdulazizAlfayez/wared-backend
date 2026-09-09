from django.urls import path
from . import views
from . import reservation_views

urlpatterns = [
    # Import Orders
    path('orders/',                             views.OrderListCreateView.as_view(),        name='order-list-create'),
    path('orders/<int:pk>/',                    views.OrderDetailView.as_view(),            name='order-detail'),
    path('orders/<int:pk>/update-status/',      views.OrderUpdateStatusView.as_view(),      name='order-update-status'),
    path('orders/<int:pk>/cancel/',             views.OrderCancelView.as_view(),            name='order-cancel'),
    path('orders/<int:pk>/pay-balance/',        views.OrderPayBalanceView.as_view(),        name='order-pay-balance'),
    path('orders/<int:pk>/confirm-payment/',    views.OrderConfirmPaymentView.as_view(),    name='order-confirm-payment'),
    path('orders/<int:pk>/reject-payment/',     views.OrderRejectPaymentView.as_view(),     name='order-reject-payment'),
    path('admin/payments/',                     views.AdminPendingPaymentsView.as_view(),   name='admin-payments'),
    path('orders/<int:pk>/timeline/',           views.OrderTimelineListCreateView.as_view(), name='order-timeline'),
    path('orders/<int:pk>/documents/',          views.OrderDocumentListCreateView.as_view(), name='order-documents'),

    # Reservations (Phase M3 + M5)
    path('reservations/',                            reservation_views.ReservationCreateView.as_view(),    name='reservation-create'),
    path('reservations/list/',                       reservation_views.ReservationListView.as_view(),      name='reservation-list'),
    path('reservations/pending-for-me/',             reservation_views.ReservationPendingForMeView.as_view(), name='reservation-pending'),
    path('reservations/<int:pk>/',                   reservation_views.ReservationDetailView.as_view(),    name='reservation-detail'),
    path('reservations/<int:pk>/cancel/',            reservation_views.ReservationCancelView.as_view(),    name='reservation-cancel'),
    path('reservations/<int:pk>/accept/',            reservation_views.ReservationAcceptView.as_view(),    name='reservation-accept'),
    path('reservations/<int:pk>/reject/',            reservation_views.ReservationRejectView.as_view(),    name='reservation-reject'),
    path('reservations/<int:pk>/convert-to-order/',  reservation_views.ReservationConvertView.as_view(),   name='reservation-convert'),
]

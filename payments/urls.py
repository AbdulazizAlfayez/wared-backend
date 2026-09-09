from django.urls import path
from .views import ReservationPayView, BankDetailsView

urlpatterns = [
    path('reservations/<int:pk>/pay/', ReservationPayView.as_view(), name='reservation-pay'),
    path('payments/bank-details/',     BankDetailsView.as_view(),    name='bank-details'),
]

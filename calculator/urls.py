from django.urls import path
from .views import CostEstimateView, ExchangeRateListView

urlpatterns = [
    path('calculator/estimate/',       CostEstimateView.as_view(),     name='calculator-estimate'),
    path('calculator/exchange-rates/', ExchangeRateListView.as_view(), name='calculator-exchange-rates'),
]

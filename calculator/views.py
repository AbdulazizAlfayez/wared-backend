from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status

from .models import ExchangeRate
from .serializers import (
    CalculatorInputSerializer,
    CalculatorResultSerializer,
    ExchangeRateSerializer,
)
from .services import calculate_import_cost


class CostEstimateView(APIView):
    """
    GET /api/calculator/estimate/
    Public — no auth required.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        input_ser = CalculatorInputSerializer(data=request.query_params)
        if not input_ser.is_valid():
            return Response(input_ser.errors, status=status.HTTP_400_BAD_REQUEST)

        data = input_ser.validated_data
        try:
            result = calculate_import_cost(
                source_country  = data['source_country'],
                car_value       = data['car_value'],
                source_currency = data['source_currency'],
                year            = data.get('year'),
                engine_size_cc  = data.get('engine_size_cc'),
            )
        except ExchangeRate.DoesNotExist:
            return Response(
                {'source_currency': f"No exchange rate found for '{data['source_currency']}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        out = CalculatorResultSerializer(result)
        return Response(out.data)


class ExchangeRateListView(APIView):
    """
    GET /api/calculator/exchange-rates/
    Public — no auth required.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        rates = ExchangeRate.objects.all()
        return Response(ExchangeRateSerializer(rates, many=True).data)

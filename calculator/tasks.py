import logging
from decimal import Decimal

import requests
import sentry_sdk
from celery import shared_task
from django.utils import timezone

from .models import ExchangeRate

logger = logging.getLogger(__name__)

SUPPORTED_CURRENCIES = ['usd', 'aed', 'jpy', 'krw', 'eur', 'gbp', 'cad', 'qar']
# open.er-api.com — free, no API key, supports SAR base
API_URL = "https://open.er-api.com/v6/latest/SAR"


@shared_task(bind=True, max_retries=3, default_retry_delay=300,
             name='calculator.tasks.update_exchange_rates')
def update_exchange_rates(self):
    """
    Daily task: fetch latest rates from exchangerate.host.
    Inverts SAR-based rates to get foreign→SAR.
    Skips rows with source='manual' (admin overrides).
    On failure: retries 3x, logs error, leaves existing rates intact.
    """
    now = timezone.now()

    try:
        response = requests.get(API_URL, timeout=15)
        response.raise_for_status()
        data = response.json()

        rates = data.get('rates', {})
        if not rates:
            raise ValueError(f"API returned empty rates: {data}")

        updated = 0
        skipped = 0
        failed = []

        for currency in SUPPORTED_CURRENCIES:
            api_key = currency.upper()
            if api_key not in rates:
                failed.append(currency)
                continue

            sar_to_foreign = Decimal(str(rates[api_key]))
            if sar_to_foreign == 0:
                failed.append(currency)
                continue

            # Invert: "1 SAR = X foreign" → "1 foreign = 1/X SAR"
            foreign_to_sar = (Decimal('1') / sar_to_foreign).quantize(Decimal('0.000001'))

            try:
                rate_obj = ExchangeRate.objects.get(currency=currency)
            except ExchangeRate.DoesNotExist:
                rate_obj = ExchangeRate(currency=currency, rate_to_sar=Decimal('0'))

            if rate_obj.source == 'manual':
                skipped += 1
                continue

            # Save previous rate for admin reference
            if rate_obj.rate_to_sar and rate_obj.rate_to_sar > 0:
                rate_obj.previous_rate = rate_obj.rate_to_sar

            rate_obj.rate_to_sar = foreign_to_sar
            rate_obj.source = 'api'
            rate_obj.last_api_attempt_at = now
            rate_obj.last_api_error = ''
            rate_obj.save()
            updated += 1

        result = {
            'status': 'success',
            'updated': updated,
            'skipped_manual': skipped,
            'failed_currencies': failed,
            'timestamp': now.isoformat(),
        }
        logger.info("Exchange rates updated: %s", result)
        return result

    except (requests.RequestException, ValueError, KeyError, Exception) as exc:
        logger.error("Exchange rate update failed (attempt %d/%d): %s",
                     self.request.retries + 1, self.max_retries + 1, exc)

        # Mark error on existing API-sourced rows
        ExchangeRate.objects.filter(source__in=['api', 'fallback']).update(
            last_api_attempt_at=now,
            last_api_error=str(exc)[:500],
        )

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

        logger.critical("Exchange rate update permanently failed after retries: %s", exc)
        sentry_sdk.capture_exception(exc)
        return {'status': 'failed', 'error': str(exc)}

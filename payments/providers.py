"""
Payment provider abstraction.
Swap PAYMENT_PROVIDER in settings to switch between mock and real providers.
"""
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PaymentResult:
    success: bool
    transaction_id: str
    provider: str
    error_message: str = ''


class PaymentProvider(ABC):
    @abstractmethod
    def charge(self, amount: Decimal, method: str, metadata: dict) -> PaymentResult:
        pass


class MockPaymentProvider(PaymentProvider):
    """Development provider. Always succeeds unless amount ends in .13."""

    def charge(self, amount: Decimal, method: str, metadata: dict) -> PaymentResult:
        time.sleep(0.5)  # simulate network latency
        if str(amount).endswith('.13'):
            return PaymentResult(
                success=False,
                transaction_id='',
                provider='mock',
                error_message='Card declined (simulated failure)',
            )
        return PaymentResult(
            success=True,
            transaction_id=f'mock_{uuid.uuid4().hex[:12]}',
            provider='mock',
        )


class MoyasarPaymentProvider(PaymentProvider):
    """Production provider. Stub — implement when CR + API keys are ready."""

    def charge(self, amount: Decimal, method: str, metadata: dict) -> PaymentResult:
        raise NotImplementedError('Moyasar integration pending CR license')


def get_payment_provider() -> PaymentProvider:
    from django.conf import settings
    provider_name = getattr(settings, 'PAYMENT_PROVIDER', 'mock')
    if provider_name == 'moyasar':
        return MoyasarPaymentProvider()
    return MockPaymentProvider()

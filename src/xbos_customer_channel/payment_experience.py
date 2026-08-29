from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class PaymentMethodCode(StrEnum):
    XAFPAY_WALLET = "xafpay_wallet"
    MTN_MOBILE_MONEY = "mtn_mobile_money"
    ORANGE_MONEY = "orange_money"
    CARD = "card"
    PAY_AT_COUNTER = "pay_at_counter"


class NextActionType(StrEnum):
    NONE = "none"
    OPEN_URL = "open_url"
    DISPLAY_QR = "display_qr"
    AWAIT_PUSH = "await_push"
    OPEN_WALLET = "open_wallet"
    WAIT = "wait"


class FakePaymentOutcome(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    REVERSED = "reversed"


@dataclass(frozen=True, slots=True)
class NextAction:
    action: NextActionType
    action_ref: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentMethodPresentation:
    method: PaymentMethodCode
    label: str
    next_action: NextAction


@dataclass(frozen=True, slots=True)
class CanonicalPaymentRequestProjection:
    payment_request_ref: str
    order_ref: str
    correlation_ref: str
    canonical_amount: Decimal
    currency: str
    methods: tuple[PaymentMethodPresentation, ...]
    policy_ref: str
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class FakePaymentResult:
    fixture_ref: str
    payment_request_ref: str
    outcome: FakePaymentOutcome
    next_action: NextAction
    test_fixture_only: bool = True
    financial_system_mutation: bool = False
    xbos_canonical_payment_mutation: bool = False


class PaymentRequestAuthorityMismatch(PermissionError):
    pass


class PaymentMethodNotPermitted(ValueError):
    pass

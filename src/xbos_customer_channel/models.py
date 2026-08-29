from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class EntryContext:
    merchant_ref: str
    location_ref: str
    service_mode: str
    table_ref: str | None = None


@dataclass(frozen=True, slots=True)
class MenuItem:
    item_ref: str
    name: str
    display_price: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class Quote:
    quote_ref: str
    merchant_ref: str
    amount: Decimal
    currency: str
    item_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OrderRef:
    order_ref: str
    state: str
    quote_ref: str


class PaymentState(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True)
class PaymentRequest:
    payment_ref: str
    amount: Decimal
    currency: str
    state: PaymentState


@dataclass(frozen=True, slots=True)
class Receipt:
    receipt_ref: str
    order_ref: str
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class CustomerIdentity:
    customer_ref: str
    consent_ref: str | None = None


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    message_ref: str
    channel: str
    body: str

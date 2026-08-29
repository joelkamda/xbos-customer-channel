from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .catalog import CommercialQuoteSnapshot, QuoteLineSnapshot


class ServiceMode(StrEnum):
    DINE_IN = "dine_in"
    TAKEAWAY = "takeaway"
    DELIVERY = "delivery"


class CanonicalOrderState(StrEnum):
    CREATED = "created"
    CONFIRMED = "confirmed"


@dataclass(frozen=True, slots=True)
class ServiceModeProjection:
    """XBOS-projected service semantics; never Channel-owned merchant policy."""

    service_context_ref: str
    merchant_ref: str
    location_ref: str
    mode: ServiceMode
    available: bool
    table_ref: str | None = None
    pickup_context_ref: str | None = None
    contact_ref: str | None = None
    delivery_address_ref: str | None = None
    service_area_ref: str | None = None
    delivery_fee: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class CommercialChargeSnapshot:
    charge_ref: str
    label: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class AuthoritativeOrderConfirmationSnapshot:
    """Commercial confirmation supplied by XBOS semantics, not calculated by Channel."""

    confirmation_ref: str
    quote_ref: str
    quote_version: str
    merchant_ref: str
    location_ref: str
    service_context_ref: str
    service_mode: ServiceMode
    lines: tuple[QuoteLineSnapshot, ...]
    delivery_fee: Decimal | None
    taxes_charges: tuple[CommercialChargeSnapshot, ...]
    total: Decimal
    currency: str
    expires_at_epoch: int


@dataclass(frozen=True, slots=True)
class OrderSubmitRequest:
    client_submit_ref: str
    correlation_ref: str
    confirmation_ref: str


@dataclass(frozen=True, slots=True)
class CanonicalOrderProjection:
    """Projection of canonical XBOS order result returned through the typed boundary."""

    order_ref: str
    client_submit_ref: str
    correlation_ref: str
    confirmation_ref: str
    state: CanonicalOrderState
    evidence_ref: str


class OrderSubmissionConflict(RuntimeError):
    pass


class OrderTransportUnknown(TimeoutError):
    """Transport outcome is unknown; never equivalent to canonical order success."""


class ServiceModeUnavailable(PermissionError):
    pass


class OrderSubmissionUnknown(RuntimeError):
    pass

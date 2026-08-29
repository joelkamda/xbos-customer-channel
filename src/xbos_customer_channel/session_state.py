from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChannelState(StrEnum):
    START = "start"
    MERCHANT_CONTEXT = "merchant_context"
    BROWSING = "browsing"
    CART = "cart"
    SERVICE_MODE = "service_mode"
    CUSTOMER_DETAILS = "customer_details"
    REVIEW = "review"
    ORDER_SUBMITTING = "order_submitting"
    ORDER_CREATED = "order_created"
    PAYMENT_METHOD = "payment_method"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    FULFILLMENT = "fulfillment"
    COMPLETED = "completed"
    HUMAN_HANDOFF = "human_handoff"
    CANCELED = "canceled"


class UpstreamOrderState(StrEnum):
    CREATED = "created"
    CONFIRMED = "confirmed"
    CANCELED = "canceled"


class UpstreamPaymentCommercialState(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELED = "canceled"


class UpstreamFulfillmentState(StrEnum):
    NOT_STARTED = "not_started"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELED = "canceled"


class MaterialAction(StrEnum):
    QUANTITY_CHANGE = "quantity_change"
    DELIVERY_ADDRESS = "delivery_address"
    HIGH_VALUE_CHANGE = "high_value_change"
    PAYMENT = "payment"
    CANCELLATION = "cancellation"


@dataclass(frozen=True, slots=True)
class CustomerSessionSnapshot:
    """Channel-owned interaction snapshot only; upstream refs are correlations, not business truth."""

    session_ref: str
    conversation_ref: str
    correlation_ref: str
    state: ChannelState
    entry_token_ref: str | None = None
    cart_ref: str | None = None
    quote_ref: str | None = None
    order_ref: str | None = None
    payment_ref: str | None = None
    last_upstream_evidence_ref: str | None = None
    human_handoff_ref: str | None = None
    transition_count: int = 0


@dataclass(frozen=True, slots=True)
class UpstreamStateProjection:
    """Typed evidence projected from authoritative upstream systems; never persisted as a channel ledger."""

    evidence_ref: str
    correlation_ref: str
    observed_at_epoch: int
    order_ref: str | None = None
    order_state: UpstreamOrderState | None = None
    payment_ref: str | None = None
    payment_state: UpstreamPaymentCommercialState | None = None
    fulfillment_state: UpstreamFulfillmentState | None = None


@dataclass(frozen=True, slots=True)
class MaterialInputCandidate:
    value_ref: str
    display_value: str


@dataclass(frozen=True, slots=True)
class MaterialInputDecision:
    action: MaterialAction
    selected: MaterialInputCandidate
    explicitly_confirmed: bool

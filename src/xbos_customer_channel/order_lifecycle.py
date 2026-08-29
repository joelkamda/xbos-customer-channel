from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .session_state import UpstreamFulfillmentState, UpstreamOrderState, UpstreamPaymentCommercialState


class ChangeDisposition(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CancellationCase(StrEnum):
    CANCEL_BEFORE_PAYMENT = "cancel_before_payment"
    CANCEL_AFTER_PAYMENT = "cancel_after_payment"
    CANCEL_AFTER_PREPARATION_BEGAN = "cancel_after_preparation_began"
    MERCHANT_INITIATED_CANCELLATION = "merchant_initiated_cancellation"


class CancellationDisposition(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class FulfillmentStage(StrEnum):
    KITCHEN_BAR_TICKET = "kitchen_bar_ticket"
    PREPARATION = "preparation"
    READY = "ready"
    SERVED = "served"
    PICKED_UP = "picked_up"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"


@dataclass(frozen=True, slots=True)
class OrderChangeRequest:
    client_change_ref: str
    correlation_ref: str
    order_ref: str
    requested_change_ref: str


@dataclass(frozen=True, slots=True)
class OrderChangeDecision:
    decision_ref: str
    order_ref: str
    correlation_ref: str
    disposition: ChangeDisposition
    reason_code: str
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class CancellationRequest:
    client_cancel_ref: str
    correlation_ref: str
    order_ref: str
    reason_ref: str


@dataclass(frozen=True, slots=True)
class CancellationDecision:
    decision_ref: str
    order_ref: str
    correlation_ref: str
    case: CancellationCase
    disposition: CancellationDisposition
    financial_correction_required: bool
    reason_code: str
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class FulfillmentProjection:
    order_ref: str
    correlation_ref: str
    stage: FulfillmentStage
    evidence_ref: str
    observed_at_epoch: int


@dataclass(frozen=True, slots=True)
class OrderLifecycleProjection:
    order_ref: str
    correlation_ref: str
    order_state: UpstreamOrderState
    payment_state: UpstreamPaymentCommercialState
    fulfillment_state: UpstreamFulfillmentState
    fulfillment_stage: FulfillmentStage | None
    evidence_ref: str
    observed_at_epoch: int


class LifecycleRequestConflict(RuntimeError):
    pass


class ExplicitLifecycleConfirmationRequired(ValueError):
    pass


class LifecycleAuthorityMismatch(PermissionError):
    pass

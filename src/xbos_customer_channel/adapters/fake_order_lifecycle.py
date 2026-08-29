from __future__ import annotations

from dataclasses import dataclass

from ..order_lifecycle import (
    CancellationCase,
    CancellationDecision,
    CancellationDisposition,
    CancellationRequest,
    ChangeDisposition,
    FulfillmentProjection,
    FulfillmentStage,
    LifecycleAuthorityMismatch,
    LifecycleRequestConflict,
    OrderChangeDecision,
    OrderChangeRequest,
    OrderLifecycleProjection,
)
from ..session_state import UpstreamFulfillmentState, UpstreamOrderState, UpstreamPaymentCommercialState


@dataclass
class _LifecycleRecord:
    correlation_ref: str
    order_state: UpstreamOrderState = UpstreamOrderState.CONFIRMED
    payment_state: UpstreamPaymentCommercialState = UpstreamPaymentCommercialState.NOT_REQUESTED
    fulfillment_stage: FulfillmentStage | None = None
    merchant_initiated_cancel: bool = False

    @property
    def fulfillment_state(self) -> UpstreamFulfillmentState:
        if self.order_state is UpstreamOrderState.CANCELED:
            return UpstreamFulfillmentState.CANCELED
        if self.fulfillment_stage is None:
            return UpstreamFulfillmentState.NOT_STARTED
        if self.fulfillment_stage in {
            FulfillmentStage.SERVED,
            FulfillmentStage.PICKED_UP,
            FulfillmentStage.DELIVERED,
        }:
            return UpstreamFulfillmentState.COMPLETED
        return UpstreamFulfillmentState.ACTIVE


class FakeXBOSOrderChangeFulfillmentClient:
    """Deterministic XBOS lifecycle-policy fixture; never Channel-owned order/fulfillment truth."""

    def __init__(self) -> None:
        self._records: dict[str, _LifecycleRecord] = {}
        self._change_by_ref: dict[str, tuple[OrderChangeRequest, OrderChangeDecision]] = {}
        self._cancel_by_ref: dict[str, tuple[CancellationRequest, CancellationDecision]] = {}
        self._event_counter = 0
        self.canonical_change_effect_count = 0
        self.canonical_cancel_effect_count = 0

    def register_order(
        self,
        *,
        order_ref: str,
        correlation_ref: str,
        payment_state: UpstreamPaymentCommercialState = UpstreamPaymentCommercialState.NOT_REQUESTED,
        fulfillment_stage: FulfillmentStage | None = None,
        merchant_initiated_cancel: bool = False,
    ) -> None:
        order_state = UpstreamOrderState.CANCELED if merchant_initiated_cancel else UpstreamOrderState.CONFIRMED
        self._records[order_ref] = _LifecycleRecord(
            correlation_ref=correlation_ref,
            order_state=order_state,
            payment_state=payment_state,
            fulfillment_stage=fulfillment_stage,
            merchant_initiated_cancel=merchant_initiated_cancel,
        )

    def set_fulfillment_stage(self, order_ref: str, stage: FulfillmentStage | None) -> None:
        record = self._required(order_ref)
        record.fulfillment_stage = stage

    def request_order_change(self, request: OrderChangeRequest) -> OrderChangeDecision:
        prior = self._change_by_ref.get(request.client_change_ref)
        if prior is not None:
            previous_request, previous_decision = prior
            if previous_request != request:
                raise LifecycleRequestConflict("change_idempotency_payload_conflict")
            return previous_decision

        record = self._required_matches(request.order_ref, request.correlation_ref)
        if record.order_state is UpstreamOrderState.CANCELED:
            disposition = ChangeDisposition.REJECTED
            reason = "canonical_order_already_canceled"
        elif record.fulfillment_stage is not None:
            disposition = ChangeDisposition.REJECTED
            reason = "change_not_permitted_after_fulfillment_started"
        else:
            disposition = ChangeDisposition.ACCEPTED
            reason = "xbos_change_policy_accepted"
            self.canonical_change_effect_count += 1

        decision = OrderChangeDecision(
            decision_ref=self._next_evidence("change-decision"),
            order_ref=request.order_ref,
            correlation_ref=request.correlation_ref,
            disposition=disposition,
            reason_code=reason,
            evidence_ref=self._next_evidence("change-evidence"),
        )
        self._change_by_ref[request.client_change_ref] = (request, decision)
        return decision

    def request_cancellation(self, request: CancellationRequest) -> CancellationDecision:
        prior = self._cancel_by_ref.get(request.client_cancel_ref)
        if prior is not None:
            previous_request, previous_decision = prior
            if previous_request != request:
                raise LifecycleRequestConflict("cancel_idempotency_payload_conflict")
            return previous_decision

        record = self._required_matches(request.order_ref, request.correlation_ref)
        case = self._classify_cancellation(record)
        financial = record.payment_state is UpstreamPaymentCommercialState.PAID

        if case is CancellationCase.CANCEL_AFTER_PREPARATION_BEGAN:
            disposition = CancellationDisposition.REJECTED
            reason = "xbos_policy_rejects_customer_cancel_after_preparation"
        elif case is CancellationCase.MERCHANT_INITIATED_CANCELLATION:
            disposition = CancellationDisposition.ACCEPTED
            reason = "merchant_initiated_cancellation_already_authoritative"
        else:
            disposition = CancellationDisposition.ACCEPTED
            reason = "xbos_cancellation_policy_accepted"
            record.order_state = UpstreamOrderState.CANCELED
            self.canonical_cancel_effect_count += 1

        decision = CancellationDecision(
            decision_ref=self._next_evidence("cancel-decision"),
            order_ref=request.order_ref,
            correlation_ref=request.correlation_ref,
            case=case,
            disposition=disposition,
            financial_correction_required=financial,
            reason_code=reason,
            evidence_ref=self._next_evidence("cancel-evidence"),
        )
        self._cancel_by_ref[request.client_cancel_ref] = (request, decision)
        return decision

    def get_cancellation_projection(self, *, order_ref: str, correlation_ref: str) -> CancellationDecision | None:
        record = self._required_matches(order_ref, correlation_ref)
        if not record.merchant_initiated_cancel:
            return None
        return CancellationDecision(
            decision_ref=self._next_evidence("merchant-cancel-decision"),
            order_ref=order_ref,
            correlation_ref=correlation_ref,
            case=CancellationCase.MERCHANT_INITIATED_CANCELLATION,
            disposition=CancellationDisposition.ACCEPTED,
            financial_correction_required=record.payment_state is UpstreamPaymentCommercialState.PAID,
            reason_code="merchant_initiated_cancellation",
            evidence_ref=self._next_evidence("merchant-cancel-evidence"),
        )

    def get_fulfillment_projection(self, *, order_ref: str, correlation_ref: str) -> FulfillmentProjection | None:
        record = self._required_matches(order_ref, correlation_ref)
        if record.fulfillment_stage is None:
            return None
        return FulfillmentProjection(
            order_ref=order_ref,
            correlation_ref=correlation_ref,
            stage=record.fulfillment_stage,
            evidence_ref=self._next_evidence("fulfillment-evidence"),
            observed_at_epoch=self._event_counter,
        )

    def reconcile_order_lifecycle(self, *, order_ref: str, correlation_ref: str) -> OrderLifecycleProjection:
        record = self._required_matches(order_ref, correlation_ref)
        return OrderLifecycleProjection(
            order_ref=order_ref,
            correlation_ref=correlation_ref,
            order_state=record.order_state,
            payment_state=record.payment_state,
            fulfillment_state=record.fulfillment_state,
            fulfillment_stage=record.fulfillment_stage,
            evidence_ref=self._next_evidence("lifecycle-evidence"),
            observed_at_epoch=self._event_counter,
        )

    def _required(self, order_ref: str) -> _LifecycleRecord:
        try:
            return self._records[order_ref]
        except KeyError:
            raise KeyError("canonical_order_not_found") from None

    def _required_matches(self, order_ref: str, correlation_ref: str) -> _LifecycleRecord:
        record = self._required(order_ref)
        if record.correlation_ref != correlation_ref:
            raise LifecycleAuthorityMismatch("canonical_order_correlation_mismatch")
        return record

    @staticmethod
    def _classify_cancellation(record: _LifecycleRecord) -> CancellationCase:
        if record.merchant_initiated_cancel:
            return CancellationCase.MERCHANT_INITIATED_CANCELLATION
        if record.fulfillment_stage is not None:
            return CancellationCase.CANCEL_AFTER_PREPARATION_BEGAN
        if record.payment_state is UpstreamPaymentCommercialState.PAID:
            return CancellationCase.CANCEL_AFTER_PAYMENT
        return CancellationCase.CANCEL_BEFORE_PAYMENT

    def _next_evidence(self, prefix: str) -> str:
        self._event_counter += 1
        return f"{prefix}:fixture:{self._event_counter}"

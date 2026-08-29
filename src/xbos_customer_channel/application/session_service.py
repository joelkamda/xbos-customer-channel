from __future__ import annotations

from dataclasses import replace

from ..ports import CustomerSessionStorePort, XBOSStateReconciliationPort
from ..session_state import (
    ChannelState,
    CustomerSessionSnapshot,
    MaterialAction,
    MaterialInputCandidate,
    MaterialInputDecision,
    UpstreamFulfillmentState,
    UpstreamOrderState,
    UpstreamPaymentCommercialState,
    UpstreamStateProjection,
)


class InvalidChannelTransition(ValueError):
    pass


class AuthoritativeEvidenceRequired(PermissionError):
    pass


class SessionReconciliationRequired(RuntimeError):
    pass


class ExplicitConfirmationRequired(ValueError):
    pass


class CustomerSessionService:
    """Deterministic interaction orchestration; never an order/payment/financial authority."""

    TRANSITIONS: dict[ChannelState, frozenset[ChannelState]] = {
        ChannelState.START: frozenset({ChannelState.MERCHANT_CONTEXT, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.MERCHANT_CONTEXT: frozenset({ChannelState.BROWSING, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.BROWSING: frozenset({ChannelState.CART, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.CART: frozenset({ChannelState.BROWSING, ChannelState.SERVICE_MODE, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.SERVICE_MODE: frozenset({ChannelState.CUSTOMER_DETAILS, ChannelState.REVIEW, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.CUSTOMER_DETAILS: frozenset({ChannelState.REVIEW, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.REVIEW: frozenset({ChannelState.ORDER_SUBMITTING, ChannelState.CART, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.ORDER_SUBMITTING: frozenset({ChannelState.ORDER_CREATED, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.ORDER_CREATED: frozenset({ChannelState.PAYMENT_METHOD, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.PAYMENT_METHOD: frozenset({ChannelState.PAYMENT_PENDING, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.PAYMENT_PENDING: frozenset({ChannelState.PAID, ChannelState.PAYMENT_METHOD, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.PAID: frozenset({ChannelState.FULFILLMENT, ChannelState.HUMAN_HANDOFF}),
        ChannelState.FULFILLMENT: frozenset({ChannelState.COMPLETED, ChannelState.HUMAN_HANDOFF, ChannelState.CANCELED}),
        ChannelState.COMPLETED: frozenset(),
        ChannelState.HUMAN_HANDOFF: frozenset({ChannelState.MERCHANT_CONTEXT, ChannelState.BROWSING, ChannelState.CART, ChannelState.REVIEW, ChannelState.ORDER_CREATED, ChannelState.PAYMENT_PENDING, ChannelState.PAID, ChannelState.FULFILLMENT, ChannelState.CANCELED}),
        ChannelState.CANCELED: frozenset(),
    }

    _EVIDENCE_GATED = frozenset(
        {
            ChannelState.ORDER_CREATED,
            ChannelState.PAYMENT_PENDING,
            ChannelState.PAID,
            ChannelState.FULFILLMENT,
            ChannelState.COMPLETED,
        }
    )

    def __init__(
        self,
        *,
        store: CustomerSessionStorePort,
        reconciliation: XBOSStateReconciliationPort,
    ) -> None:
        self._store = store
        self._reconciliation = reconciliation

    def create_session(
        self,
        *,
        session_ref: str,
        conversation_ref: str,
        correlation_ref: str,
        entry_token_ref: str | None = None,
    ) -> CustomerSessionSnapshot:
        if self._store.get(session_ref) is not None:
            raise ValueError("session_already_exists")
        snapshot = CustomerSessionSnapshot(
            session_ref=session_ref,
            conversation_ref=conversation_ref,
            correlation_ref=correlation_ref,
            state=ChannelState.START,
            entry_token_ref=entry_token_ref,
        )
        return self._store.put(snapshot)

    def attach_interaction_refs(
        self,
        session_ref: str,
        *,
        cart_ref: str | None = None,
        quote_ref: str | None = None,
    ) -> CustomerSessionSnapshot:
        current = self._required(session_ref)
        return self._store.put(replace(current, cart_ref=cart_ref or current.cart_ref, quote_ref=quote_ref or current.quote_ref))

    def transition(
        self,
        session_ref: str,
        target: ChannelState,
        *,
        idempotency_key: str,
        upstream_evidence: UpstreamStateProjection | None = None,
        human_handoff_ref: str | None = None,
    ) -> CustomerSessionSnapshot:
        prior = self._store.idempotent_result(session_ref, idempotency_key)
        if prior is not None:
            return prior

        current = self._required(session_ref)
        allowed = self.TRANSITIONS[current.state]
        if target not in allowed:
            raise InvalidChannelTransition(f"invalid_transition:{current.state.value}->{target.value}")

        order_ref = current.order_ref
        payment_ref = current.payment_ref
        evidence_ref = current.last_upstream_evidence_ref
        if target in self._EVIDENCE_GATED:
            if upstream_evidence is None:
                raise AuthoritativeEvidenceRequired(f"upstream_evidence_required:{target.value}")
            self._assert_evidence_matches_session(current, upstream_evidence)
            self._assert_projection_supports(target, upstream_evidence)
            order_ref = upstream_evidence.order_ref or order_ref
            payment_ref = upstream_evidence.payment_ref or payment_ref
            evidence_ref = upstream_evidence.evidence_ref

        updated = replace(
            current,
            state=target,
            order_ref=order_ref,
            payment_ref=payment_ref,
            last_upstream_evidence_ref=evidence_ref,
            human_handoff_ref=human_handoff_ref if target is ChannelState.HUMAN_HANDOFF else current.human_handoff_ref,
            transition_count=current.transition_count + 1,
        )
        self._store.put(updated)
        return self._store.record_idempotent_result(session_ref, idempotency_key, updated)

    def reenter(self, session_ref: str) -> CustomerSessionSnapshot:
        """Reconcile upstream-dependent projection state instead of trusting a stale local snapshot."""
        current = self._required(session_ref)
        if current.state not in self._EVIDENCE_GATED and not current.order_ref and not current.payment_ref:
            return current

        projection = self._reconciliation.reconcile_session(current)
        if projection is None:
            raise SessionReconciliationRequired("authoritative_upstream_state_required_for_reentry")
        self._assert_evidence_matches_session(current, projection)
        projected_state = self._state_from_projection(projection)
        return self._store.put(
            replace(
                current,
                state=projected_state,
                order_ref=projection.order_ref or current.order_ref,
                payment_ref=projection.payment_ref or current.payment_ref,
                last_upstream_evidence_ref=projection.evidence_ref,
            )
        )

    @staticmethod
    def resolve_material_input(
        *,
        action: MaterialAction,
        candidates: tuple[MaterialInputCandidate, ...],
        confirmed_value_ref: str | None = None,
    ) -> MaterialInputDecision:
        if not candidates:
            raise ValueError("no_material_input_candidate")
        unique = {candidate.value_ref: candidate for candidate in candidates}
        if len(unique) > 1 and confirmed_value_ref is None:
            raise ExplicitConfirmationRequired(f"explicit_confirmation_required:{action.value}")
        selected_ref = confirmed_value_ref or next(iter(unique))
        if selected_ref not in unique:
            raise ExplicitConfirmationRequired(f"confirmed_choice_not_in_candidates:{action.value}")
        return MaterialInputDecision(
            action=action,
            selected=unique[selected_ref],
            explicitly_confirmed=confirmed_value_ref is not None,
        )

    def _required(self, session_ref: str) -> CustomerSessionSnapshot:
        session = self._store.get(session_ref)
        if session is None:
            raise KeyError(session_ref)
        return session

    @staticmethod
    def _assert_evidence_matches_session(session: CustomerSessionSnapshot, projection: UpstreamStateProjection) -> None:
        if projection.correlation_ref != session.correlation_ref:
            raise AuthoritativeEvidenceRequired("upstream_correlation_mismatch")
        if session.order_ref is not None and projection.order_ref is not None and session.order_ref != projection.order_ref:
            raise AuthoritativeEvidenceRequired("upstream_order_reference_mismatch")
        if session.payment_ref is not None and projection.payment_ref is not None and session.payment_ref != projection.payment_ref:
            raise AuthoritativeEvidenceRequired("upstream_payment_reference_mismatch")

    @staticmethod
    def _assert_projection_supports(target: ChannelState, projection: UpstreamStateProjection) -> None:
        if target is ChannelState.ORDER_CREATED and projection.order_state not in {UpstreamOrderState.CREATED, UpstreamOrderState.CONFIRMED}:
            raise AuthoritativeEvidenceRequired("order_created_requires_xbos_order_evidence")
        if target is ChannelState.PAYMENT_PENDING and projection.payment_state is not UpstreamPaymentCommercialState.PENDING:
            raise AuthoritativeEvidenceRequired("payment_pending_requires_xbos_commercial_evidence")
        if target is ChannelState.PAID and projection.payment_state is not UpstreamPaymentCommercialState.PAID:
            raise AuthoritativeEvidenceRequired("paid_requires_xbos_commercial_evidence")
        if target is ChannelState.FULFILLMENT and projection.fulfillment_state is not UpstreamFulfillmentState.ACTIVE:
            raise AuthoritativeEvidenceRequired("fulfillment_requires_xbos_fulfillment_evidence")
        if target is ChannelState.COMPLETED and projection.fulfillment_state is not UpstreamFulfillmentState.COMPLETED:
            raise AuthoritativeEvidenceRequired("completed_requires_xbos_fulfillment_evidence")

    @staticmethod
    def _state_from_projection(projection: UpstreamStateProjection) -> ChannelState:
        if projection.order_state is UpstreamOrderState.CANCELED or projection.fulfillment_state is UpstreamFulfillmentState.CANCELED:
            return ChannelState.CANCELED
        if projection.fulfillment_state is UpstreamFulfillmentState.COMPLETED:
            return ChannelState.COMPLETED
        if projection.fulfillment_state is UpstreamFulfillmentState.ACTIVE:
            return ChannelState.FULFILLMENT
        if projection.payment_state is UpstreamPaymentCommercialState.PAID:
            return ChannelState.PAID
        if projection.payment_state is UpstreamPaymentCommercialState.PENDING:
            return ChannelState.PAYMENT_PENDING
        if projection.order_state in {UpstreamOrderState.CREATED, UpstreamOrderState.CONFIRMED}:
            return ChannelState.ORDER_CREATED
        raise SessionReconciliationRequired("upstream_projection_has_no_channel_state")

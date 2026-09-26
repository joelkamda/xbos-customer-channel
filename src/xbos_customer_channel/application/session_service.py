from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import datetime, timezone

from ..entry_context import ResolvedEntryContext
from ..ports import ChannelProvenanceStorePort, CustomerSessionStorePort, IdentityBindingStorePort, XBOSContextPort, XBOSStateReconciliationPort
from ..provenance import binding_matches_session
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


class SessionSecurityRequired(PermissionError):
    pass


class CustomerSessionService:
    """Interaction orchestration. Session bindings are security metadata, never domain truth."""

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
        provenance_store: ChannelProvenanceStorePort | None = None,
        xbos_context: XBOSContextPort | None = None,
        identity_binding_store: IdentityBindingStorePort | None = None,
    ) -> None:
        self._store = store
        self._reconciliation = reconciliation
        self._provenance_store = provenance_store
        self._xbos_context = xbos_context
        self._identity_binding_store = identity_binding_store

    @staticmethod
    def _new_session_ref() -> str:
        return "sess_" + secrets.token_urlsafe(32)

    def _attest_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        dining_area_ref: str | None,
        purpose,
        context_binding_ref: str,
        correlation_ref: str,
        effective_at_epoch: int,
    ):
        if self._xbos_context is None:
            raise SessionSecurityRequired("xbos_context_reattestation_required")
        bound = getattr(self._xbos_context, "attest_bound_context", None)
        try:
            if callable(bound):
                return bound(
                    context_binding_ref=context_binding_ref,
                    merchant_ref=merchant_ref,
                    location_ref=location_ref,
                    table_ref=table_ref,
                    purpose=purpose,
                    dining_area_ref=dining_area_ref,
                    effective_at=datetime.fromtimestamp(
                        effective_at_epoch,
                        tz=timezone.utc,
                    ),
                    correlation_ref=correlation_ref,
                )
            return self._xbos_context.attest_context(
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                table_ref=table_ref,
                purpose=purpose,
                dining_area_ref=dining_area_ref,
            )
        except (KeyError, PermissionError, ValueError):
            raise SessionSecurityRequired("entry_context_unavailable") from None

    def _assert_entry_context_current(
        self,
        entry_context: ResolvedEntryContext,
        *,
        now_epoch: int,
        correlation_ref: str,
    ) -> None:
        attestation = self._attest_context(
            merchant_ref=entry_context.merchant_ref,
            location_ref=entry_context.location_ref,
            table_ref=entry_context.table_ref,
            purpose=entry_context.purpose,
            dining_area_ref=entry_context.dining_area_ref,
            context_binding_ref=entry_context.context_binding_ref,
            correlation_ref=correlation_ref,
            effective_at_epoch=now_epoch,
        )
        expected = (
            entry_context.tenant_ref,
            entry_context.merchant_ref,
            entry_context.location_ref,
            entry_context.table_ref,
            entry_context.dining_area_ref,
            entry_context.purpose,
            entry_context.context_binding_ref,
        )
        observed = (
            attestation.tenant_ref,
            attestation.merchant_ref,
            attestation.location_ref,
            attestation.table_ref,
            attestation.dining_area_ref,
            attestation.purpose,
            attestation.context_binding_ref,
        )
        if expected != observed:
            raise SessionSecurityRequired("stale_entry_context_rejected")

    def _binding_for_entry_context(
        self,
        *,
        identity_binding_ref: str,
        conversation_ref: str,
        entry_context: ResolvedEntryContext,
        now_epoch: int,
    ):
        if self._identity_binding_store is None:
            raise SessionSecurityRequired("identity_binding_store_required")
        binding = self._identity_binding_store.resolve_binding(identity_binding_ref)
        if binding is None:
            raise SessionSecurityRequired("unknown_identity_binding")
        if now_epoch >= binding.expires_at_epoch:
            raise SessionSecurityRequired("identity_binding_expired")
        if binding.conversation_ref != conversation_ref:
            raise SessionSecurityRequired("identity_binding_conversation_mismatch")
        expected = (
            entry_context.tenant_ref,
            entry_context.merchant_ref,
            entry_context.location_ref,
            entry_context.table_ref,
            entry_context.dining_area_ref,
            entry_context.context_binding_ref,
        )
        observed = (
            binding.tenant_ref,
            binding.merchant_ref,
            binding.location_ref,
            binding.table_ref,
            binding.dining_area_ref,
            binding.context_binding_ref,
        )
        if expected != observed:
            raise SessionSecurityRequired("identity_binding_context_mismatch")
        return binding

    def create_session(
        self,
        *,
        conversation_ref: str,
        correlation_ref: str,
        identity_binding_ref: str | None = None,
        owner_identity_ref: str | None = None,
        entry_context: ResolvedEntryContext | None = None,
        now_epoch: int | None = None,
        expires_at_epoch: int | None = None,
        session_ref: str | None = None,
        entry_token_ref: str | None = None,
    ) -> CustomerSessionSnapshot:
        """Create a server-issued session.

        Security-significant sessions accept only a server-issued CR3 identity
        binding handle. ``owner_identity_ref`` remains in the signature solely so
        raw caller identity assertions fail closed rather than being mistaken for
        authority. Historical fixture aliases remain non-resumable.
        """
        actual_ref = self._new_session_ref()
        while self._store.get(actual_ref) is not None:
            actual_ref = self._new_session_ref()

        secure = (
            identity_binding_ref is not None
            or owner_identity_ref is not None
            or entry_context is not None
            or expires_at_epoch is not None
        )
        if secure:
            if owner_identity_ref is not None:
                raise SessionSecurityRequired("caller_owner_identity_ref_not_authority")
            if identity_binding_ref is None or entry_context is None or now_epoch is None or expires_at_epoch is None:
                raise SessionSecurityRequired("secure_session_binding_incomplete")
            if expires_at_epoch <= now_epoch:
                raise SessionSecurityRequired("session_expiry_must_be_future")
            if entry_token_ref is not None and entry_token_ref != entry_context.token_ref:
                raise SessionSecurityRequired("entry_token_binding_mismatch")
            self._assert_entry_context_current(
                entry_context,
                now_epoch=now_epoch,
                correlation_ref=correlation_ref,
            )
            binding = self._binding_for_entry_context(
                identity_binding_ref=identity_binding_ref,
                conversation_ref=conversation_ref,
                entry_context=entry_context,
                now_epoch=now_epoch,
            )
            if expires_at_epoch > binding.expires_at_epoch:
                raise SessionSecurityRequired("session_outlives_identity_binding")
            snapshot = CustomerSessionSnapshot(
                session_ref=actual_ref,
                conversation_ref=conversation_ref,
                correlation_ref=correlation_ref,
                state=ChannelState.START,
                entry_token_ref=entry_context.token_ref,
                owner_identity_ref=binding.identity_ref,
                tenant_ref=entry_context.tenant_ref,
                merchant_ref=entry_context.merchant_ref,
                location_ref=entry_context.location_ref,
                table_ref=entry_context.table_ref,
                dining_area_ref=entry_context.dining_area_ref,
                entry_purpose=entry_context.purpose,
                context_binding_ref=entry_context.context_binding_ref,
                created_at_epoch=now_epoch,
                expires_at_epoch=expires_at_epoch,
                generation=0,
                security_binding_complete=True,
            )
            return self._store.put(snapshot)

        # Historical test compatibility only. The caller value is an alias, not a
        # session identity, and cannot be resumed through the security path.
        snapshot = CustomerSessionSnapshot(
            session_ref=actual_ref,
            conversation_ref=conversation_ref,
            correlation_ref=correlation_ref,
            state=ChannelState.START,
            entry_token_ref=entry_token_ref,
            security_binding_complete=False,
        )
        stored = self._store.put_with_legacy_alias(snapshot, session_ref)
        if session_ref is not None:
            return replace(stored, session_ref=session_ref)
        return stored

    def resume_session(
        self,
        *,
        session_ref: str,
        identity_binding_ref: str | None = None,
        owner_identity_ref: str | None = None,
        now_epoch: int,
    ) -> CustomerSessionSnapshot:
        if owner_identity_ref is not None:
            raise SessionSecurityRequired("caller_owner_identity_ref_not_authority")
        if identity_binding_ref is None:
            raise SessionSecurityRequired("identity_binding_required")
        if self._identity_binding_store is None:
            raise SessionSecurityRequired("identity_binding_store_required")
        if self._store.canonical_ref(session_ref) != session_ref:
            raise SessionSecurityRequired("fixture_alias_not_resumable")
        current = self._required(session_ref)
        if not current.security_binding_complete:
            raise SessionSecurityRequired("secure_session_binding_required")
        binding = self._identity_binding_store.resolve_binding(identity_binding_ref)
        if binding is None:
            raise SessionSecurityRequired("unknown_identity_binding")
        if now_epoch >= binding.expires_at_epoch:
            raise SessionSecurityRequired("identity_binding_expired")
        if binding.identity_ref != current.owner_identity_ref:
            raise PermissionError("session_owner_mismatch")
        if binding.conversation_ref != current.conversation_ref:
            raise SessionSecurityRequired("identity_binding_conversation_mismatch")
        bound_identity_context = (
            current.tenant_ref,
            current.merchant_ref,
            current.location_ref,
            current.table_ref,
            current.dining_area_ref,
            current.context_binding_ref,
        )
        observed_identity_context = (
            binding.tenant_ref,
            binding.merchant_ref,
            binding.location_ref,
            binding.table_ref,
            binding.dining_area_ref,
            binding.context_binding_ref,
        )
        if bound_identity_context != observed_identity_context:
            raise SessionSecurityRequired("identity_binding_context_mismatch")
        if current.expires_at_epoch is None or now_epoch >= current.expires_at_epoch:
            raise PermissionError("session_expired")
        if current.invalidated_at_epoch is not None or current.rotated_to_session_ref is not None:
            raise PermissionError("session_stale_or_rotated")
        if self._xbos_context is None:
            raise SessionSecurityRequired("xbos_context_reattestation_required")
        if (
            current.merchant_ref is None
            or current.location_ref is None
            or current.entry_purpose is None
            or current.context_binding_ref is None
        ):
            raise SessionSecurityRequired("bound_context_incomplete")

        attestation = self._attest_context(
            merchant_ref=current.merchant_ref,
            location_ref=current.location_ref,
            table_ref=current.table_ref,
            purpose=current.entry_purpose,
            dining_area_ref=current.dining_area_ref,
            context_binding_ref=current.context_binding_ref,
            correlation_ref=current.correlation_ref,
            effective_at_epoch=now_epoch,
        )

        bound = (
            current.tenant_ref,
            current.merchant_ref,
            current.location_ref,
            current.table_ref,
            current.dining_area_ref,
            current.entry_purpose,
            current.context_binding_ref,
        )
        current_attested = (
            attestation.tenant_ref,
            attestation.merchant_ref,
            attestation.location_ref,
            attestation.table_ref,
            attestation.dining_area_ref,
            attestation.purpose,
            attestation.context_binding_ref,
        )
        if bound != current_attested:
            raise SessionSecurityRequired("stale_entry_context_rejected")

        replacement = replace(
            current,
            session_ref=self._new_session_ref(),
            predecessor_session_ref=current.session_ref,
            rotated_to_session_ref=None,
            invalidated_at_epoch=None,
            generation=current.generation + 1,
        )
        rotated = self._store.rotate_if_active(
            session_ref=current.session_ref,
            expected_owner_identity_ref=current.owner_identity_ref or "",
            now_epoch=now_epoch,
            replacement=replacement,
        )
        if rotated is None:
            raise PermissionError("session_stale_or_rotated")
        return rotated

    def validate_active_session(
        self,
        *,
        session_ref: str,
        now_epoch: int,
    ) -> CustomerSessionSnapshot:
        """Revalidate the active CR1 session/context binding before sensitive use."""
        current = self._required(session_ref)
        if not current.security_binding_complete:
            raise SessionSecurityRequired("secure_session_binding_required")
        if current.invalidated_at_epoch is not None or current.rotated_to_session_ref is not None:
            raise PermissionError("session_stale_or_rotated")
        if current.expires_at_epoch is None or now_epoch >= current.expires_at_epoch:
            raise PermissionError("session_expired")
        if self._xbos_context is None:
            raise SessionSecurityRequired("xbos_context_reattestation_required")
        if (
            current.merchant_ref is None
            or current.location_ref is None
            or current.entry_purpose is None
            or current.context_binding_ref is None
        ):
            raise SessionSecurityRequired("bound_context_incomplete")
        attestation = self._attest_context(
            merchant_ref=current.merchant_ref,
            location_ref=current.location_ref,
            table_ref=current.table_ref,
            purpose=current.entry_purpose,
            dining_area_ref=current.dining_area_ref,
            context_binding_ref=current.context_binding_ref,
            correlation_ref=current.correlation_ref,
            effective_at_epoch=now_epoch,
        )
        bound = (
            current.tenant_ref,
            current.merchant_ref,
            current.location_ref,
            current.table_ref,
            current.dining_area_ref,
            current.entry_purpose,
            current.context_binding_ref,
        )
        observed = (
            attestation.tenant_ref,
            attestation.merchant_ref,
            attestation.location_ref,
            attestation.table_ref,
            attestation.dining_area_ref,
            attestation.purpose,
            attestation.context_binding_ref,
        )
        if bound != observed:
            raise SessionSecurityRequired("stale_entry_context_rejected")
        return current

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
        evidence_handle_ref: str | None = None,
        upstream_evidence: UpstreamStateProjection | None = None,
        human_handoff_ref: str | None = None,
    ) -> CustomerSessionSnapshot:
        prior = self._store.idempotent_result(session_ref, idempotency_key)
        if prior is not None:
            return prior

        current = self._required(session_ref)
        if current.invalidated_at_epoch is not None or current.rotated_to_session_ref is not None:
            raise PermissionError("session_stale_or_rotated")
        allowed = self.TRANSITIONS[current.state]
        if target not in allowed:
            raise InvalidChannelTransition(f"invalid_transition:{current.state.value}->{target.value}")

        if upstream_evidence is not None:
            raise AuthoritativeEvidenceRequired("caller_supplied_upstream_projection_not_authority")

        order_ref = current.order_ref
        payment_ref = current.payment_ref
        evidence_ref = current.last_upstream_evidence_ref
        if target in self._EVIDENCE_GATED:
            projection = self._resolve_authoritative_projection(
                current,
                evidence_handle_ref=evidence_handle_ref,
            )
            self._assert_evidence_matches_session(current, projection)
            self._assert_projection_supports(target, projection)
            order_ref = projection.order_ref or order_ref
            payment_ref = projection.payment_ref or payment_ref
            evidence_ref = projection.evidence_ref

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

    def _resolve_authoritative_projection(
        self,
        session: CustomerSessionSnapshot,
        *,
        evidence_handle_ref: str | None,
    ) -> UpstreamStateProjection:
        if evidence_handle_ref is not None:
            if self._provenance_store is None:
                raise AuthoritativeEvidenceRequired("server_provenance_store_required")
            record = self._provenance_store.resolve_evidence(evidence_handle_ref)
            if record is None:
                raise AuthoritativeEvidenceRequired("unknown_server_evidence_handle")
            if not binding_matches_session(record.binding, session):
                raise AuthoritativeEvidenceRequired("server_evidence_session_context_mismatch")
            return record.projection

        projection = self._reconciliation.reconcile_session(session)
        if projection is None:
            raise AuthoritativeEvidenceRequired("server_side_authoritative_evidence_required")
        return projection

    def reenter(self, session_ref: str) -> CustomerSessionSnapshot:
        """Business-state reconciliation only; secure owner/session resume is ``resume_session``."""
        canonical_ref = self._store.canonical_ref(session_ref)
        current = self._required(session_ref)
        if current.state not in self._EVIDENCE_GATED and not current.order_ref and not current.payment_ref:
            return replace(current, session_ref=session_ref) if canonical_ref != session_ref else current

        projection = self._reconciliation.reconcile_session(current)
        if projection is None:
            raise SessionReconciliationRequired("authoritative_upstream_state_required_for_reentry")
        self._assert_evidence_matches_session(current, projection)
        projected_state = self._state_from_projection(projection)
        updated = self._store.put(
            replace(
                current,
                state=projected_state,
                order_ref=projection.order_ref or current.order_ref,
                payment_ref=projection.payment_ref or current.payment_ref,
                last_upstream_evidence_ref=projection.evidence_ref,
            )
        )
        return replace(updated, session_ref=session_ref) if canonical_ref != session_ref else updated

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

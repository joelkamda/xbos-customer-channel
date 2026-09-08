from __future__ import annotations

from ..application.catalog_service import CatalogQuoteService, CatalogSession
from ..entry_context import EntryPurpose
from ..order import (
    AuthoritativeOrderConfirmationSnapshot,
    CanonicalOrderProjection,
    CanonicalOrderState,
    OrderSubmissionConflict,
    OrderSubmissionUnknown,
    OrderSubmitRequest,
    OrderTransportUnknown,
    ServiceMode,
    ServiceModeUnavailable,
)
from ..ports import ChannelProvenanceStorePort, CustomerSessionStorePort, XBOSOrderPort
from ..provenance import (
    ProvenanceConflict,
    ServerIssuedConfirmation,
    binding_matches_session,
    confirmation_commercial_fingerprint,
)
from ..session_state import ChannelState, UpstreamOrderState, UpstreamStateProjection
from .session_service import CustomerSessionService, InvalidChannelTransition


class OrderConfirmationRejected(RuntimeError):
    pass


class OrderDraftConfirmationService:
    """Channel orchestration only. XBOS owns service policy, commercial snapshot and canonical order."""

    def __init__(
        self,
        *,
        catalog: CatalogQuoteService,
        order_port: XBOSOrderPort,
        sessions: CustomerSessionService,
        session_store: CustomerSessionStorePort,
        provenance_store: ChannelProvenanceStorePort,
    ) -> None:
        self._catalog = catalog
        self._orders = order_port
        self._sessions = sessions
        self._session_store = session_store
        self._provenance = provenance_store

    def prepare_confirmation(
        self,
        *,
        session_ref: str,
        catalog_session: CatalogSession,
        service_mode: ServiceMode,
        now_epoch: int,
        acknowledged_quote_ref: str | None = None,
        contact_ref: str | None = None,
        delivery_address_ref: str | None = None,
    ) -> ServerIssuedConfirmation:
        entry = catalog_session.entry
        session = self._require_active_secure_session(session_ref, now_epoch=now_epoch)
        self._assert_session_matches_entry(session, catalog_session)

        if service_mode.value not in entry.projection.allowed_fulfillment_modes:
            raise ServiceModeUnavailable("service_mode_not_allowed_by_xbos_context")

        table_ref: str | None = None
        if service_mode is ServiceMode.DINE_IN:
            if entry.purpose is not EntryPurpose.DINE_IN or entry.table_ref is None:
                raise ServiceModeUnavailable("valid_xc3_dine_in_table_context_required")
            table_ref = entry.table_ref
        elif service_mode is ServiceMode.TAKEAWAY:
            if not contact_ref:
                raise ServiceModeUnavailable("takeaway_contact_required")
        elif service_mode is ServiceMode.DELIVERY:
            if not contact_ref or not delivery_address_ref:
                raise ServiceModeUnavailable("delivery_contact_and_address_required")

        review = self._catalog.review_for_confirmation(catalog_session, now_epoch=now_epoch)
        ack_ref = acknowledged_quote_ref
        if not review.requires_reconfirmation:
            ack_ref = review.quote.quote_ref
        accepted_quote = self._catalog.accept_review(review, acknowledged_quote_ref=ack_ref or "")

        service_context = self._orders.resolve_service_context(
            merchant_ref=entry.merchant_ref,
            location_ref=entry.location_ref,
            mode=service_mode,
            table_ref=table_ref,
            contact_ref=contact_ref,
            delivery_address_ref=delivery_address_ref,
        )
        confirmation = self._orders.prepare_order_confirmation(
            quote=accepted_quote,
            service_context=service_context,
            now_epoch=now_epoch,
        )
        self._assert_confirmation_matches_authority(
            confirmation=confirmation,
            quote_ref=accepted_quote.quote_ref,
            quote_version=accepted_quote.version,
            merchant_ref=entry.merchant_ref,
            location_ref=entry.location_ref,
            currency=accepted_quote.currency,
            service_mode=service_mode,
        )
        return self._provenance.issue_confirmation(session=session, confirmation=confirmation)

    def submit(
        self,
        *,
        session_ref: str,
        confirmation_handle_ref: str,
        client_submit_ref: str,
        now_epoch: int,
    ) -> CanonicalOrderProjection:
        session = self._require_active_secure_session(session_ref, now_epoch=now_epoch)
        record = self._provenance.resolve_confirmation(confirmation_handle_ref)
        if record is None:
            raise OrderConfirmationRejected("unknown_server_confirmation_handle")
        if not binding_matches_session(record.binding, session):
            raise OrderConfirmationRejected("confirmation_session_context_mismatch")

        authoritative = self._orders.resolve_order_confirmation(record.authoritative_xbos_confirmation_ref)
        if authoritative is None:
            raise OrderConfirmationRejected("authoritative_confirmation_unavailable")
        if authoritative.expires_at_epoch <= now_epoch:
            raise OrderConfirmationRejected("authoritative_confirmation_expired")
        if confirmation_commercial_fingerprint(authoritative) != record.commercial_fingerprint:
            raise OrderConfirmationRejected("authoritative_confirmation_material_changed")
        if (
            authoritative.quote_ref != record.quote_ref
            or authoritative.quote_version != record.quote_version
            or authoritative.service_context_ref != record.service_context_ref
            or authoritative.service_mode is not record.service_mode
        ):
            raise OrderConfirmationRejected("authoritative_confirmation_record_mismatch")

        try:
            self._provenance.claim_confirmation(
                confirmation_handle_ref=confirmation_handle_ref,
                client_submit_ref=client_submit_ref,
            )
        except (KeyError, ProvenanceConflict) as exc:
            raise OrderSubmissionConflict(str(exc)) from None

        if session.state is ChannelState.REVIEW:
            self._sessions.transition(
                session_ref,
                ChannelState.ORDER_SUBMITTING,
                idempotency_key=f"xc6-submit-start:{client_submit_ref}",
            )
            session = self._session_store.get(session_ref)
            assert session is not None
        elif session.state not in {ChannelState.ORDER_SUBMITTING, ChannelState.ORDER_CREATED}:
            raise InvalidChannelTransition(f"xc6_submit_requires_review_or_retry_state:{session.state.value}")

        request = OrderSubmitRequest(
            client_submit_ref=client_submit_ref,
            correlation_ref=session.correlation_ref,
            confirmation_ref=record.authoritative_xbos_confirmation_ref,
        )

        try:
            order = self._orders.submit_order(request)
        except OrderSubmissionConflict:
            raise
        except OrderTransportUnknown:
            order = self._orders.get_order_by_client_ref(client_submit_ref)
            if order is None:
                raise OrderSubmissionUnknown("canonical_order_outcome_unknown_reconcile_required") from None

        self._assert_order_result_matches_request(order, request)

        latest = self._session_store.get(session_ref)
        assert latest is not None
        if latest.state is ChannelState.ORDER_CREATED:
            if latest.order_ref is not None and latest.order_ref != order.order_ref:
                raise OrderConfirmationRejected("existing_channel_order_projection_mismatch")
            return order
        if latest.state is not ChannelState.ORDER_SUBMITTING:
            raise InvalidChannelTransition(f"unexpected_submit_completion_state:{latest.state.value}")

        evidence = UpstreamStateProjection(
            evidence_ref=order.evidence_ref,
            correlation_ref=order.correlation_ref,
            observed_at_epoch=now_epoch,
            order_ref=order.order_ref,
            order_state=(
                UpstreamOrderState.CONFIRMED
                if order.state is CanonicalOrderState.CONFIRMED
                else UpstreamOrderState.CREATED
            ),
        )
        evidence_handle_ref = self._provenance.issue_evidence(session=latest, projection=evidence)
        self._sessions.transition(
            session_ref,
            ChannelState.ORDER_CREATED,
            idempotency_key=f"xc6-submit-result:{client_submit_ref}",
            evidence_handle_ref=evidence_handle_ref,
        )
        return order

    def _require_active_secure_session(self, session_ref: str, *, now_epoch: int):
        try:
            return self._sessions.validate_active_session(session_ref=session_ref, now_epoch=now_epoch)
        except (PermissionError, RuntimeError, ValueError) as exc:
            raise OrderConfirmationRejected(str(exc)) from None

    @staticmethod
    def _assert_session_matches_entry(session, catalog_session: CatalogSession) -> None:
        entry = catalog_session.entry
        if (session.merchant_ref, session.location_ref) != (entry.merchant_ref, entry.location_ref):
            raise OrderConfirmationRejected("session_entry_merchant_location_mismatch")
        if session.table_ref != entry.table_ref:
            raise OrderConfirmationRejected("session_entry_table_mismatch")
        if session.dining_area_ref != entry.dining_area_ref:
            raise OrderConfirmationRejected("session_entry_dining_area_mismatch")
        if session.entry_purpose is not entry.purpose:
            raise OrderConfirmationRejected("session_entry_purpose_mismatch")
        if session.context_binding_ref != entry.context_binding_ref:
            raise OrderConfirmationRejected("session_entry_context_binding_mismatch")

    @staticmethod
    def _assert_confirmation_matches_authority(
        *,
        confirmation: AuthoritativeOrderConfirmationSnapshot,
        quote_ref: str,
        quote_version: str,
        merchant_ref: str,
        location_ref: str,
        currency: str,
        service_mode: ServiceMode,
    ) -> None:
        if confirmation.quote_ref != quote_ref or confirmation.quote_version != quote_version:
            raise OrderConfirmationRejected("authoritative_confirmation_quote_mismatch")
        if (confirmation.merchant_ref, confirmation.location_ref) != (merchant_ref, location_ref):
            raise OrderConfirmationRejected("authoritative_confirmation_context_mismatch")
        if confirmation.currency != currency:
            raise OrderConfirmationRejected("authoritative_confirmation_currency_mismatch")
        if confirmation.service_mode is not service_mode:
            raise OrderConfirmationRejected("authoritative_confirmation_service_mode_mismatch")

    @staticmethod
    def _assert_order_result_matches_request(order: CanonicalOrderProjection, request: OrderSubmitRequest) -> None:
        if order.client_submit_ref != request.client_submit_ref:
            raise OrderConfirmationRejected("canonical_order_client_reference_mismatch")
        if order.correlation_ref != request.correlation_ref:
            raise OrderConfirmationRejected("canonical_order_correlation_mismatch")
        if order.confirmation_ref != request.confirmation_ref:
            raise OrderConfirmationRejected("canonical_order_confirmation_reference_mismatch")

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
from ..ports import CustomerSessionStorePort, XBOSOrderPort
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
    ) -> None:
        self._catalog = catalog
        self._orders = order_port
        self._sessions = sessions
        self._session_store = session_store

    def prepare_confirmation(
        self,
        *,
        catalog_session: CatalogSession,
        service_mode: ServiceMode,
        now_epoch: int,
        acknowledged_quote_ref: str | None = None,
        contact_ref: str | None = None,
        delivery_address_ref: str | None = None,
    ) -> AuthoritativeOrderConfirmationSnapshot:
        entry = catalog_session.entry
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
        return confirmation

    def submit(
        self,
        *,
        session_ref: str,
        confirmation: AuthoritativeOrderConfirmationSnapshot,
        client_submit_ref: str,
    ) -> CanonicalOrderProjection:
        session = self._session_store.get(session_ref)
        if session is None:
            raise KeyError(session_ref)

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
            confirmation_ref=confirmation.confirmation_ref,
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
            observed_at_epoch=0,
            order_ref=order.order_ref,
            order_state=(
                UpstreamOrderState.CONFIRMED
                if order.state is CanonicalOrderState.CONFIRMED
                else UpstreamOrderState.CREATED
            ),
        )
        self._sessions.transition(
            session_ref,
            ChannelState.ORDER_CREATED,
            idempotency_key=f"xc6-submit-result:{client_submit_ref}",
            upstream_evidence=evidence,
        )
        return order

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

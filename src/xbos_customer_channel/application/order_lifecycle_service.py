from __future__ import annotations

from ..order_lifecycle import (
    CancellationDecision,
    CancellationRequest,
    ExplicitLifecycleConfirmationRequired,
    FulfillmentProjection,
    LifecycleAuthorityMismatch,
    OrderChangeDecision,
    OrderChangeRequest,
    OrderLifecycleProjection,
)
from ..ports import CustomerSessionStorePort, XBOSOrderChangeFulfillmentPort


class OrderChangeFulfillmentService:
    """Request/present/confirm orchestration only. XBOS owns change policy, cancellation and fulfillment truth."""

    def __init__(
        self,
        *,
        lifecycle_port: XBOSOrderChangeFulfillmentPort,
        session_store: CustomerSessionStorePort,
    ) -> None:
        self._lifecycle = lifecycle_port
        self._sessions = session_store

    def request_change(
        self,
        *,
        session_ref: str,
        requested_change_ref: str,
        client_change_ref: str,
        explicitly_confirmed: bool,
    ) -> OrderChangeDecision:
        session = self._required_order_session(session_ref)
        if not explicitly_confirmed:
            raise ExplicitLifecycleConfirmationRequired("explicit_confirmation_required:order_change")
        return self._lifecycle.request_order_change(
            OrderChangeRequest(
                client_change_ref=client_change_ref,
                correlation_ref=session.correlation_ref,
                order_ref=session.order_ref or "",
                requested_change_ref=requested_change_ref,
            )
        )

    def request_cancellation(
        self,
        *,
        session_ref: str,
        reason_ref: str,
        client_cancel_ref: str,
        explicitly_confirmed: bool,
    ) -> CancellationDecision:
        session = self._required_order_session(session_ref)
        if not explicitly_confirmed:
            raise ExplicitLifecycleConfirmationRequired("explicit_confirmation_required:cancellation")
        return self._lifecycle.request_cancellation(
            CancellationRequest(
                client_cancel_ref=client_cancel_ref,
                correlation_ref=session.correlation_ref,
                order_ref=session.order_ref or "",
                reason_ref=reason_ref,
            )
        )

    def merchant_cancellation_projection(self, *, session_ref: str) -> CancellationDecision | None:
        session = self._required_order_session(session_ref)
        return self._lifecycle.get_cancellation_projection(
            order_ref=session.order_ref or "",
            correlation_ref=session.correlation_ref,
        )

    def fulfillment_projection(self, *, session_ref: str) -> FulfillmentProjection | None:
        session = self._required_order_session(session_ref)
        projection = self._lifecycle.get_fulfillment_projection(
            order_ref=session.order_ref or "",
            correlation_ref=session.correlation_ref,
        )
        if projection is not None:
            self._assert_projection_matches_session(
                order_ref=projection.order_ref,
                correlation_ref=projection.correlation_ref,
                expected_order_ref=session.order_ref or "",
                expected_correlation_ref=session.correlation_ref,
            )
        return projection

    def reconcile_reentry(self, *, session_ref: str) -> OrderLifecycleProjection:
        """Always query the typed XBOS lifecycle boundary; never trust stale local order/fulfillment state."""
        session = self._required_order_session(session_ref)
        projection = self._lifecycle.reconcile_order_lifecycle(
            order_ref=session.order_ref or "",
            correlation_ref=session.correlation_ref,
        )
        self._assert_projection_matches_session(
            order_ref=projection.order_ref,
            correlation_ref=projection.correlation_ref,
            expected_order_ref=session.order_ref or "",
            expected_correlation_ref=session.correlation_ref,
        )
        return projection

    def _required_order_session(self, session_ref: str):
        session = self._sessions.get(session_ref)
        if session is None:
            raise KeyError(session_ref)
        if not session.order_ref:
            raise LifecycleAuthorityMismatch("canonical_order_reference_required")
        return session

    @staticmethod
    def _assert_projection_matches_session(
        *,
        order_ref: str,
        correlation_ref: str,
        expected_order_ref: str,
        expected_correlation_ref: str,
    ) -> None:
        if order_ref != expected_order_ref:
            raise LifecycleAuthorityMismatch("upstream_order_reference_mismatch")
        if correlation_ref != expected_correlation_ref:
            raise LifecycleAuthorityMismatch("upstream_correlation_mismatch")

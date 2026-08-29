from __future__ import annotations

from ..payment_experience import (
    CanonicalPaymentRequestProjection,
    FakePaymentOutcome,
    FakePaymentResult,
    NextAction,
    PaymentMethodCode,
    PaymentMethodNotPermitted,
    PaymentRequestAuthorityMismatch,
)
from ..ports import CustomerSessionStorePort, XBOSPaymentRequestPort
from ..adapters.fake_payment_ux import FakePaymentUXAdapter


class PaymentExperienceService:
    """Payment presentation/orchestration only; owns no amount, method policy, provider route or payment truth."""

    def __init__(
        self,
        *,
        payment_request_port: XBOSPaymentRequestPort,
        session_store: CustomerSessionStorePort,
        fake_payment_ux: FakePaymentUXAdapter,
    ) -> None:
        self._payment_requests = payment_request_port
        self._sessions = session_store
        self._fake_payment_ux = fake_payment_ux

    def payment_request(self, *, session_ref: str) -> CanonicalPaymentRequestProjection:
        session = self._required_order_session(session_ref)
        projection = self._payment_requests.get_payment_request(
            order_ref=session.order_ref or "",
            correlation_ref=session.correlation_ref,
        )
        self._assert_projection_matches_session(
            projection=projection,
            order_ref=session.order_ref or "",
            correlation_ref=session.correlation_ref,
        )
        return projection

    def present_method(
        self,
        *,
        payment_request: CanonicalPaymentRequestProjection,
        method: PaymentMethodCode,
    ) -> NextAction:
        for presentation in payment_request.methods:
            if presentation.method is method:
                return presentation.next_action
        raise PaymentMethodNotPermitted(f"payment_method_not_permitted:{method.value}")

    def simulate_fake_payment(
        self,
        *,
        payment_request: CanonicalPaymentRequestProjection,
        fixture_ref: str,
        outcome: FakePaymentOutcome,
    ) -> FakePaymentResult:
        return self._fake_payment_ux.simulate(
            fixture_ref=fixture_ref,
            payment_request_ref=payment_request.payment_request_ref,
            outcome=outcome,
        )

    def _required_order_session(self, session_ref: str):
        session = self._sessions.get(session_ref)
        if session is None:
            raise KeyError(session_ref)
        if not session.order_ref:
            raise PaymentRequestAuthorityMismatch("canonical_order_reference_required")
        return session

    @staticmethod
    def _assert_projection_matches_session(
        *,
        projection: CanonicalPaymentRequestProjection,
        order_ref: str,
        correlation_ref: str,
    ) -> None:
        if projection.order_ref != order_ref:
            raise PaymentRequestAuthorityMismatch("payment_request_order_reference_mismatch")
        if projection.correlation_ref != correlation_ref:
            raise PaymentRequestAuthorityMismatch("payment_request_correlation_mismatch")

from __future__ import annotations

from ..models import PaymentRequest, PaymentState, Quote


class FakePaymentService:
    """Typed payment fixture; it returns simulated external authority state and owns no ledger."""

    def __init__(self, terminal_state: PaymentState = PaymentState.SUCCEEDED) -> None:
        self.terminal_state = terminal_state
        self._requests_by_key: dict[str, PaymentRequest] = {}

    def create_payment_request(self, quote: Quote, idempotency_key: str) -> PaymentRequest:
        if idempotency_key not in self._requests_by_key:
            self._requests_by_key[idempotency_key] = PaymentRequest(
                "payment:fixture:1", quote.amount, quote.currency, PaymentState.PENDING
            )
        return self._requests_by_key[idempotency_key]

    def get_payment_status(self, payment_ref: str) -> PaymentState:
        return self.terminal_state

    def cancel_payment_request_if_supported(self, payment_ref: str, idempotency_key: str) -> PaymentState:
        return PaymentState.CANCELED

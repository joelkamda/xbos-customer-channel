from __future__ import annotations

from ..payment_experience import FakePaymentOutcome, FakePaymentResult, NextAction, NextActionType


class FakePaymentUXAdapter:
    """Deterministic non-financial fixture for payment UX/E2E only."""

    def simulate(
        self,
        *,
        fixture_ref: str,
        payment_request_ref: str,
        outcome: FakePaymentOutcome,
    ) -> FakePaymentResult:
        actions = {
            FakePaymentOutcome.PENDING: NextAction(NextActionType.WAIT, "wait:fixture"),
            FakePaymentOutcome.SUCCEEDED: NextAction(NextActionType.NONE, None),
            FakePaymentOutcome.FAILED: NextAction(NextActionType.NONE, None),
            FakePaymentOutcome.EXPIRED: NextAction(NextActionType.NONE, None),
            FakePaymentOutcome.REVERSED: NextAction(NextActionType.NONE, None),
        }
        return FakePaymentResult(
            fixture_ref=fixture_ref,
            payment_request_ref=payment_request_ref,
            outcome=outcome,
            next_action=actions[outcome],
            test_fixture_only=True,
            financial_system_mutation=False,
            xbos_canonical_payment_mutation=False,
        )

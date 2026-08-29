from __future__ import annotations

import unittest
from dataclasses import replace

from xbos_customer_channel.adapters.fake_order_lifecycle import FakeXBOSOrderChangeFulfillmentClient
from xbos_customer_channel.application.order_lifecycle_service import OrderChangeFulfillmentService
from xbos_customer_channel.order_lifecycle import (
    CancellationCase,
    CancellationDisposition,
    ChangeDisposition,
    ExplicitLifecycleConfirmationRequired,
    FulfillmentStage,
    LifecycleAuthorityMismatch,
    LifecycleRequestConflict,
)
from xbos_customer_channel.persistence.session_records import InMemoryCustomerSessionStore
from xbos_customer_channel.session_state import (
    ChannelState,
    CustomerSessionSnapshot,
    UpstreamPaymentCommercialState,
)


class XC7OrderLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryCustomerSessionStore()
        self.fake = FakeXBOSOrderChangeFulfillmentClient()
        self.service = OrderChangeFulfillmentService(lifecycle_port=self.fake, session_store=self.store)

    def seed_session(
        self,
        *,
        session_ref: str = "session:xc7:1",
        order_ref: str = "order:fixture:xc7",
        correlation_ref: str = "correlation:xc7:1",
        state: ChannelState = ChannelState.ORDER_CREATED,
    ) -> None:
        self.store.put(
            CustomerSessionSnapshot(
                session_ref=session_ref,
                conversation_ref="conversation:xc7:1",
                correlation_ref=correlation_ref,
                state=state,
                order_ref=order_ref,
            )
        )

    def register(
        self,
        *,
        order_ref: str = "order:fixture:xc7",
        correlation_ref: str = "correlation:xc7:1",
        payment_state: UpstreamPaymentCommercialState = UpstreamPaymentCommercialState.NOT_REQUESTED,
        fulfillment_stage: FulfillmentStage | None = None,
        merchant_initiated_cancel: bool = False,
    ) -> None:
        self.fake.register_order(
            order_ref=order_ref,
            correlation_ref=correlation_ref,
            payment_state=payment_state,
            fulfillment_stage=fulfillment_stage,
            merchant_initiated_cancel=merchant_initiated_cancel,
        )

    def test_pre_fulfillment_change_is_decided_by_xbos_policy(self) -> None:
        self.seed_session()
        self.register()
        decision = self.service.request_change(
            session_ref="session:xc7:1",
            requested_change_ref="change-intent:quantity:2",
            client_change_ref="client-change:1",
            explicitly_confirmed=True,
        )
        self.assertEqual(decision.disposition, ChangeDisposition.ACCEPTED)
        self.assertEqual(self.fake.canonical_change_effect_count, 1)

    def test_change_after_fulfillment_started_is_rejected_by_xbos_policy(self) -> None:
        self.seed_session()
        self.register(fulfillment_stage=FulfillmentStage.PREPARATION)
        decision = self.service.request_change(
            session_ref="session:xc7:1",
            requested_change_ref="change-intent:quantity:2",
            client_change_ref="client-change:prep",
            explicitly_confirmed=True,
        )
        self.assertEqual(decision.disposition, ChangeDisposition.REJECTED)
        self.assertEqual(self.fake.canonical_change_effect_count, 0)

    def test_change_requires_explicit_confirmation(self) -> None:
        self.seed_session()
        self.register()
        with self.assertRaisesRegex(ExplicitLifecycleConfirmationRequired, "order_change"):
            self.service.request_change(
                session_ref="session:xc7:1",
                requested_change_ref="change-intent:quantity:2",
                client_change_ref="client-change:confirm",
                explicitly_confirmed=False,
            )

    def test_duplicate_change_same_payload_is_one_canonical_effect(self) -> None:
        self.seed_session()
        self.register()
        args = dict(
            session_ref="session:xc7:1",
            requested_change_ref="change-intent:item:remove",
            client_change_ref="client-change:retry",
            explicitly_confirmed=True,
        )
        first = self.service.request_change(**args)
        second = self.service.request_change(**args)
        self.assertEqual(first, second)
        self.assertEqual(self.fake.canonical_change_effect_count, 1)

    def test_altered_change_payload_same_client_ref_conflicts(self) -> None:
        self.seed_session()
        self.register()
        self.service.request_change(
            session_ref="session:xc7:1",
            requested_change_ref="change-intent:item:a",
            client_change_ref="client-change:conflict",
            explicitly_confirmed=True,
        )
        with self.assertRaisesRegex(LifecycleRequestConflict, "change_idempotency_payload_conflict"):
            self.service.request_change(
                session_ref="session:xc7:1",
                requested_change_ref="change-intent:item:b",
                client_change_ref="client-change:conflict",
                explicitly_confirmed=True,
            )

    def test_cancel_before_payment_is_distinct_and_has_no_financial_correction(self) -> None:
        self.seed_session()
        self.register()
        decision = self.service.request_cancellation(
            session_ref="session:xc7:1",
            reason_ref="reason:customer_changed_mind",
            client_cancel_ref="client-cancel:prepay",
            explicitly_confirmed=True,
        )
        self.assertEqual(decision.case, CancellationCase.CANCEL_BEFORE_PAYMENT)
        self.assertEqual(decision.disposition, CancellationDisposition.ACCEPTED)
        self.assertFalse(decision.financial_correction_required)

    def test_cancel_after_payment_is_distinct_and_only_projects_correction_requirement(self) -> None:
        self.seed_session()
        self.register(payment_state=UpstreamPaymentCommercialState.PAID)
        decision = self.service.request_cancellation(
            session_ref="session:xc7:1",
            reason_ref="reason:customer_request",
            client_cancel_ref="client-cancel:paid",
            explicitly_confirmed=True,
        )
        self.assertEqual(decision.case, CancellationCase.CANCEL_AFTER_PAYMENT)
        self.assertEqual(decision.disposition, CancellationDisposition.ACCEPTED)
        self.assertTrue(decision.financial_correction_required)

    def test_cancel_after_preparation_is_distinct_and_xbos_policy_rejects(self) -> None:
        self.seed_session()
        self.register(
            payment_state=UpstreamPaymentCommercialState.PAID,
            fulfillment_stage=FulfillmentStage.PREPARATION,
        )
        decision = self.service.request_cancellation(
            session_ref="session:xc7:1",
            reason_ref="reason:customer_request",
            client_cancel_ref="client-cancel:prep",
            explicitly_confirmed=True,
        )
        self.assertEqual(decision.case, CancellationCase.CANCEL_AFTER_PREPARATION_BEGAN)
        self.assertEqual(decision.disposition, CancellationDisposition.REJECTED)
        self.assertTrue(decision.financial_correction_required)

    def test_merchant_initiated_cancellation_is_authoritative_projection(self) -> None:
        self.seed_session()
        self.register(
            payment_state=UpstreamPaymentCommercialState.PAID,
            merchant_initiated_cancel=True,
        )
        decision = self.service.merchant_cancellation_projection(session_ref="session:xc7:1")
        assert decision is not None
        self.assertEqual(decision.case, CancellationCase.MERCHANT_INITIATED_CANCELLATION)
        self.assertEqual(decision.disposition, CancellationDisposition.ACCEPTED)
        self.assertTrue(decision.financial_correction_required)

    def test_cancellation_requires_explicit_confirmation(self) -> None:
        self.seed_session()
        self.register()
        with self.assertRaisesRegex(ExplicitLifecycleConfirmationRequired, "cancellation"):
            self.service.request_cancellation(
                session_ref="session:xc7:1",
                reason_ref="reason:customer_request",
                client_cancel_ref="client-cancel:confirm",
                explicitly_confirmed=False,
            )

    def test_duplicate_cancel_same_payload_is_one_canonical_cancel_effect(self) -> None:
        self.seed_session()
        self.register()
        args = dict(
            session_ref="session:xc7:1",
            reason_ref="reason:customer_request",
            client_cancel_ref="client-cancel:retry",
            explicitly_confirmed=True,
        )
        first = self.service.request_cancellation(**args)
        second = self.service.request_cancellation(**args)
        self.assertEqual(first, second)
        self.assertEqual(self.fake.canonical_cancel_effect_count, 1)

    def test_altered_cancel_payload_same_client_ref_conflicts(self) -> None:
        self.seed_session()
        self.register()
        self.service.request_cancellation(
            session_ref="session:xc7:1",
            reason_ref="reason:a",
            client_cancel_ref="client-cancel:conflict",
            explicitly_confirmed=True,
        )
        with self.assertRaisesRegex(LifecycleRequestConflict, "cancel_idempotency_payload_conflict"):
            self.service.request_cancellation(
                session_ref="session:xc7:1",
                reason_ref="reason:b",
                client_cancel_ref="client-cancel:conflict",
                explicitly_confirmed=True,
            )

    def test_all_required_fulfillment_stages_project_without_channel_mutation(self) -> None:
        self.seed_session()
        self.register()
        required = (
            FulfillmentStage.KITCHEN_BAR_TICKET,
            FulfillmentStage.PREPARATION,
            FulfillmentStage.READY,
            FulfillmentStage.SERVED,
            FulfillmentStage.PICKED_UP,
            FulfillmentStage.DISPATCHED,
            FulfillmentStage.DELIVERED,
        )
        original = self.store.get("session:xc7:1")
        for stage in required:
            self.fake.set_fulfillment_stage("order:fixture:xc7", stage)
            projection = self.service.fulfillment_projection(session_ref="session:xc7:1")
            assert projection is not None
            self.assertEqual(projection.stage, stage)
        self.assertEqual(self.store.get("session:xc7:1"), original)

    def test_reentry_reconciles_latest_authoritative_lifecycle_not_local_state(self) -> None:
        self.seed_session(state=ChannelState.ORDER_CREATED)
        self.register(
            payment_state=UpstreamPaymentCommercialState.PAID,
            fulfillment_stage=FulfillmentStage.READY,
        )
        projection = self.service.reconcile_reentry(session_ref="session:xc7:1")
        self.assertEqual(projection.payment_state, UpstreamPaymentCommercialState.PAID)
        self.assertEqual(projection.fulfillment_stage, FulfillmentStage.READY)
        self.assertEqual(self.store.get("session:xc7:1").state, ChannelState.ORDER_CREATED)  # local snapshot remains projection only

    def test_correlation_mismatch_fails_closed(self) -> None:
        self.seed_session(correlation_ref="correlation:channel")
        self.register(correlation_ref="correlation:xbos")
        with self.assertRaisesRegex(LifecycleAuthorityMismatch, "canonical_order_correlation_mismatch"):
            self.service.reconcile_reentry(session_ref="session:xc7:1")

    def test_session_without_canonical_order_ref_cannot_request_lifecycle_action(self) -> None:
        self.store.put(
            CustomerSessionSnapshot(
                session_ref="session:no-order",
                conversation_ref="conversation:no-order",
                correlation_ref="correlation:no-order",
                state=ChannelState.REVIEW,
            )
        )
        with self.assertRaisesRegex(LifecycleAuthorityMismatch, "canonical_order_reference_required"):
            self.service.reconcile_reentry(session_ref="session:no-order")

    def test_service_exposes_no_financial_correction_or_payment_creation_operations(self) -> None:
        public = {name.lower() for name in dir(self.service) if not name.startswith("_")}
        forbidden = {"refund", "reverse", "reversal", "financial_correction", "create_payment_request", "pay", "wallet", "gateway"}
        self.assertTrue(public.isdisjoint(forbidden))


if __name__ == "__main__":
    unittest.main()

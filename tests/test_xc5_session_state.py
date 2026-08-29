import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_state_reconciliation import FakeXBOSStateReconciliationClient
from xbos_customer_channel.application.session_service import (
    AuthoritativeEvidenceRequired,
    CustomerSessionService,
    ExplicitConfirmationRequired,
    InvalidChannelTransition,
    SessionReconciliationRequired,
)
from xbos_customer_channel.persistence.session_records import InMemoryCustomerSessionStore
from xbos_customer_channel.session_state import (
    ChannelState,
    MaterialAction,
    MaterialInputCandidate,
    UpstreamFulfillmentState,
    UpstreamOrderState,
    UpstreamPaymentCommercialState,
    UpstreamStateProjection,
)


class XC5SessionStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryCustomerSessionStore()
        self.reconcile = FakeXBOSStateReconciliationClient()
        self.service = CustomerSessionService(store=self.store, reconciliation=self.reconcile)
        self.session = self.service.create_session(
            session_ref="sess_fixture_1",
            conversation_ref="conv_fixture_1",
            correlation_ref="corr_fixture_1",
            entry_token_ref="ent_fixture_1",
        )

    def move_to_review(self) -> None:
        path = (
            ChannelState.MERCHANT_CONTEXT,
            ChannelState.BROWSING,
            ChannelState.CART,
            ChannelState.SERVICE_MODE,
            ChannelState.REVIEW,
        )
        for index, state in enumerate(path):
            self.service.transition(self.session.session_ref, state, idempotency_key=f"path-{index}")

    def order_projection(self, **overrides):
        values = dict(
            evidence_ref="evidence_order_1",
            correlation_ref="corr_fixture_1",
            observed_at_epoch=1000,
            order_ref="order_fixture_1",
            order_state=UpstreamOrderState.CREATED,
            payment_ref=None,
            payment_state=UpstreamPaymentCommercialState.NOT_REQUESTED,
            fulfillment_state=UpstreamFulfillmentState.NOT_STARTED,
        )
        values.update(overrides)
        return UpstreamStateProjection(**values)

    def test_interaction_states_follow_explicit_transition_matrix(self) -> None:
        self.move_to_review()
        current = self.store.get(self.session.session_ref)
        self.assertEqual(current.state, ChannelState.REVIEW)
        self.assertEqual(current.transition_count, 5)

    def test_invalid_transition_fails_safe_without_mutation(self) -> None:
        before = self.store.get(self.session.session_ref)
        with self.assertRaisesRegex(InvalidChannelTransition, "invalid_transition"):
            self.service.transition(self.session.session_ref, ChannelState.PAID, idempotency_key="bad")
        self.assertEqual(self.store.get(self.session.session_ref), before)

    def test_retry_is_idempotent_and_does_not_duplicate_transition_effect(self) -> None:
        first = self.service.transition(self.session.session_ref, ChannelState.MERCHANT_CONTEXT, idempotency_key="same-key")
        second = self.service.transition(self.session.session_ref, ChannelState.MERCHANT_CONTEXT, idempotency_key="same-key")
        self.assertEqual(first, second)
        self.assertEqual(second.transition_count, 1)

    def test_order_created_projection_requires_typed_upstream_evidence(self) -> None:
        self.move_to_review()
        self.service.transition(self.session.session_ref, ChannelState.ORDER_SUBMITTING, idempotency_key="submit")
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "order_created"):
            self.service.transition(self.session.session_ref, ChannelState.ORDER_CREATED, idempotency_key="created")

    def test_order_created_projection_accepts_matching_xbos_evidence_only(self) -> None:
        self.move_to_review()
        self.service.transition(self.session.session_ref, ChannelState.ORDER_SUBMITTING, idempotency_key="submit")
        current = self.service.transition(
            self.session.session_ref,
            ChannelState.ORDER_CREATED,
            idempotency_key="created",
            upstream_evidence=self.order_projection(),
        )
        self.assertEqual(current.state, ChannelState.ORDER_CREATED)
        self.assertEqual(current.order_ref, "order_fixture_1")
        self.assertEqual(current.last_upstream_evidence_ref, "evidence_order_1")

    def test_cross_correlation_upstream_evidence_is_rejected(self) -> None:
        self.move_to_review()
        self.service.transition(self.session.session_ref, ChannelState.ORDER_SUBMITTING, idempotency_key="submit")
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "correlation_mismatch"):
            self.service.transition(
                self.session.session_ref,
                ChannelState.ORDER_CREATED,
                idempotency_key="created",
                upstream_evidence=self.order_projection(correlation_ref="corr_other"),
            )

    def test_paid_label_cannot_be_manufactured_from_pending_evidence(self) -> None:
        evidence = self.order_projection(
            evidence_ref="evidence_pending",
            payment_ref="pay_fixture_1",
            payment_state=UpstreamPaymentCommercialState.PENDING,
        )
        self.move_to_review()
        self.service.transition(self.session.session_ref, ChannelState.ORDER_SUBMITTING, idempotency_key="submit")
        self.service.transition(self.session.session_ref, ChannelState.ORDER_CREATED, idempotency_key="created", upstream_evidence=evidence)
        self.service.transition(self.session.session_ref, ChannelState.PAYMENT_METHOD, idempotency_key="method")
        self.service.transition(self.session.session_ref, ChannelState.PAYMENT_PENDING, idempotency_key="pending", upstream_evidence=evidence)
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "paid_requires"):
            self.service.transition(self.session.session_ref, ChannelState.PAID, idempotency_key="paid", upstream_evidence=evidence)

    def test_paid_label_requires_paid_commercial_projection(self) -> None:
        pending = self.order_projection(
            evidence_ref="evidence_pending",
            payment_ref="pay_fixture_1",
            payment_state=UpstreamPaymentCommercialState.PENDING,
        )
        paid = self.order_projection(
            evidence_ref="evidence_paid",
            payment_ref="pay_fixture_1",
            payment_state=UpstreamPaymentCommercialState.PAID,
        )
        self.move_to_review()
        self.service.transition(self.session.session_ref, ChannelState.ORDER_SUBMITTING, idempotency_key="submit")
        self.service.transition(self.session.session_ref, ChannelState.ORDER_CREATED, idempotency_key="created", upstream_evidence=pending)
        self.service.transition(self.session.session_ref, ChannelState.PAYMENT_METHOD, idempotency_key="method")
        self.service.transition(self.session.session_ref, ChannelState.PAYMENT_PENDING, idempotency_key="pending", upstream_evidence=pending)
        current = self.service.transition(self.session.session_ref, ChannelState.PAID, idempotency_key="paid", upstream_evidence=paid)
        self.assertEqual(current.state, ChannelState.PAID)
        self.assertEqual(current.payment_ref, "pay_fixture_1")

    def test_reentry_does_not_trust_stale_local_paid_projection(self) -> None:
        stale = self.store.replace(
            self.session.session_ref,
            state=ChannelState.PAID,
            order_ref="order_fixture_1",
            payment_ref="pay_fixture_1",
            last_upstream_evidence_ref="old_evidence",
        )
        self.assertEqual(stale.state, ChannelState.PAID)
        self.reconcile.register(
            self.order_projection(
                evidence_ref="fresh_pending",
                payment_ref="pay_fixture_1",
                payment_state=UpstreamPaymentCommercialState.PENDING,
            )
        )
        recovered = self.service.reenter(self.session.session_ref)
        self.assertEqual(recovered.state, ChannelState.PAYMENT_PENDING)
        self.assertEqual(recovered.last_upstream_evidence_ref, "fresh_pending")

    def test_reentry_requires_upstream_reconciliation_when_business_projection_exists(self) -> None:
        self.store.replace(
            self.session.session_ref,
            state=ChannelState.ORDER_CREATED,
            order_ref="order_fixture_1",
        )
        with self.assertRaisesRegex(SessionReconciliationRequired, "authoritative_upstream_state_required"):
            self.service.reenter(self.session.session_ref)

    def test_reentry_preserves_stable_session_conversation_correlation_and_entry_refs(self) -> None:
        self.store.replace(
            self.session.session_ref,
            state=ChannelState.ORDER_CREATED,
            order_ref="order_fixture_1",
        )
        self.reconcile.register(self.order_projection(evidence_ref="fresh_order"))
        recovered = self.service.reenter(self.session.session_ref)
        self.assertEqual(recovered.session_ref, "sess_fixture_1")
        self.assertEqual(recovered.conversation_ref, "conv_fixture_1")
        self.assertEqual(recovered.correlation_ref, "corr_fixture_1")
        self.assertEqual(recovered.entry_token_ref, "ent_fixture_1")

    def test_material_ambiguity_requires_explicit_confirmation_for_all_required_classes(self) -> None:
        candidates = (
            MaterialInputCandidate("choice_a", "Choice A"),
            MaterialInputCandidate("choice_b", "Choice B"),
        )
        for action in MaterialAction:
            with self.subTest(action=action):
                with self.assertRaisesRegex(ExplicitConfirmationRequired, "explicit_confirmation_required"):
                    self.service.resolve_material_input(action=action, candidates=candidates)

    def test_explicit_confirmation_selects_only_a_known_candidate(self) -> None:
        candidates = (
            MaterialInputCandidate("choice_a", "Choice A"),
            MaterialInputCandidate("choice_b", "Choice B"),
        )
        decision = self.service.resolve_material_input(
            action=MaterialAction.PAYMENT,
            candidates=candidates,
            confirmed_value_ref="choice_b",
        )
        self.assertTrue(decision.explicitly_confirmed)
        self.assertEqual(decision.selected.value_ref, "choice_b")
        with self.assertRaisesRegex(ExplicitConfirmationRequired, "confirmed_choice_not_in_candidates"):
            self.service.resolve_material_input(
                action=MaterialAction.CANCELLATION,
                candidates=candidates,
                confirmed_value_ref="choice_unknown",
            )

    def test_unambiguous_material_input_does_not_require_fake_confirmation(self) -> None:
        decision = self.service.resolve_material_input(
            action=MaterialAction.QUANTITY_CHANGE,
            candidates=(MaterialInputCandidate("qty_2", "2"),),
        )
        self.assertFalse(decision.explicitly_confirmed)
        self.assertEqual(decision.selected.value_ref, "qty_2")

    def test_upstream_cancel_reconciles_channel_projection_to_canceled(self) -> None:
        self.store.replace(
            self.session.session_ref,
            state=ChannelState.ORDER_CREATED,
            order_ref="order_fixture_1",
        )
        self.reconcile.register(
            self.order_projection(
                evidence_ref="cancel_evidence",
                order_state=UpstreamOrderState.CANCELED,
            )
        )
        recovered = self.service.reenter(self.session.session_ref)
        self.assertEqual(recovered.state, ChannelState.CANCELED)

    def test_session_service_exposes_no_order_or_payment_creation_operation(self) -> None:
        public = {name for name in dir(CustomerSessionService) if not name.startswith("_")}
        self.assertNotIn("open_order", public)
        self.assertNotIn("confirm_order", public)
        self.assertNotIn("create_payment_request", public)
        self.assertNotIn("fulfill_order", public)


if __name__ == "__main__":
    unittest.main()

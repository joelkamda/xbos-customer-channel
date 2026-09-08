import ast
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_catalog import FakeXBOSCatalogClient
from xbos_customer_channel.adapters.fake_context import FakeXBOSContextClient
from xbos_customer_channel.adapters.fake_order import FakeXBOSOrderClient
from xbos_customer_channel.adapters.fake_state_reconciliation import FakeXBOSStateReconciliationClient
from xbos_customer_channel.application.catalog_service import CatalogQuoteService
from xbos_customer_channel.application.order_service import OrderConfirmationRejected, OrderDraftConfirmationService
from xbos_customer_channel.application.session_service import AuthoritativeEvidenceRequired, CustomerSessionService
from xbos_customer_channel.entry_context import EntryPurpose, ResolvedEntryContext
from xbos_customer_channel.order import OrderSubmissionConflict, OrderSubmitRequest, ServiceMode
from xbos_customer_channel.persistence.provenance_records import InMemoryChannelProvenanceStore
from xbos_customer_channel.persistence.session_records import InMemoryCustomerSessionStore
from xbos_customer_channel.session_state import (
    ChannelState,
    UpstreamFulfillmentState,
    UpstreamOrderState,
    UpstreamPaymentCommercialState,
    UpstreamStateProjection,
)


class CR2AuthoritativeProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = FakeXBOSContextClient()
        self.catalog_fake = FakeXBOSCatalogClient()
        self.catalog = CatalogQuoteService(self.catalog_fake)
        self.orders = FakeXBOSOrderClient()
        self.reconcile = FakeXBOSStateReconciliationClient()
        self.sessions_store = InMemoryCustomerSessionStore()
        self.provenance = InMemoryChannelProvenanceStore()
        self.sessions = CustomerSessionService(
            store=self.sessions_store,
            reconciliation=self.reconcile,
            provenance_store=self.provenance,
            xbos_context=self.context,
        )
        self.order_service = OrderDraftConfirmationService(
            catalog=self.catalog,
            order_port=self.orders,
            sessions=self.sessions,
            session_store=self.sessions_store,
            provenance_store=self.provenance,
        )
        self._counter = 0

    def resolved_entry(
        self,
        *,
        merchant_ref: str = "merchant:fixture:alpha",
        location_ref: str = "location:fixture:one",
        table_ref: str | None = "table:fixture:a1",
        purpose: EntryPurpose = EntryPurpose.DINE_IN,
    ) -> ResolvedEntryContext:
        dining_area_ref = "area:fixture:main" if table_ref in {"table:fixture:a1", "table:fixture:a2"} else None
        att = self.context.attest_context(
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            table_ref=table_ref,
            dining_area_ref=dining_area_ref,
            purpose=purpose,
        )
        self._counter += 1
        return ResolvedEntryContext(
            token_ref=f"entry:cr2:{self._counter}",
            merchant_ref=att.merchant_ref,
            location_ref=att.location_ref,
            table_ref=att.table_ref,
            dining_area_ref=att.dining_area_ref,
            purpose=att.purpose,
            projection=att.projection,
            tenant_ref=att.tenant_ref,
            context_binding_ref=att.context_binding_ref,
        )

    def make_session(
        self,
        resolved: ResolvedEntryContext | None = None,
        *,
        state: ChannelState = ChannelState.REVIEW,
        owner: str = "identity:fixture:cr2",
    ):
        resolved = resolved or self.resolved_entry()
        self._counter += 1
        session = self.sessions.create_session(
            conversation_ref=f"conversation:cr2:{self._counter}",
            correlation_ref=f"correlation:cr2:{self._counter}",
            owner_identity_ref=owner,
            entry_context=resolved,
            now_epoch=900,
            expires_at_epoch=5000,
        )
        self.sessions_store.replace(session.session_ref, state=state)
        current = self.sessions_store.get(session.session_ref)
        assert current is not None
        return current

    def catalog_session(self, resolved: ResolvedEntryContext):
        return self.catalog.add_to_cart(self.catalog.browse(resolved), item_ref="item:fixture:one", quantity=1)

    def prepare_alpha(self):
        resolved = self.resolved_entry()
        session = self.make_session(resolved)
        confirmation = self.order_service.prepare_confirmation(
            session_ref=session.session_ref,
            catalog_session=self.catalog_session(resolved),
            service_mode=ServiceMode.DINE_IN,
            now_epoch=1000,
        )
        return resolved, session, confirmation

    def projection(
        self,
        session,
        *,
        evidence_ref: str = "evidence:cr2",
        order_ref: str | None = None,
        order_state: UpstreamOrderState | None = None,
        payment_ref: str | None = None,
        payment_state: UpstreamPaymentCommercialState | None = None,
        fulfillment_state: UpstreamFulfillmentState | None = None,
    ) -> UpstreamStateProjection:
        return UpstreamStateProjection(
            evidence_ref=evidence_ref,
            correlation_ref=session.correlation_ref,
            observed_at_epoch=1000,
            order_ref=order_ref,
            order_state=order_state,
            payment_ref=payment_ref,
            payment_state=payment_state,
            fulfillment_state=fulfillment_state,
        )

    # CR2_A01-A24
    def test_cr2_a01_caller_constructed_payment_projection_not_authority(self) -> None:
        session = self.make_session(state=ChannelState.PAYMENT_METHOD)
        raw = self.projection(
            session,
            payment_ref="payment:fixture:1",
            payment_state=UpstreamPaymentCommercialState.PENDING,
        )
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "caller_supplied"):
            self.sessions.transition(
                session.session_ref,
                ChannelState.PAYMENT_PENDING,
                idempotency_key="a01",
                upstream_evidence=raw,
            )

    def test_cr2_a02_caller_constructed_order_projection_not_authority(self) -> None:
        session = self.make_session(state=ChannelState.ORDER_SUBMITTING)
        raw = self.projection(session, order_ref="order:1", order_state=UpstreamOrderState.CREATED)
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "caller_supplied"):
            self.sessions.transition(
                session.session_ref,
                ChannelState.ORDER_CREATED,
                idempotency_key="a02",
                upstream_evidence=raw,
            )

    def test_cr2_a03_caller_constructed_fulfillment_projection_not_authority(self) -> None:
        session = self.make_session(state=ChannelState.PAID)
        raw = self.projection(session, order_ref="order:1", fulfillment_state=UpstreamFulfillmentState.ACTIVE)
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "caller_supplied"):
            self.sessions.transition(
                session.session_ref,
                ChannelState.FULFILLMENT,
                idempotency_key="a03",
                upstream_evidence=raw,
            )

    def test_cr2_a04_server_side_payment_evidence_resolution_required(self) -> None:
        session = self.make_session(state=ChannelState.PAYMENT_METHOD)
        projection = self.projection(
            session,
            payment_ref="payment:fixture:1",
            payment_state=UpstreamPaymentCommercialState.PENDING,
        )
        self.reconcile.register(session, projection)
        result = self.sessions.transition(session.session_ref, ChannelState.PAYMENT_PENDING, idempotency_key="a04")
        self.assertEqual(result.payment_ref, "payment:fixture:1")

    def test_cr2_a05_missing_authoritative_evidence_fails_closed(self) -> None:
        session = self.make_session(state=ChannelState.PAYMENT_METHOD)
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "server_side_authoritative_evidence_required"):
            self.sessions.transition(session.session_ref, ChannelState.PAYMENT_PENDING, idempotency_key="a05")

    def test_cr2_a06_wrong_correlation_evidence_denied(self) -> None:
        session = self.make_session(state=ChannelState.PAYMENT_METHOD)
        wrong = replace(self.projection(session, payment_state=UpstreamPaymentCommercialState.PENDING), correlation_ref="wrong")
        with self.assertRaisesRegex(ValueError, "correlation_mismatch"):
            self.reconcile.register(session, wrong)

    def test_cr2_a07_wrong_session_evidence_denied(self) -> None:
        session_a = self.make_session(state=ChannelState.ORDER_SUBMITTING)
        session_b = self.make_session(state=ChannelState.ORDER_SUBMITTING)
        projection = self.projection(session_b, order_ref="order:b", order_state=UpstreamOrderState.CREATED)
        handle = self.provenance.issue_evidence(session=session_b, projection=projection)
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "session_context_mismatch"):
            self.sessions.transition(
                session_a.session_ref,
                ChannelState.ORDER_CREATED,
                idempotency_key="a07",
                evidence_handle_ref=handle,
            )

    def test_cr2_a08_wrong_context_evidence_denied(self) -> None:
        session = self.make_session(state=ChannelState.ORDER_SUBMITTING)
        projection = self.projection(session, order_ref="order:a", order_state=UpstreamOrderState.CREATED)
        handle = self.provenance.issue_evidence(session=session, projection=projection)
        self.sessions_store.replace(session.session_ref, context_binding_ref="tampered-context")
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "session_context_mismatch"):
            self.sessions.transition(
                session.session_ref,
                ChannelState.ORDER_CREATED,
                idempotency_key="a08",
                evidence_handle_ref=handle,
            )

    def test_cr2_a09_never_issued_confirmation_denied_by_fake_xbos(self) -> None:
        request = OrderSubmitRequest(
            client_submit_ref="client:a09",
            correlation_ref="corr:a09",
            confirmation_ref="confirmation:never-issued",
        )
        with self.assertRaisesRegex(OrderSubmissionConflict, "confirmation_not_issued"):
            self.orders.submit_order(request)

    def test_cr2_a10_unknown_channel_confirmation_handle_denied(self) -> None:
        session = self.make_session()
        with self.assertRaisesRegex(OrderConfirmationRejected, "unknown_server_confirmation_handle"):
            self.order_service.submit(
                session_ref=session.session_ref,
                confirmation_handle_ref="confirmation_handle_unknown",
                client_submit_ref="client:a10",
                now_epoch=1001,
            )

    def test_cr2_a11_cross_session_confirmation_denied(self) -> None:
        _, _, confirmation = self.prepare_alpha()
        other = self.make_session()
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_context_mismatch"):
            self.order_service.submit(
                session_ref=other.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:a11",
                now_epoch=1001,
            )

    def test_cr2_a12_cross_merchant_confirmation_denied(self) -> None:
        _, _, confirmation = self.prepare_alpha()
        beta = self.resolved_entry(
            merchant_ref="merchant:fixture:beta",
            location_ref="location:fixture:two",
            table_ref="table:fixture:b1",
        )
        other = self.make_session(beta)
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_context_mismatch"):
            self.order_service.submit(
                session_ref=other.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:a12",
                now_epoch=1001,
            )

    def test_cr2_a13_cross_location_confirmation_denied(self) -> None:
        _, _, confirmation = self.prepare_alpha()
        beta = self.resolved_entry(
            merchant_ref="merchant:fixture:beta",
            location_ref="location:fixture:two",
            table_ref="table:fixture:b1",
        )
        other = self.make_session(beta)
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_context_mismatch"):
            self.order_service.submit(
                session_ref=other.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:a13",
                now_epoch=1001,
            )

    def test_cr2_a14_cross_table_confirmation_denied(self) -> None:
        _, _, confirmation = self.prepare_alpha()
        other_entry = self.resolved_entry(table_ref="table:fixture:a2")
        other = self.make_session(other_entry)
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_context_mismatch"):
            self.order_service.submit(
                session_ref=other.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:a14",
                now_epoch=1001,
            )

    def test_cr2_a15_stale_context_binding_confirmation_denied(self) -> None:
        _, session, confirmation = self.prepare_alpha()
        self.context.reassign_table(
            merchant_ref="merchant:fixture:alpha",
            location_ref="location:fixture:one",
            table_ref="table:fixture:a1",
            dining_area_ref="area:fixture:main",
        )
        with self.assertRaisesRegex(OrderConfirmationRejected, "stale_entry_context_rejected"):
            self.order_service.submit(
                session_ref=session.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:a15",
                now_epoch=1001,
            )

    def test_cr2_a16_changed_quote_confirmation_conflict_or_reconfirm(self) -> None:
        _, session, confirmation = self.prepare_alpha()
        record = self.provenance.resolve_confirmation(confirmation.confirmation_handle_ref)
        assert record is not None
        current = self.orders.resolve_order_confirmation(record.authoritative_xbos_confirmation_ref)
        assert current is not None
        changed = replace(current, total=current.total + Decimal("1"))
        self.orders.replace_issued_confirmation(current.confirmation_ref, changed)
        with self.assertRaisesRegex(OrderConfirmationRejected, "material_changed"):
            self.order_service.submit(
                session_ref=session.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:a16",
                now_epoch=1001,
            )

    def test_cr2_a17_same_confirmation_safe_retry_idempotent(self) -> None:
        _, session, confirmation = self.prepare_alpha()
        kwargs = dict(
            session_ref=session.session_ref,
            confirmation_handle_ref=confirmation.confirmation_handle_ref,
            client_submit_ref="client:a17",
        )
        first = self.order_service.submit(now_epoch=1001, **kwargs)
        second = self.order_service.submit(now_epoch=1002, **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(self.orders.canonical_order_effect_count, 1)

    def test_cr2_a18_conflicting_confirmation_reuse_denied(self) -> None:
        _, session, confirmation = self.prepare_alpha()
        self.order_service.submit(
            session_ref=session.session_ref,
            confirmation_handle_ref=confirmation.confirmation_handle_ref,
            client_submit_ref="client:a18:first",
            now_epoch=1001,
        )
        with self.assertRaisesRegex(OrderSubmissionConflict, "confirmation_handle_client_submit_conflict"):
            self.order_service.submit(
                session_ref=session.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:a18:second",
                now_epoch=1002,
            )

    def test_cr2_a19_no_customer_channel_xbos_order_authority(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel" / "application" / "order_service.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        constructed = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "CanonicalOrderProjection"
        ]
        self.assertEqual(constructed, [])

    def test_cr2_a20_no_customer_channel_payment_authority(self) -> None:
        public = {name for name in dir(self.order_service) if not name.startswith("_")}
        self.assertNotIn("create_payment_request", public)
        self.assertNotIn("mark_paid", public)

    def test_cr2_a21_no_gateway_provider_authority(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel" / "application" / "order_service.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("provider_client", source)
        self.assertNotIn("gateway_client", source)

    def test_cr2_a22_no_core_monetary_authority(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel" / "application" / "order_service.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("core_db", source)
        self.assertNotIn("ledger", source)

    def test_cr2_a23_cr1_security_non_regression(self) -> None:
        resolved = self.resolved_entry()
        session = self.sessions.create_session(
            session_ref="caller-fixed",
            conversation_ref="conversation:a23",
            correlation_ref="correlation:a23",
            owner_identity_ref="identity:a23",
            entry_context=resolved,
            now_epoch=1000,
            expires_at_epoch=2000,
        )
        self.assertNotEqual(session.session_ref, "caller-fixed")
        rotated = self.sessions.resume_session(session_ref=session.session_ref, owner_identity_ref="identity:a23", now_epoch=1001)
        self.assertEqual(rotated.generation, 1)
        with self.assertRaisesRegex(PermissionError, "stale_or_rotated"):
            self.sessions.resume_session(session_ref=session.session_ref, owner_identity_ref="identity:a23", now_epoch=1002)

    def test_cr2_a24_a0_005_remains_open_for_cr4(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel"
        self.assertFalse((root / "production_composition.py").exists())
        self.assertTrue((root / "adapters" / "fake_order.py").exists())

    # CR2_C01-C08
    def test_cr2_c01_session_a_plus_payment_evidence_b_denied(self) -> None:
        a = self.make_session(state=ChannelState.PAYMENT_METHOD)
        b = self.make_session(state=ChannelState.PAYMENT_METHOD)
        projection = self.projection(b, payment_ref="payment:b", payment_state=UpstreamPaymentCommercialState.PENDING)
        handle = self.provenance.issue_evidence(session=b, projection=projection)
        with self.assertRaisesRegex(AuthoritativeEvidenceRequired, "session_context_mismatch"):
            self.sessions.transition(a.session_ref, ChannelState.PAYMENT_PENDING, idempotency_key="c01", evidence_handle_ref=handle)

    def test_cr2_c02_session_a_plus_confirmation_b_denied(self) -> None:
        _, _, confirmation_b = self.prepare_alpha()
        a = self.make_session()
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_context_mismatch"):
            self.order_service.submit(
                session_ref=a.session_ref,
                confirmation_handle_ref=confirmation_b.confirmation_handle_ref,
                client_submit_ref="client:c02",
                now_epoch=1001,
            )

    def test_cr2_c03_merchant_a_plus_confirmation_b_denied(self) -> None:
        _, _, confirmation_a = self.prepare_alpha()
        beta = self.resolved_entry(merchant_ref="merchant:fixture:beta", location_ref="location:fixture:two", table_ref="table:fixture:b1")
        b = self.make_session(beta)
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_context_mismatch"):
            self.order_service.submit(
                session_ref=b.session_ref,
                confirmation_handle_ref=confirmation_a.confirmation_handle_ref,
                client_submit_ref="client:c03",
                now_epoch=1001,
            )

    def test_cr2_c04_location_a_plus_confirmation_b_denied(self) -> None:
        _, _, confirmation_a = self.prepare_alpha()
        beta = self.resolved_entry(merchant_ref="merchant:fixture:beta", location_ref="location:fixture:two", table_ref="table:fixture:b1")
        b = self.make_session(beta)
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_context_mismatch"):
            self.order_service.submit(
                session_ref=b.session_ref,
                confirmation_handle_ref=confirmation_a.confirmation_handle_ref,
                client_submit_ref="client:c04",
                now_epoch=1001,
            )

    def test_cr2_c05_table_a_plus_confirmation_b_denied(self) -> None:
        _, _, confirmation_a = self.prepare_alpha()
        a2 = self.make_session(self.resolved_entry(table_ref="table:fixture:a2"))
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_context_mismatch"):
            self.order_service.submit(
                session_ref=a2.session_ref,
                confirmation_handle_ref=confirmation_a.confirmation_handle_ref,
                client_submit_ref="client:c05",
                now_epoch=1001,
            )

    def test_cr2_c06_valid_confirmation_plus_modified_quote_denied_or_reconfirm(self) -> None:
        _, session, confirmation = self.prepare_alpha()
        record = self.provenance.resolve_confirmation(confirmation.confirmation_handle_ref)
        assert record is not None
        current = self.orders.resolve_order_confirmation(record.authoritative_xbos_confirmation_ref)
        assert current is not None
        changed = replace(current, quote_version=current.quote_version + ":changed")
        self.orders.replace_issued_confirmation(current.confirmation_ref, changed)
        with self.assertRaisesRegex(OrderConfirmationRejected, "material_changed"):
            self.order_service.submit(
                session_ref=session.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:c06",
                now_epoch=1001,
            )

    def test_cr2_c07_two_concurrent_submits_same_confirmation_one_effect(self) -> None:
        _, session, confirmation = self.prepare_alpha()

        def submit_once():
            return self.order_service.submit(
                session_ref=session.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:c07",
                now_epoch=1001,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit_once(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.orders.canonical_order_effect_count, 1)

    def test_cr2_c08_handle_after_session_rotation_denied_and_reprepare_required(self) -> None:
        resolved, session, confirmation = self.prepare_alpha()
        rotated = self.sessions.resume_session(
            session_ref=session.session_ref,
            owner_identity_ref="identity:fixture:cr2",
            now_epoch=1001,
        )
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_context_mismatch"):
            self.order_service.submit(
                session_ref=rotated.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client:c08:stale",
                now_epoch=1002,
            )
        refreshed = self.order_service.prepare_confirmation(
            session_ref=rotated.session_ref,
            catalog_session=self.catalog_session(resolved),
            service_mode=ServiceMode.DINE_IN,
            now_epoch=1002,
        )
        self.assertNotEqual(refreshed.confirmation_handle_ref, confirmation.confirmation_handle_ref)


if __name__ == "__main__":
    unittest.main()

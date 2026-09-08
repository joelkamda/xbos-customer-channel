import ast
import sys
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_catalog import FakeXBOSCatalogClient
from xbos_customer_channel.adapters.fake_context import FakeXBOSContextClient
from xbos_customer_channel.adapters.fake_order import FakeXBOSOrderClient
from xbos_customer_channel.adapters.fake_state_reconciliation import FakeXBOSStateReconciliationClient
from xbos_customer_channel.application.catalog_service import CatalogQuoteService, QuoteUnavailable, ReconfirmationRequired
from xbos_customer_channel.application.order_service import OrderConfirmationRejected, OrderDraftConfirmationService
from xbos_customer_channel.application.session_service import CustomerSessionService
from xbos_customer_channel.entry_context import EntryPurpose, ResolvedEntryContext
from xbos_customer_channel.order import (
    CanonicalOrderState,
    OrderSubmissionConflict,
    OrderSubmissionUnknown,
    ServiceMode,
    ServiceModeUnavailable,
)
from xbos_customer_channel.persistence.provenance_records import InMemoryChannelProvenanceStore
from xbos_customer_channel.persistence.identity_records import InMemoryIdentityBindingStore
from xbos_customer_channel.persistence.session_records import InMemoryCustomerSessionStore
from xbos_customer_channel.session_state import ChannelState


def entry(
    purpose: EntryPurpose,
    *,
    merchant_ref: str = "merchant:fixture:alpha",
    location_ref: str = "location:fixture:one",
    table_ref: str | None = None,
) -> ResolvedEntryContext:
    if purpose is EntryPurpose.DINE_IN and table_ref is None:
        table_ref = "table:fixture:a1"
    xbos = FakeXBOSContextClient()
    dining_area_ref = "area:fixture:main" if table_ref == "table:fixture:a1" else None
    att = xbos.attest_context(
        merchant_ref=merchant_ref,
        location_ref=location_ref,
        table_ref=table_ref,
        dining_area_ref=dining_area_ref,
        purpose=purpose,
    )
    return ResolvedEntryContext(
        token_ref=f"entry:fixture:{purpose.value}:{table_ref or 'none'}",
        merchant_ref=att.merchant_ref,
        location_ref=att.location_ref,
        table_ref=att.table_ref,
        dining_area_ref=att.dining_area_ref,
        purpose=att.purpose,
        projection=att.projection,
        tenant_ref=att.tenant_ref,
        context_binding_ref=att.context_binding_ref,
    )


class XC6OrderFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog_fake = FakeXBOSCatalogClient()
        self.catalog = CatalogQuoteService(self.catalog_fake)
        self.order_fake = FakeXBOSOrderClient()
        self.context_fake = FakeXBOSContextClient()
        self.store = InMemoryCustomerSessionStore()
        self.provenance = InMemoryChannelProvenanceStore()
        self.identity_bindings = InMemoryIdentityBindingStore()
        self.sessions = CustomerSessionService(
            store=self.store,
            reconciliation=FakeXBOSStateReconciliationClient(),
            provenance_store=self.provenance,
            xbos_context=self.context_fake,
            identity_binding_store=self.identity_bindings,
        )
        self.service = OrderDraftConfirmationService(
            catalog=self.catalog,
            order_port=self.order_fake,
            sessions=self.sessions,
            session_store=self.store,
            provenance_store=self.provenance,
        )

    def catalog_session(self, purpose: EntryPurpose, *, table_ref: str | None = None):
        resolved = entry(purpose, table_ref=table_ref)
        session = self.catalog.browse(resolved)
        return self.catalog.add_to_cart(session, item_ref="item:fixture:one", quantity=1), resolved

    def secure_session(self, resolved: ResolvedEntryContext):
        conversation_ref = f"conversation:xc6:{resolved.token_ref}"
        binding = self.identity_bindings.issue_binding(
            identity_ref="identity:fixture:customer",
            canonical_channel_subject_ref="subject:fixture:customer",
            subject_attestation_ref="subject_attestation:fixture:customer",
            conversation_ref=conversation_ref,
            tenant_ref=resolved.tenant_ref,
            merchant_ref=resolved.merchant_ref,
            location_ref=resolved.location_ref,
            table_ref=resolved.table_ref,
            dining_area_ref=resolved.dining_area_ref,
            context_binding_ref=resolved.context_binding_ref,
            issued_at_epoch=900,
            expires_at_epoch=5000,
        )
        session = self.sessions.create_session(
            conversation_ref=conversation_ref,
            correlation_ref=f"correlation:xc6:{resolved.token_ref}",
            identity_binding_ref=binding.identity_binding_ref,
            entry_context=resolved,
            now_epoch=900,
            expires_at_epoch=5000,
        )
        self.store.replace(session.session_ref, state=ChannelState.REVIEW)
        return session

    def prepare_for(self, purpose: EntryPurpose, service_mode: ServiceMode, **kwargs):
        catalog_session, resolved = self.catalog_session(purpose)
        session = self.secure_session(resolved)
        confirmation = self.service.prepare_confirmation(
            session_ref=session.session_ref,
            catalog_session=catalog_session,
            service_mode=service_mode,
            now_epoch=1000,
            **kwargs,
        )
        return confirmation, session

    def prepare_dine_in(self):
        return self.prepare_for(EntryPurpose.DINE_IN, ServiceMode.DINE_IN)

    def test_dine_in_requires_and_preserves_valid_xc3_table_context(self) -> None:
        confirmation, _ = self.prepare_dine_in()
        self.assertEqual(confirmation.service_mode, ServiceMode.DINE_IN)
        self.assertIn("table:fixture:a1", confirmation.service_context_ref)
        self.assertEqual(confirmation.total, Decimal("3000"))
        self.assertTrue(confirmation.confirmation_handle_ref.startswith("confirmation_handle_"))

    def test_dine_in_rejects_cross_location_or_stale_table_context(self) -> None:
        good_catalog, resolved = self.catalog_session(EntryPurpose.DINE_IN)
        session = self.secure_session(resolved)
        bad_entry = replace(resolved, table_ref="table:fixture:b1", dining_area_ref=None, context_binding_ref="ctxbind:other")
        bad_catalog = replace(good_catalog, entry=bad_entry)
        with self.assertRaisesRegex(OrderConfirmationRejected, "session_entry_.*mismatch"):
            self.service.prepare_confirmation(
                session_ref=session.session_ref,
                catalog_session=bad_catalog,
                service_mode=ServiceMode.DINE_IN,
                now_epoch=1000,
            )

    def test_dine_in_cannot_be_invented_from_non_table_entry(self) -> None:
        catalog_session, resolved = self.catalog_session(EntryPurpose.MERCHANT_DISCOVERY)
        session = self.secure_session(resolved)
        with self.assertRaisesRegex(ServiceModeUnavailable, "valid_xc3_dine_in_table_context_required"):
            self.service.prepare_confirmation(
                session_ref=session.session_ref,
                catalog_session=catalog_session,
                service_mode=ServiceMode.DINE_IN,
                now_epoch=1000,
            )

    def test_takeaway_requires_contact_and_uses_xbos_pickup_context(self) -> None:
        catalog_session, resolved = self.catalog_session(EntryPurpose.TAKEAWAY)
        session = self.secure_session(resolved)
        with self.assertRaisesRegex(ServiceModeUnavailable, "takeaway_contact_required"):
            self.service.prepare_confirmation(
                session_ref=session.session_ref,
                catalog_session=catalog_session,
                service_mode=ServiceMode.TAKEAWAY,
                now_epoch=1000,
            )
        confirmation = self.service.prepare_confirmation(
            session_ref=session.session_ref,
            catalog_session=catalog_session,
            service_mode=ServiceMode.TAKEAWAY,
            contact_ref="contact:fixture:customer",
            now_epoch=1000,
        )
        self.assertEqual(confirmation.service_mode, ServiceMode.TAKEAWAY)
        self.assertIsNone(confirmation.delivery_fee)
        self.assertEqual(confirmation.total, Decimal("3000"))

    def test_delivery_requires_contact_and_address(self) -> None:
        catalog_session, resolved = self.catalog_session(EntryPurpose.DELIVERY)
        session = self.secure_session(resolved)
        with self.assertRaisesRegex(ServiceModeUnavailable, "delivery_contact_and_address_required"):
            self.service.prepare_confirmation(
                session_ref=session.session_ref,
                catalog_session=catalog_session,
                service_mode=ServiceMode.DELIVERY,
                now_epoch=1000,
            )

    def test_delivery_fee_and_service_area_are_xbos_supplied(self) -> None:
        confirmation, _ = self.prepare_for(
            EntryPurpose.DELIVERY,
            ServiceMode.DELIVERY,
            contact_ref="contact:fixture:customer",
            delivery_address_ref="address:fixture:inside",
        )
        self.assertEqual(confirmation.delivery_fee, Decimal("750"))
        self.assertEqual(confirmation.total, Decimal("3750"))
        self.assertEqual(confirmation.currency, "XAF")

    def test_delivery_outside_authoritative_service_area_fails(self) -> None:
        catalog_session, resolved = self.catalog_session(EntryPurpose.DELIVERY)
        session = self.secure_session(resolved)
        with self.assertRaisesRegex(ServiceModeUnavailable, "delivery_service_area_unavailable"):
            self.service.prepare_confirmation(
                session_ref=session.session_ref,
                catalog_session=catalog_session,
                service_mode=ServiceMode.DELIVERY,
                contact_ref="contact:fixture:customer",
                delivery_address_ref="address:fixture:outside",
                now_epoch=1000,
            )

    def test_channel_service_contains_no_delivery_fee_or_total_arithmetic(self) -> None:
        path = Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel" / "application" / "order_service.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)) for node in ast.walk(tree)))

    def test_authoritative_confirmation_contains_quote_items_mode_total_currency_and_context(self) -> None:
        confirmation, _ = self.prepare_dine_in()
        self.assertEqual(confirmation.quote_ref, "quote:fixture:v1")
        self.assertEqual(confirmation.lines[0].item_ref, "item:fixture:one")
        self.assertEqual(confirmation.lines[0].quantity, 1)
        self.assertEqual(confirmation.merchant_ref, "merchant:fixture:alpha")
        self.assertEqual(confirmation.location_ref, "location:fixture:one")
        self.assertEqual(confirmation.currency, "XAF")
        self.assertEqual(confirmation.total, Decimal("3000"))

    def test_material_price_change_requires_latest_quote_reconfirmation(self) -> None:
        catalog_session, resolved = self.catalog_session(EntryPurpose.DINE_IN)
        session = self.secure_session(resolved)
        self.catalog_fake.set_price("item:fixture:one", Decimal("3500"))
        with self.assertRaisesRegex(ReconfirmationRequired, "latest_quote"):
            self.service.prepare_confirmation(
                session_ref=session.session_ref,
                catalog_session=catalog_session,
                service_mode=ServiceMode.DINE_IN,
                now_epoch=1000,
            )
        confirmation = self.service.prepare_confirmation(
            session_ref=session.session_ref,
            catalog_session=catalog_session,
            service_mode=ServiceMode.DINE_IN,
            acknowledged_quote_ref="quote:fixture:v2",
            now_epoch=1000,
        )
        self.assertEqual(confirmation.total, Decimal("3500"))

    def test_authoritative_unavailability_still_blocks_order_confirmation(self) -> None:
        from xbos_customer_channel.catalog import AvailabilityState

        catalog_session, resolved = self.catalog_session(EntryPurpose.DINE_IN)
        session = self.secure_session(resolved)
        self.catalog_fake.set_availability("item:fixture:one", AvailabilityState.UNAVAILABLE)
        with self.assertRaisesRegex(QuoteUnavailable, "not_available"):
            self.service.prepare_confirmation(
                session_ref=session.session_ref,
                catalog_session=catalog_session,
                service_mode=ServiceMode.DINE_IN,
                acknowledged_quote_ref="quote:fixture:v2",
                now_epoch=1000,
            )

    def test_submit_creates_one_canonical_xbos_order_and_projects_order_created(self) -> None:
        confirmation, session = self.prepare_dine_in()
        order = self.service.submit(
            session_ref=session.session_ref,
            confirmation_handle_ref=confirmation.confirmation_handle_ref,
            client_submit_ref="client-submit:fixture:1",
            now_epoch=1001,
        )
        self.assertEqual(order.state, CanonicalOrderState.CONFIRMED)
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)
        channel = self.store.get(session.session_ref)
        assert channel is not None
        self.assertEqual(channel.state, ChannelState.ORDER_CREATED)
        self.assertEqual(channel.order_ref, order.order_ref)
        self.assertEqual(channel.last_upstream_evidence_ref, order.evidence_ref)

    def test_retry_same_submit_same_payload_has_one_canonical_order_effect(self) -> None:
        confirmation, session = self.prepare_dine_in()
        first = self.service.submit(
            session_ref=session.session_ref,
            confirmation_handle_ref=confirmation.confirmation_handle_ref,
            client_submit_ref="client-submit:fixture:retry",
            now_epoch=1001,
        )
        second = self.service.submit(
            session_ref=session.session_ref,
            confirmation_handle_ref=confirmation.confirmation_handle_ref,
            client_submit_ref="client-submit:fixture:retry",
            now_epoch=1002,
        )
        self.assertEqual(first, second)
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)

    def test_altered_confirmation_same_idempotency_reference_conflicts(self) -> None:
        first_confirmation, session = self.prepare_dine_in()
        self.service.submit(
            session_ref=session.session_ref,
            confirmation_handle_ref=first_confirmation.confirmation_handle_ref,
            client_submit_ref="client-submit:fixture:conflict",
            now_epoch=1001,
        )
        changed_catalog, _ = self.catalog_session(EntryPurpose.DINE_IN)
        self.catalog_fake.set_price("item:fixture:one", Decimal("3500"))
        second_confirmation = self.service.prepare_confirmation(
            session_ref=session.session_ref,
            catalog_session=changed_catalog,
            service_mode=ServiceMode.DINE_IN,
            acknowledged_quote_ref="quote:fixture:v2",
            now_epoch=1002,
        )
        with self.assertRaisesRegex(OrderSubmissionConflict, "idempotency_payload_conflict"):
            self.service.submit(
                session_ref=session.session_ref,
                confirmation_handle_ref=second_confirmation.confirmation_handle_ref,
                client_submit_ref="client-submit:fixture:conflict",
                now_epoch=1003,
            )
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)

    def test_lost_response_after_authoritative_effect_recovers_by_stable_reference(self) -> None:
        confirmation, session = self.prepare_dine_in()
        self.order_fake.fail_next_after_effect = True
        order = self.service.submit(
            session_ref=session.session_ref,
            confirmation_handle_ref=confirmation.confirmation_handle_ref,
            client_submit_ref="client-submit:fixture:lost-after",
            now_epoch=1001,
        )
        self.assertEqual(order.client_submit_ref, "client-submit:fixture:lost-after")
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)
        self.assertEqual(self.store.get(session.session_ref).state, ChannelState.ORDER_CREATED)  # type: ignore[union-attr]

    def test_unknown_before_effect_does_not_infer_success_and_retry_is_safe(self) -> None:
        confirmation, session = self.prepare_dine_in()
        self.order_fake.fail_next_before_effect = True
        with self.assertRaisesRegex(OrderSubmissionUnknown, "outcome_unknown"):
            self.service.submit(
                session_ref=session.session_ref,
                confirmation_handle_ref=confirmation.confirmation_handle_ref,
                client_submit_ref="client-submit:fixture:lost-before",
                now_epoch=1001,
            )
        self.assertEqual(self.order_fake.canonical_order_effect_count, 0)
        self.assertEqual(self.store.get(session.session_ref).state, ChannelState.ORDER_SUBMITTING)  # type: ignore[union-attr]
        order = self.service.submit(
            session_ref=session.session_ref,
            confirmation_handle_ref=confirmation.confirmation_handle_ref,
            client_submit_ref="client-submit:fixture:lost-before",
            now_epoch=1002,
        )
        self.assertEqual(order.state, CanonicalOrderState.CONFIRMED)
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)
        self.assertEqual(self.store.get(session.session_ref).state, ChannelState.ORDER_CREATED)  # type: ignore[union-attr]

    def test_delivery_not_offered_by_xbos_context_cannot_be_invented(self) -> None:
        resolved = entry(EntryPurpose.MERCHANT_DISCOVERY)
        no_delivery = replace(
            resolved,
            projection=replace(resolved.projection, allowed_fulfillment_modes=("dine_in", "takeaway")),
        )
        catalog_session = self.catalog.add_to_cart(self.catalog.browse(no_delivery), item_ref="item:fixture:one")
        session = self.secure_session(resolved)
        with self.assertRaisesRegex(ServiceModeUnavailable, "not_allowed_by_xbos_context"):
            self.service.prepare_confirmation(
                session_ref=session.session_ref,
                catalog_session=catalog_session,
                service_mode=ServiceMode.DELIVERY,
                contact_ref="contact:fixture:customer",
                delivery_address_ref="address:fixture:inside",
                now_epoch=1000,
            )

    def test_xc6_service_exposes_no_payment_creation_method(self) -> None:
        public = {name for name in dir(self.service) if not name.startswith("_")}
        self.assertNotIn("create_payment_request", public)
        self.assertNotIn("pay", public)
        self.assertNotIn("wallet", public)
        self.assertNotIn("gateway", public)


if __name__ == "__main__":
    unittest.main()

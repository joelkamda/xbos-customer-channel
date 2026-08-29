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
from xbos_customer_channel.application.order_service import OrderDraftConfirmationService
from xbos_customer_channel.application.session_service import CustomerSessionService
from xbos_customer_channel.entry_context import EntryPurpose, ResolvedEntryContext
from xbos_customer_channel.order import (
    CanonicalOrderState,
    OrderSubmissionConflict,
    OrderSubmissionUnknown,
    ServiceMode,
    ServiceModeUnavailable,
)
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
    projection = FakeXBOSContextClient().resolve_context(
        merchant_ref=merchant_ref,
        location_ref=location_ref,
        table_ref=table_ref,
        purpose=purpose,
    )
    return ResolvedEntryContext(
        token_ref=f"entry:fixture:{purpose.value}",
        merchant_ref=merchant_ref,
        location_ref=location_ref,
        table_ref=table_ref,
        dining_area_ref="area:fixture:main" if table_ref else None,
        purpose=purpose,
        projection=projection,
    )


class XC6OrderFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog_fake = FakeXBOSCatalogClient()
        self.catalog = CatalogQuoteService(self.catalog_fake)
        self.order_fake = FakeXBOSOrderClient()
        self.store = InMemoryCustomerSessionStore()
        self.sessions = CustomerSessionService(
            store=self.store,
            reconciliation=FakeXBOSStateReconciliationClient(),
        )
        self.service = OrderDraftConfirmationService(
            catalog=self.catalog,
            order_port=self.order_fake,
            sessions=self.sessions,
            session_store=self.store,
        )
        self.sessions.create_session(
            session_ref="session:xc6:1",
            conversation_ref="conversation:xc6:1",
            correlation_ref="correlation:xc6:1",
            entry_token_ref="entry:fixture:dine_in",
        )
        self.store.replace("session:xc6:1", state=ChannelState.REVIEW)

    def catalog_session(self, purpose: EntryPurpose, *, table_ref: str | None = None):
        session = self.catalog.browse(entry(purpose, table_ref=table_ref))
        return self.catalog.add_to_cart(session, item_ref="item:fixture:one", quantity=1)

    def prepare_dine_in(self):
        return self.service.prepare_confirmation(
            catalog_session=self.catalog_session(EntryPurpose.DINE_IN),
            service_mode=ServiceMode.DINE_IN,
            now_epoch=1000,
        )

    def test_dine_in_requires_and_preserves_valid_xc3_table_context(self) -> None:
        confirmation = self.prepare_dine_in()
        self.assertEqual(confirmation.service_mode, ServiceMode.DINE_IN)
        self.assertIn("table:fixture:a1", confirmation.service_context_ref)
        self.assertEqual(confirmation.total, Decimal("3000"))

    def test_dine_in_rejects_cross_location_or_stale_table_context(self) -> None:
        bad = self.catalog_session(EntryPurpose.DINE_IN, table_ref="table:fixture:b1")
        with self.assertRaisesRegex(ServiceModeUnavailable, "invalid_or_stale_table_context"):
            self.service.prepare_confirmation(
                catalog_session=bad,
                service_mode=ServiceMode.DINE_IN,
                now_epoch=1000,
            )

    def test_dine_in_cannot_be_invented_from_non_table_entry(self) -> None:
        discovery = self.catalog_session(EntryPurpose.MERCHANT_DISCOVERY)
        with self.assertRaisesRegex(ServiceModeUnavailable, "valid_xc3_dine_in_table_context_required"):
            self.service.prepare_confirmation(
                catalog_session=discovery,
                service_mode=ServiceMode.DINE_IN,
                now_epoch=1000,
            )

    def test_takeaway_requires_contact_and_uses_xbos_pickup_context(self) -> None:
        session = self.catalog_session(EntryPurpose.TAKEAWAY)
        with self.assertRaisesRegex(ServiceModeUnavailable, "takeaway_contact_required"):
            self.service.prepare_confirmation(
                catalog_session=session,
                service_mode=ServiceMode.TAKEAWAY,
                now_epoch=1000,
            )
        confirmation = self.service.prepare_confirmation(
            catalog_session=session,
            service_mode=ServiceMode.TAKEAWAY,
            contact_ref="contact:fixture:customer",
            now_epoch=1000,
        )
        self.assertEqual(confirmation.service_mode, ServiceMode.TAKEAWAY)
        self.assertIsNone(confirmation.delivery_fee)
        self.assertEqual(confirmation.total, Decimal("3000"))

    def test_delivery_requires_contact_and_address(self) -> None:
        session = self.catalog_session(EntryPurpose.DELIVERY)
        with self.assertRaisesRegex(ServiceModeUnavailable, "delivery_contact_and_address_required"):
            self.service.prepare_confirmation(
                catalog_session=session,
                service_mode=ServiceMode.DELIVERY,
                now_epoch=1000,
            )

    def test_delivery_fee_and_service_area_are_xbos_supplied(self) -> None:
        confirmation = self.service.prepare_confirmation(
            catalog_session=self.catalog_session(EntryPurpose.DELIVERY),
            service_mode=ServiceMode.DELIVERY,
            contact_ref="contact:fixture:customer",
            delivery_address_ref="address:fixture:inside",
            now_epoch=1000,
        )
        self.assertEqual(confirmation.delivery_fee, Decimal("750"))
        self.assertEqual(confirmation.total, Decimal("3750"))
        self.assertEqual(confirmation.currency, "XAF")

    def test_delivery_outside_authoritative_service_area_fails(self) -> None:
        with self.assertRaisesRegex(ServiceModeUnavailable, "delivery_service_area_unavailable"):
            self.service.prepare_confirmation(
                catalog_session=self.catalog_session(EntryPurpose.DELIVERY),
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
        confirmation = self.prepare_dine_in()
        self.assertEqual(confirmation.quote_ref, "quote:fixture:v1")
        self.assertEqual(confirmation.lines[0].item_ref, "item:fixture:one")
        self.assertEqual(confirmation.lines[0].quantity, 1)
        self.assertEqual(confirmation.merchant_ref, "merchant:fixture:alpha")
        self.assertEqual(confirmation.location_ref, "location:fixture:one")
        self.assertEqual(confirmation.currency, "XAF")
        self.assertEqual(confirmation.total, Decimal("3000"))

    def test_material_price_change_requires_latest_quote_reconfirmation(self) -> None:
        session = self.catalog_session(EntryPurpose.DINE_IN)
        self.catalog_fake.set_price("item:fixture:one", Decimal("3500"))
        with self.assertRaisesRegex(ReconfirmationRequired, "latest_quote"):
            self.service.prepare_confirmation(
                catalog_session=session,
                service_mode=ServiceMode.DINE_IN,
                now_epoch=1000,
            )
        confirmation = self.service.prepare_confirmation(
            catalog_session=session,
            service_mode=ServiceMode.DINE_IN,
            acknowledged_quote_ref="quote:fixture:v2",
            now_epoch=1000,
        )
        self.assertEqual(confirmation.total, Decimal("3500"))

    def test_authoritative_unavailability_still_blocks_order_confirmation(self) -> None:
        from xbos_customer_channel.catalog import AvailabilityState

        session = self.catalog_session(EntryPurpose.DINE_IN)
        self.catalog_fake.set_availability("item:fixture:one", AvailabilityState.UNAVAILABLE)
        with self.assertRaisesRegex(QuoteUnavailable, "not_available"):
            self.service.prepare_confirmation(
                catalog_session=session,
                service_mode=ServiceMode.DINE_IN,
                acknowledged_quote_ref="quote:fixture:v2",
                now_epoch=1000,
            )

    def test_submit_creates_one_canonical_xbos_order_and_projects_order_created(self) -> None:
        confirmation = self.prepare_dine_in()
        order = self.service.submit(
            session_ref="session:xc6:1",
            confirmation=confirmation,
            client_submit_ref="client-submit:fixture:1",
        )
        self.assertEqual(order.state, CanonicalOrderState.CONFIRMED)
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)
        channel = self.store.get("session:xc6:1")
        assert channel is not None
        self.assertEqual(channel.state, ChannelState.ORDER_CREATED)
        self.assertEqual(channel.order_ref, order.order_ref)
        self.assertEqual(channel.last_upstream_evidence_ref, order.evidence_ref)

    def test_retry_same_submit_same_payload_has_one_canonical_order_effect(self) -> None:
        confirmation = self.prepare_dine_in()
        first = self.service.submit(
            session_ref="session:xc6:1",
            confirmation=confirmation,
            client_submit_ref="client-submit:fixture:retry",
        )
        second = self.service.submit(
            session_ref="session:xc6:1",
            confirmation=confirmation,
            client_submit_ref="client-submit:fixture:retry",
        )
        self.assertEqual(first, second)
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)

    def test_altered_confirmation_same_idempotency_reference_conflicts(self) -> None:
        first_confirmation = self.prepare_dine_in()
        self.service.submit(
            session_ref="session:xc6:1",
            confirmation=first_confirmation,
            client_submit_ref="client-submit:fixture:conflict",
        )
        altered = replace(first_confirmation, confirmation_ref=first_confirmation.confirmation_ref + ":altered")
        with self.assertRaisesRegex(OrderSubmissionConflict, "idempotency_payload_conflict"):
            self.service.submit(
                session_ref="session:xc6:1",
                confirmation=altered,
                client_submit_ref="client-submit:fixture:conflict",
            )
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)

    def test_lost_response_after_authoritative_effect_recovers_by_stable_reference(self) -> None:
        confirmation = self.prepare_dine_in()
        self.order_fake.fail_next_after_effect = True
        order = self.service.submit(
            session_ref="session:xc6:1",
            confirmation=confirmation,
            client_submit_ref="client-submit:fixture:lost-after",
        )
        self.assertEqual(order.client_submit_ref, "client-submit:fixture:lost-after")
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)
        self.assertEqual(self.store.get("session:xc6:1").state, ChannelState.ORDER_CREATED)  # type: ignore[union-attr]

    def test_unknown_before_effect_does_not_infer_success_and_retry_is_safe(self) -> None:
        confirmation = self.prepare_dine_in()
        self.order_fake.fail_next_before_effect = True
        with self.assertRaisesRegex(OrderSubmissionUnknown, "outcome_unknown"):
            self.service.submit(
                session_ref="session:xc6:1",
                confirmation=confirmation,
                client_submit_ref="client-submit:fixture:lost-before",
            )
        self.assertEqual(self.order_fake.canonical_order_effect_count, 0)
        self.assertEqual(self.store.get("session:xc6:1").state, ChannelState.ORDER_SUBMITTING)  # type: ignore[union-attr]
        order = self.service.submit(
            session_ref="session:xc6:1",
            confirmation=confirmation,
            client_submit_ref="client-submit:fixture:lost-before",
        )
        self.assertEqual(order.state, CanonicalOrderState.CONFIRMED)
        self.assertEqual(self.order_fake.canonical_order_effect_count, 1)
        self.assertEqual(self.store.get("session:xc6:1").state, ChannelState.ORDER_CREATED)  # type: ignore[union-attr]

    def test_delivery_not_offered_by_xbos_context_cannot_be_invented(self) -> None:
        base = entry(EntryPurpose.MERCHANT_DISCOVERY)
        no_delivery = replace(
            base,
            projection=replace(base.projection, allowed_fulfillment_modes=("dine_in", "takeaway")),
        )
        catalog_session = self.catalog.add_to_cart(self.catalog.browse(no_delivery), item_ref="item:fixture:one")
        with self.assertRaisesRegex(ServiceModeUnavailable, "not_allowed_by_xbos_context"):
            self.service.prepare_confirmation(
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

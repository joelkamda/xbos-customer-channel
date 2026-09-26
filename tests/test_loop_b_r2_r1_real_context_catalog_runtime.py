from __future__ import annotations

import inspect
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.adapters.real_xbos_catalog import (
    PrivateXBOSCatalogBindingResolver,
    PrivateXBOSMenuReadClient,
    RealXBOSCatalogAdapter,
    RealXBOSCatalogUnavailable,
)
from xbos_customer_channel.adapters.real_xbos_context import (
    RealXBOSContextAdapter,
    RealXBOSContextUnavailable,
)
from xbos_customer_channel.application.w1_composition import (
    compose_real_xbos_boundaries,
)
from xbos_customer_channel.entry_context import EntryPurpose
from xbos_customer_channel.persistence.postgres.w1_runtime_state_port import (
    PostgresW1RuntimeStatePort,
)
from xbos_customer_channel.runtime.config import RuntimeConfig


NOW = datetime(2026, 9, 26, 12, 45, tzinfo=timezone.utc)
CATALOG_ID = UUID("00000000-0000-0000-0000-000000000101")
SECTION_ID = UUID("00000000-0000-0000-0000-000000000201")
ENTRY_ID = UUID("00000000-0000-0000-0000-000000000301")
TARGET_ID = UUID("00000000-0000-0000-0000-000000000401")
PRICE_ID = UUID("00000000-0000-0000-0000-000000000501")


class StubPrivateXBOS:
    def __init__(self) -> None:
        self.events = []
        self.binding_payload = {
            "binding_ref": "bind-wnd-logpom-v1",
            "binding_version": 7,
            "tenant_id": 2,
            "catalog_public_id": str(CATALOG_ID),
            "price_code": "retail",
            "currency": "XAF",
            "scope_type": "location",
            "scope_id": 9,
        }

    def attest_context(self, **kwargs):
        self.events.append(("context", kwargs))
        return {
            "tenant_ref": "2",
            "merchant_ref": kwargs["merchant_ref"],
            "location_ref": kwargs["location_ref"],
            "table_ref": kwargs["table_ref"],
            "dining_area_ref": kwargs["dining_area_ref"],
            "purpose": kwargs["purpose"],
            "context_binding_ref": kwargs["context_binding_ref"],
            "projection": {
                "merchant_ref": kwargs["merchant_ref"],
                "location_ref": kwargs["location_ref"],
                "service_available": True,
                "display_name": "WND Logpom",
                "terminology": [["table", "Table"], ["order", "Order"]],
                "currency": "XAF",
                "allowed_fulfillment_modes": [
                    "dine_in",
                    "takeaway",
                    "delivery",
                ],
            },
        }

    def resolve_catalog_binding(self, **kwargs):
        self.events.append(("binding", kwargs))
        return dict(self.binding_payload)

    def menu(self, request, **kwargs):
        self.events.append(("menu", request, kwargs))
        return {
            "tenant_id": 2,
            "catalog_reference": {
                "public_id": str(CATALOG_ID),
                "code": "main_menu",
                "name": "Main Menu",
            },
            "effective_at": request.effective_at.isoformat(),
            "pricing_context": {
                "price_code": "retail",
                "currency": "XAF",
                "scope_type": "location",
                "scope_id": 9,
            },
            "sections": [
                {
                    "section_public_id": str(SECTION_ID),
                    "section_code": "mains",
                    "display_name": "Mains",
                    "sort_order": 1,
                    "entries": [
                        {
                            "catalog_entry_public_id": str(ENTRY_ID),
                            "target_type": "atomic_unit",
                            "target_public_id": str(TARGET_ID),
                            "target_presentation": {
                                "public_id": str(TARGET_ID),
                                "name": "Ndole",
                                "active": True,
                                "metadata": {
                                    "description": "Canonical menu item",
                                    "media_refs": [],
                                },
                            },
                            "resolved_price": {
                                "public_id": str(PRICE_ID),
                                "target_public_id": str(TARGET_ID),
                                "amount": "4500",
                                "currency": "XAF",
                                "price_code": "retail",
                            },
                            "modifier_configuration": [],
                            "sort_order": 1,
                        }
                    ],
                }
            ],
            "creates_financial_truth": False,
        }


class LoopBR2R1RealContextCatalogRuntimeTests(unittest.TestCase):
    def test_real_context_requires_existing_context_binding_ref(self):
        adapter = RealXBOSContextAdapter(StubPrivateXBOS())
        with self.assertRaisesRegex(
            RealXBOSContextUnavailable,
            "existing_context_binding_ref_required",
        ):
            adapter.attest_context(
                merchant_ref="merchant-wnd",
                location_ref="location-logpom",
                table_ref=None,
                purpose=EntryPurpose.TAKEAWAY,
            )

    def test_real_context_propagates_binding_correlation_and_effective_at(self):
        private = StubPrivateXBOS()
        adapter = RealXBOSContextAdapter(private)
        attestation = adapter.attest_bound_context(
            context_binding_ref="ctx-wnd-logpom",
            merchant_ref="merchant-wnd",
            location_ref="location-logpom",
            table_ref=None,
            purpose=EntryPurpose.TAKEAWAY,
            dining_area_ref=None,
            effective_at=NOW,
            correlation_ref="corr-1",
        )
        self.assertEqual(attestation.context_binding_ref, "ctx-wnd-logpom")
        self.assertEqual(attestation.projection.currency, "XAF")
        event = private.events[0]
        self.assertEqual(event[0], "context")
        self.assertEqual(event[1]["context_binding_ref"], "ctx-wnd-logpom")
        self.assertEqual(event[1]["effective_at"], NOW)
        self.assertEqual(event[1]["correlation_ref"], "corr-1")

    def test_catalog_binding_precedes_menu_and_preserves_canonical_truth(self):
        private = StubPrivateXBOS()
        adapter = RealXBOSCatalogAdapter(
            client=PrivateXBOSMenuReadClient(private),
            binding_resolver=PrivateXBOSCatalogBindingResolver(private),
            effective_at_factory=lambda: NOW,
        )
        projection = adapter.get_catalog_bound(
            merchant_ref="merchant-wnd",
            location_ref="location-logpom",
            effective_at=NOW,
            correlation_ref="corr-2",
        )
        self.assertEqual([event[0] for event in private.events], ["binding", "menu"])
        binding_event = private.events[0][1]
        menu_event = private.events[1]
        self.assertEqual(binding_event["effective_at"], NOW)
        self.assertEqual(menu_event[1].effective_at, NOW)
        self.assertEqual(menu_event[2]["binding_ref"], "bind-wnd-logpom-v1")
        self.assertEqual(menu_event[2]["binding_version"], 7)
        item = projection.items[0]
        self.assertEqual(item.item_ref, str(TARGET_ID))
        self.assertEqual(item.display_price, Decimal("4500"))
        self.assertEqual(item.currency, "XAF")
        self.assertEqual(projection.catalog_ref, str(CATALOG_ID))

    def test_binding_malformed_fails_before_menu(self):
        private = StubPrivateXBOS()
        private.binding_payload.pop("binding_version")
        adapter = RealXBOSCatalogAdapter(
            client=PrivateXBOSMenuReadClient(private),
            binding_resolver=PrivateXBOSCatalogBindingResolver(private),
            effective_at_factory=lambda: NOW,
        )
        with self.assertRaisesRegex(
            RealXBOSCatalogUnavailable,
            "binding_malformed",
        ):
            adapter.get_catalog_bound(
                merchant_ref="merchant-wnd",
                location_ref="location-logpom",
                effective_at=NOW,
                correlation_ref="corr-3",
            )
        self.assertEqual([event[0] for event in private.events], ["binding"])

    def test_real_boundary_composition_requires_injected_token_but_does_not_call_network(self):
        env = {
            "META_WHATSAPP_GRAPH_API_VERSION": "v99.0",
            "META_WHATSAPP_PHONE_NUMBER_ID": "phone-id",
            "META_WHATSAPP_APP_SECRET": "synthetic-meta-secret",
            "META_WHATSAPP_VERIFY_TOKEN": "synthetic-verify",
            "META_WHATSAPP_ACCESS_TOKEN": "synthetic-access",
            "XAFPAY_CUSTOMER_CHANNEL_XBOS_PRIVATE_BASE_URL": "http://private-xbos.example:10000",
            "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_ADAPTER": "real",
            "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_READ_TOKEN": "synthetic-read-token",
        }
        config = RuntimeConfig.from_environment(env)
        self.assertNotIn("synthetic-read-token", repr(config))
        context, catalog = compose_real_xbos_boundaries(
            config,
            sleeper=lambda _: None,
            effective_at_factory=lambda: NOW,
        )
        self.assertIsInstance(context, RealXBOSContextAdapter)
        self.assertIsInstance(catalog, RealXBOSCatalogAdapter)

    def test_runtime_source_reuses_one_now_for_bound_context_and_catalog(self):
        source = inspect.getsource(PostgresW1RuntimeStatePort.load)
        self.assertIn("effective_at=now", source)
        self.assertGreaterEqual(source.count("effective_at=now"), 2)
        self.assertIn("get_catalog_bound", source)
        reattest = inspect.getsource(PostgresW1RuntimeStatePort._reattest_context)
        self.assertIn("context_binding_ref=session.context_binding_ref", reattest)
        self.assertIn("correlation_ref=session.correlation_ref", reattest)

    def test_new_real_transport_source_contains_no_order_or_payment_effect(self):
        from xbos_customer_channel.adapters import xbos_private_http

        source = inspect.getsource(xbos_private_http.PrivateXBOSHTTPClient)
        self.assertNotIn("create_order", source)
        self.assertNotIn("create_payment", source)
        self.assertNotIn("inventory", source)


if __name__ == "__main__":
    unittest.main()

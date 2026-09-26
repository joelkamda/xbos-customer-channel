from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.adapters.fake_catalog import FakeXBOSCatalogClient
from xbos_customer_channel.adapters.real_xbos_catalog import (
    XBOS_CATALOG_CONTRACT_STATE,
    XBOS_CATALOG_READ_OPERATION,
    XBOS_CATALOG_READ_PERMISSION,
    RealXBOSCatalogAdapter,
    RealXBOSCatalogUnavailable,
    RealXBOSQuoteUnavailable,
    XBOSMenuReadBinding,
    XBOSMenuReadClientError,
)
from xbos_customer_channel.application.w1_composition import (
    XBOSW1ContractUnavailable,
    select_xbos_catalog_adapter,
)
from xbos_customer_channel.catalog import AvailabilityState
from xbos_customer_channel.runtime.config import RuntimeConfig


NOW = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
CATALOG_ID = UUID("00000000-0000-0000-0000-000000000101")
SECTION_A = UUID("00000000-0000-0000-0000-000000000201")
SECTION_B = UUID("00000000-0000-0000-0000-000000000202")
ENTRY_A = UUID("00000000-0000-0000-0000-000000000301")
ENTRY_B = UUID("00000000-0000-0000-0000-000000000302")
TARGET_A = UUID("00000000-0000-0000-0000-000000000401")
TARGET_B = UUID("00000000-0000-0000-0000-000000000402")
PRICE_A = UUID("00000000-0000-0000-0000-000000000501")
PRICE_B = UUID("00000000-0000-0000-0000-000000000502")
GROUP_A = UUID("00000000-0000-0000-0000-000000000601")


@dataclass(frozen=True)
class BindingResolver:
    binding: XBOSMenuReadBinding | None

    def resolve(self, *, merchant_ref: str, location_ref: str):
        del merchant_ref, location_ref
        return self.binding


class RecordingMenuClient:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = []
        self.error = None

    def menu(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def binding() -> XBOSMenuReadBinding:
    return XBOSMenuReadBinding(
        merchant_ref="merchant:real:one",
        location_ref="location:real:one",
        tenant_id=2,
        catalog_public_id=CATALOG_ID,
        price_code="retail",
        currency="XAF",
        scope_type="tenant",
        scope_id=None,
    )


def price(
    *,
    public_id=PRICE_A,
    target_public_id=TARGET_A,
    amount=Decimal("4500"),
    currency="XAF",
    price_code="retail",
):
    return SimpleNamespace(
        public_id=public_id,
        target_public_id=target_public_id,
        amount=amount,
        currency=currency,
        price_code=price_code,
    )


def target(*, public_id=TARGET_A, name="Item One", active=True):
    return SimpleNamespace(
        public_id=public_id,
        name=name,
        active=active,
        metadata={
            "description": f"{name} description",
            "media_refs": (f"media:{public_id}",),
        },
    )


def entry(
    *,
    catalog_entry_public_id=ENTRY_A,
    target_public_id=TARGET_A,
    target_type="atomic_unit",
    target_presentation=None,
    resolved_price=None,
    groups=(),
    sort_order=1,
):
    return SimpleNamespace(
        catalog_entry_public_id=catalog_entry_public_id,
        target_type=SimpleNamespace(value=target_type),
        target_public_id=target_public_id,
        target_presentation=target_presentation or target(
            public_id=target_public_id
        ),
        resolved_price=resolved_price
        or price(target_public_id=target_public_id),
        modifier_configuration=tuple(
            SimpleNamespace(group_public_id=group) for group in groups
        ),
        sort_order=sort_order,
    )


def section(
    *,
    section_public_id=SECTION_A,
    display_name="Mains",
    sort_order=1,
    entries=(),
):
    return SimpleNamespace(
        section_public_id=section_public_id,
        section_code="mains",
        display_name=display_name,
        sort_order=sort_order,
        entries=tuple(entries),
    )


def menu(*, sections=(), currency="XAF", creates_financial_truth=False):
    return SimpleNamespace(
        tenant_id=2,
        catalog_reference=SimpleNamespace(
            public_id=CATALOG_ID,
            code="main_menu",
            name="Main Menu",
        ),
        effective_at=NOW,
        pricing_context=SimpleNamespace(
            price_code="retail",
            currency=currency,
            scope_type="tenant",
            scope_id=None,
        ),
        sections=tuple(sections),
        creates_financial_truth=creates_financial_truth,
    )


def adapter(response):
    client = RecordingMenuClient(response)
    result = RealXBOSCatalogAdapter(
        client=client,
        binding_resolver=BindingResolver(binding()),
        effective_at_factory=lambda: NOW,
    )
    return result, client


class LoopBR1RealXBOSCatalogAdapterTests(unittest.TestCase):
    def test_accepted_contract_constants_are_exact(self):
        self.assertEqual(XBOS_CATALOG_READ_OPERATION, "R2Authority.menu")
        self.assertEqual(
            XBOS_CATALOG_READ_PERMISSION,
            "restaurant.menu.read",
        )
        self.assertEqual(
            XBOS_CATALOG_CONTRACT_STATE,
            "xbos-g02-c3-h1b-customer-channel-cross-process-binding-accepted-20260925",
        )

    def test_request_matches_all_seven_accepted_xbos_menu_inputs(self):
        real, client = adapter(menu())
        real.get_catalog(
            merchant_ref="merchant:real:one",
            location_ref="location:real:one",
        )
        request = client.calls[0]
        self.assertEqual(request.tenant_id, 2)
        self.assertEqual(request.catalog_public_id, CATALOG_ID)
        self.assertEqual(request.effective_at, NOW)
        self.assertEqual(request.price_code, "retail")
        self.assertEqual(request.currency, "XAF")
        self.assertEqual(request.scope_type, "tenant")
        self.assertIsNone(request.scope_id)

    def test_empty_catalog_maps_without_synthetic_items(self):
        real, _ = adapter(menu(sections=()))
        projection = real.get_catalog(
            merchant_ref="merchant:real:one",
            location_ref="location:real:one",
        )
        self.assertEqual(projection.catalog_ref, str(CATALOG_ID))
        self.assertEqual(projection.sections, ())
        self.assertEqual(projection.items, ())
        self.assertEqual(projection.currency, "XAF")

    def test_one_item_preserves_canonical_sellable_id_price_currency_and_modifiers(self):
        upstream = menu(
            sections=(
                section(
                    entries=(
                        entry(
                            groups=(GROUP_A,),
                            resolved_price=price(amount=Decimal("4500")),
                        ),
                    )
                ),
            )
        )
        real, _ = adapter(upstream)
        projection = real.get_catalog(
            merchant_ref="merchant:real:one",
            location_ref="location:real:one",
        )
        item = projection.items[0]
        self.assertEqual(item.item_ref, str(TARGET_A))
        self.assertEqual(item.section_ref, str(SECTION_A))
        self.assertEqual(item.display_price, Decimal("4500"))
        self.assertEqual(item.currency, "XAF")
        self.assertEqual(item.modifier_group_refs, (str(GROUP_A),))
        self.assertEqual(item.availability, AvailabilityState.AVAILABLE)
        self.assertEqual(item.name, "Item One")
        self.assertTrue(item.description)
        self.assertTrue(item.media_refs)

    def test_multiple_items_and_sections_map_deterministically(self):
        second = entry(
            catalog_entry_public_id=ENTRY_B,
            target_public_id=TARGET_B,
            target_type="offer",
            target_presentation=target(
                public_id=TARGET_B,
                name="Offer Two",
            ),
            resolved_price=price(
                public_id=PRICE_B,
                target_public_id=TARGET_B,
                amount=Decimal("7000"),
            ),
            sort_order=2,
        )
        upstream = menu(
            sections=(
                section(entries=(entry(),)),
                section(
                    section_public_id=SECTION_B,
                    display_name="Offers",
                    sort_order=2,
                    entries=(second,),
                ),
            )
        )
        real, _ = adapter(upstream)
        projection = real.get_catalog(
            merchant_ref="merchant:real:one",
            location_ref="location:real:one",
        )
        self.assertEqual(
            tuple(item.item_ref for item in projection.items),
            (str(TARGET_A), str(TARGET_B)),
        )
        self.assertEqual(
            tuple(section.section_ref for section in projection.sections),
            (str(SECTION_A), str(SECTION_B)),
        )

    def test_inactive_sellable_returned_by_upstream_fails_closed(self):
        upstream = menu(
            sections=(
                section(
                    entries=(
                        entry(
                            target_presentation=target(active=False),
                        ),
                    )
                ),
            )
        )
        real, _ = adapter(upstream)
        with self.assertRaisesRegex(
            RealXBOSCatalogUnavailable,
            "inactive_target",
        ):
            real.get_catalog(
                merchant_ref="merchant:real:one",
                location_ref="location:real:one",
            )

    def test_currency_is_never_synthesized_or_overridden(self):
        upstream = menu(
            sections=(
                section(
                    entries=(
                        entry(
                            resolved_price=price(currency="USD"),
                        ),
                    )
                ),
            ),
            currency="XAF",
        )
        real, _ = adapter(upstream)
        with self.assertRaisesRegex(
            RealXBOSCatalogUnavailable,
            "item_currency_mismatch",
        ):
            real.get_catalog(
                merchant_ref="merchant:real:one",
                location_ref="location:real:one",
            )

    def test_missing_required_field_fails_closed(self):
        malformed = {
            "tenant_id": 2,
            "catalog_reference": {"public_id": str(CATALOG_ID)},
            "pricing_context": {
                "price_code": "retail",
                "currency": "XAF",
                "scope_type": "tenant",
                "scope_id": None,
            },
            "creates_financial_truth": False,
        }
        real, _ = adapter(malformed)
        with self.assertRaisesRegex(
            RealXBOSCatalogUnavailable,
            "missing_required_field:sections",
        ):
            real.get_catalog(
                merchant_ref="merchant:real:one",
                location_ref="location:real:one",
            )

    def test_malformed_uuid_fails_closed(self):
        malformed = menu()
        malformed.catalog_reference.public_id = "not-a-uuid"
        real, _ = adapter(malformed)
        with self.assertRaisesRegex(
            RealXBOSCatalogUnavailable,
            "invalid_uuid:catalog_public_id",
        ):
            real.get_catalog(
                merchant_ref="merchant:real:one",
                location_ref="location:real:one",
            )

    def test_cross_context_binding_fails_closed_before_read(self):
        wrong = binding()
        wrong = XBOSMenuReadBinding(
            merchant_ref="merchant:other",
            location_ref=wrong.location_ref,
            tenant_id=wrong.tenant_id,
            catalog_public_id=wrong.catalog_public_id,
            price_code=wrong.price_code,
            currency=wrong.currency,
            scope_type=wrong.scope_type,
            scope_id=wrong.scope_id,
        )
        client = RecordingMenuClient(menu())
        real = RealXBOSCatalogAdapter(
            client=client,
            binding_resolver=BindingResolver(wrong),
            effective_at_factory=lambda: NOW,
        )
        with self.assertRaisesRegex(
            RealXBOSCatalogUnavailable,
            "binding_context_mismatch",
        ):
            real.get_catalog(
                merchant_ref="merchant:real:one",
                location_ref="location:real:one",
            )
        self.assertEqual(client.calls, [])

    def test_xbos_4xx_is_fail_closed(self):
        self._assert_client_failure("xbos_4xx")

    def test_xbos_5xx_is_fail_closed(self):
        self._assert_client_failure("xbos_5xx")

    def test_auth_failure_is_fail_closed(self):
        self._assert_client_failure("auth_failure")

    def test_timeout_is_fail_closed(self):
        real, client = adapter(menu())
        client.error = TimeoutError("timeout")
        with self.assertRaisesRegex(
            RealXBOSCatalogUnavailable,
            "timeout",
        ):
            real.get_catalog(
                merchant_ref="merchant:real:one",
                location_ref="location:real:one",
            )

    def test_repeated_read_has_no_local_side_effect_and_same_projection(self):
        real, client = adapter(
            menu(sections=(section(entries=(entry(),)),))
        )
        first = real.get_catalog(
            merchant_ref="merchant:real:one",
            location_ref="location:real:one",
        )
        second = real.get_catalog(
            merchant_ref="merchant:real:one",
            location_ref="location:real:one",
        )
        self.assertEqual(first, second)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(first.items[0].item_ref, str(TARGET_A))

    def test_financial_truth_claim_is_rejected(self):
        real, _ = adapter(menu(creates_financial_truth=True))
        with self.assertRaisesRegex(
            RealXBOSCatalogUnavailable,
            "claimed_financial_truth",
        ):
            real.get_catalog(
                merchant_ref="merchant:real:one",
                location_ref="location:real:one",
            )

    def test_quote_path_is_explicitly_not_materialized_by_this_gate(self):
        real, _ = adapter(menu())
        with self.assertRaises(RealXBOSQuoteUnavailable):
            real.resolve_quote(
                merchant_ref="merchant:real:one",
                location_ref="location:real:one",
                cart=SimpleNamespace(),
                now_epoch=1,
            )

    def test_real_selector_never_silently_falls_back_to_fake(self):
        fake = FakeXBOSCatalogClient()
        with self.assertRaisesRegex(
            XBOSW1ContractUnavailable,
            "REAL_XBOS_CATALOG_RUNTIME_BINDING_UNAVAILABLE",
        ):
            select_xbos_catalog_adapter(
                "real",
                fake=fake,
                real=None,
            )

    def test_real_selector_returns_explicit_real_adapter(self):
        fake = FakeXBOSCatalogClient()
        real, _ = adapter(menu())
        selected = select_xbos_catalog_adapter(
            "real",
            fake=fake,
            real=real,
        )
        self.assertIs(selected, real)

    def test_fake_remains_explicit_local_test_option(self):
        fake = FakeXBOSCatalogClient()
        real, _ = adapter(menu())
        selected = select_xbos_catalog_adapter(
            "fake",
            fake=fake,
            real=real,
        )
        self.assertIs(selected, fake)

    def test_runtime_config_defaults_fake_and_allows_explicit_real(self):
        base = {
            "META_WHATSAPP_GRAPH_API_VERSION": "v99.0",
            "META_WHATSAPP_PHONE_NUMBER_ID": "phone-id",
            "META_WHATSAPP_APP_SECRET": "secret",
            "META_WHATSAPP_VERIFY_TOKEN": "verify",
            "META_WHATSAPP_ACCESS_TOKEN": "token",
            "XAFPAY_CUSTOMER_CHANNEL_XBOS_PRIVATE_BASE_URL": "https://xbos.internal",
        }
        self.assertEqual(
            RuntimeConfig.from_environment(base).xbos_catalog_adapter,
            "fake",
        )
        real_env = dict(base)
        real_env["XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_ADAPTER"] = "real"
        self.assertEqual(
            RuntimeConfig.from_environment(real_env).xbos_catalog_adapter,
            "real",
        )

    def test_runtime_config_rejects_unknown_selector(self):
        env = {
            "META_WHATSAPP_GRAPH_API_VERSION": "v99.0",
            "META_WHATSAPP_PHONE_NUMBER_ID": "phone-id",
            "META_WHATSAPP_APP_SECRET": "secret",
            "META_WHATSAPP_VERIFY_TOKEN": "verify",
            "META_WHATSAPP_ACCESS_TOKEN": "token",
            "XAFPAY_CUSTOMER_CHANNEL_XBOS_PRIVATE_BASE_URL": "https://xbos.internal",
            "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_ADAPTER": "automatic",
        }
        with self.assertRaisesRegex(
            ValueError,
            "invalid_customer_channel_xbos_catalog_adapter",
        ):
            RuntimeConfig.from_environment(env)

    def _assert_client_failure(self, reason: str):
        real, client = adapter(menu())
        client.error = XBOSMenuReadClientError(reason)
        with self.assertRaisesRegex(
            RealXBOSCatalogUnavailable,
            reason,
        ):
            real.get_catalog(
                merchant_ref="merchant:real:one",
                location_ref="location:real:one",
            )


if __name__ == "__main__":
    unittest.main()

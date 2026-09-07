import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_context import FakeXBOSContextClient
from xbos_customer_channel.application.entry_service import EntryContextRejected, EntryContextService
from xbos_customer_channel.entry_context import EntryPurpose, EntryTarget, ReplayPolicy
from xbos_customer_channel.persistence.entry_tokens import InMemoryEntryTokenStore
from xbos_customer_channel.security.entry_tokens import HmacEntryTokenCodec
from xbos_customer_channel.transports.entry_links import EntryLinkBuilder


class XC3EntryContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryEntryTokenStore()
        self.codec = HmacEntryTokenCodec(b"x" * 32)
        self.links = EntryLinkBuilder(
            {
                EntryTarget.WHATSAPP: "https://whatsapp-entry.example/channel",
                EntryTarget.CUSTOMER_WEB: "https://customer-entry.example/entry",
            }
        )
        self.service = EntryContextService(
            xbos_context=FakeXBOSContextClient(),
            token_store=self.store,
            token_codec=self.codec,
            link_builder=self.links,
        )

    def issue_table(self, *, replay_policy=ReplayPolicy.REUSABLE):
        return self.service.issue_entry(
            merchant_ref="merchant:fixture:alpha",
            location_ref="location:fixture:one",
            table_ref="table:fixture:a1",
            dining_area_ref="area:fixture:main",
            purpose=EntryPurpose.DINE_IN,
            expires_at_epoch=2000,
            replay_policy=replay_policy,
            now_epoch=1000,
        )

    def test_resolves_merchant_location_table_from_typed_xbos_context(self) -> None:
        _, token = self.issue_table()
        resolved = self.service.resolve(token, now_epoch=1001)
        self.assertEqual(resolved.merchant_ref, "merchant:fixture:alpha")
        self.assertEqual(resolved.location_ref, "location:fixture:one")
        self.assertEqual(resolved.table_ref, "table:fixture:a1")
        self.assertEqual(resolved.projection.currency, "XAF")
        self.assertIn("dine_in", resolved.projection.allowed_fulfillment_modes)

    def test_public_token_contains_no_merchant_location_table_or_commercial_material(self) -> None:
        record, token = self.issue_table()
        forbidden = (
            record.merchant_ref,
            record.location_ref,
            record.table_ref or "",
            "3000",
            "price",
            "payment",
            "provider",
            "account",
        )
        lowered = token.lower()
        for value in forbidden:
            if value:
                self.assertNotIn(value.lower(), lowered)
        self.assertIn(record.token_ref, token)

    def test_tampering_fails_closed(self) -> None:
        _, token = self.issue_table()
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        with self.assertRaisesRegex(EntryContextRejected, "invalid_entry_token"):
            self.service.resolve(tampered, now_epoch=1001)

    def test_expired_token_fails_closed(self) -> None:
        _, token = self.issue_table()
        with self.assertRaisesRegex(EntryContextRejected, "expired_entry_token"):
            self.service.resolve(token, now_epoch=2000)

    def test_cross_tenant_expected_context_is_rejected(self) -> None:
        _, token = self.issue_table()
        with self.assertRaisesRegex(EntryContextRejected, "cross_tenant_context_rejected"):
            self.service.resolve(
                token,
                now_epoch=1001,
                expected_merchant_ref="merchant:fixture:beta",
            )

    def test_swapping_opaque_reference_without_resigning_is_rejected(self) -> None:
        _, token_a = self.issue_table()
        _, token_b = self.service.issue_entry(
            merchant_ref="merchant:fixture:beta",
            location_ref="location:fixture:two",
            table_ref="table:fixture:b1",
            dining_area_ref=None,
            purpose=EntryPurpose.DINE_IN,
            expires_at_epoch=2000,
            replay_policy=ReplayPolicy.REUSABLE,
            now_epoch=1000,
        )
        parts_a = token_a.split(".")
        parts_b = token_b.split(".")
        parts_a[1] = parts_b[1]
        forged = ".".join(parts_a)
        with self.assertRaisesRegex(EntryContextRejected, "invalid_entry_token"):
            self.service.resolve(forged, now_epoch=1001)

    def test_single_use_entry_rejects_unsafe_replay(self) -> None:
        _, token = self.issue_table(replay_policy=ReplayPolicy.SINGLE_USE)
        self.service.resolve(token, now_epoch=1001)
        with self.assertRaisesRegex(EntryContextRejected, "unsafe_replay_rejected"):
            self.service.resolve(token, now_epoch=1002)

    def test_reusable_table_entry_can_resume(self) -> None:
        _, token = self.issue_table(replay_policy=ReplayPolicy.REUSABLE)
        first = self.service.resolve(token, now_epoch=1001)
        second = self.service.resolve(token, now_epoch=1002)
        self.assertEqual(first, second)

    def test_unknown_enumerated_token_fails_without_fallback(self) -> None:
        unknown = self.codec.issue(
            version=1,
            token_ref="ent_" + "z" * 32,
            expires_at_epoch=2000,
            nonce="n" * 18,
        )
        with self.assertRaisesRegex(EntryContextRejected, "invalid_or_unknown_entry"):
            self.service.resolve(unknown, now_epoch=1001)

    def test_whatsapp_and_web_links_preserve_same_logical_context(self) -> None:
        _, token = self.issue_table()
        links = self.service.launch_links(token)
        self.assertEqual(set(links), {EntryTarget.WHATSAPP, EntryTarget.CUSTOMER_WEB})
        for link in links.values():
            extracted = self.service.token_from_link(link)
            self.assertEqual(extracted, token)
            resolved = self.service.resolve(extracted, now_epoch=1001)
            self.assertEqual(resolved.merchant_ref, "merchant:fixture:alpha")
            self.assertEqual(resolved.table_ref, "table:fixture:a1")

    def test_link_builder_rejects_open_redirect_style_untrusted_base(self) -> None:
        with self.assertRaisesRegex(ValueError, "untrusted_entry_base"):
            EntryLinkBuilder(
                {
                    EntryTarget.WHATSAPP: "javascript:alert(1)",
                    EntryTarget.CUSTOMER_WEB: "https://customer-entry.example/entry",
                }
            )

    def test_link_api_has_no_caller_supplied_redirect(self) -> None:
        _, token = self.issue_table()
        with self.assertRaises(TypeError):
            self.links.build(EntryTarget.CUSTOMER_WEB, token, redirect="https://evil.example")

    def test_dine_in_requires_table_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "table_required_for_dine_in"):
            self.service.issue_entry(
                merchant_ref="merchant:fixture:alpha",
                location_ref="location:fixture:one",
                table_ref=None,
                dining_area_ref=None,
                purpose=EntryPurpose.DINE_IN,
                expires_at_epoch=2000,
                replay_policy=ReplayPolicy.REUSABLE,
                now_epoch=1000,
            )

    def test_token_refs_are_opaque_high_entropy_style_values(self) -> None:
        record, _ = self.issue_table()
        self.assertTrue(record.token_ref.startswith("ent_"))
        self.assertGreaterEqual(len(record.token_ref), 30)
        self.assertNotIn("merchant", record.token_ref)
        self.assertNotIn("location", record.token_ref)
        self.assertNotIn("table", record.token_ref)


if __name__ == "__main__":
    unittest.main()

# CR1 adversarial security coverage appended under bounded remediation authority.
class CR1EntrySecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.xbos = FakeXBOSContextClient()
        self.store = InMemoryEntryTokenStore()
        self.codec = HmacEntryTokenCodec(b"x" * 32)
        self.service = EntryContextService(
            xbos_context=self.xbos,
            token_store=self.store,
            token_codec=self.codec,
            link_builder=EntryLinkBuilder(
                {
                    EntryTarget.WHATSAPP: "https://whatsapp-entry.example/channel",
                    EntryTarget.CUSTOMER_WEB: "https://customer-entry.example/entry",
                }
            ),
        )

    def issue(self, *, replay_policy=ReplayPolicy.REUSABLE):
        return self.service.issue_entry(
            merchant_ref="merchant:fixture:alpha",
            location_ref="location:fixture:one",
            table_ref="table:fixture:a1",
            dining_area_ref="area:fixture:main",
            purpose=EntryPurpose.DINE_IN,
            expires_at_epoch=2000,
            replay_policy=replay_policy,
            now_epoch=1000,
        )

    def test_cr1_a01_foreign_table_denied(self) -> None:
        with self.assertRaisesRegex(EntryContextRejected, "entry_context_unavailable"):
            self.service.issue_entry(
                merchant_ref="merchant:fixture:alpha",
                location_ref="location:fixture:one",
                table_ref="table:fixture:b1",
                dining_area_ref=None,
                purpose=EntryPurpose.DINE_IN,
                expires_at_epoch=2000,
                replay_policy=ReplayPolicy.REUSABLE,
                now_epoch=1000,
            )

    def test_cr1_a02_a03_wrong_location_or_merchant_table_denied(self) -> None:
        for merchant, location, table in (
            ("merchant:fixture:alpha", "location:fixture:two", "table:fixture:b1"),
            ("merchant:fixture:beta", "location:fixture:two", "table:fixture:a1"),
        ):
            with self.subTest(merchant=merchant, location=location, table=table):
                with self.assertRaises(EntryContextRejected):
                    self.service.issue_entry(
                        merchant_ref=merchant,
                        location_ref=location,
                        table_ref=table,
                        dining_area_ref=None,
                        purpose=EntryPurpose.DINE_IN,
                        expires_at_epoch=2000,
                        replay_policy=ReplayPolicy.REUSABLE,
                        now_epoch=1000,
                    )

    def test_cr1_a04_fake_tenant_scope_is_server_attested(self) -> None:
        record_a, _ = self.issue()
        record_b, _ = self.service.issue_entry(
            merchant_ref="merchant:fixture:beta",
            location_ref="location:fixture:two",
            table_ref="table:fixture:b1",
            dining_area_ref=None,
            purpose=EntryPurpose.DINE_IN,
            expires_at_epoch=2000,
            replay_policy=ReplayPolicy.REUSABLE,
            now_epoch=1000,
        )
        self.assertEqual(record_a.tenant_ref, "tenant:fixture:a")
        self.assertEqual(record_b.tenant_ref, "tenant:fixture:b")
        self.assertNotEqual(record_a.tenant_ref, record_b.tenant_ref)

    def test_cr1_a11_reassignment_fails_closed_without_consuming_token(self) -> None:
        record, token = self.issue(replay_policy=ReplayPolicy.SINGLE_USE)
        self.xbos.reassign_table(
            merchant_ref=record.merchant_ref,
            location_ref=record.location_ref,
            table_ref=record.table_ref or "",
            dining_area_ref=record.dining_area_ref,
        )
        with self.assertRaisesRegex(EntryContextRejected, "stale_entry_context_rejected"):
            self.service.resolve(token, now_epoch=1001)
        self.assertIsNone(self.store.get(record.token_ref).consumed_at_epoch)

    def test_cr1_a14_two_concurrent_single_use_resolvers_exactly_one_success(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        _, token = self.issue(replay_policy=ReplayPolicy.SINGLE_USE)

        def resolve_once() -> str:
            try:
                self.service.resolve(token, now_epoch=1001)
                return "success"
            except EntryContextRejected:
                return "deny"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: resolve_once(), range(2)))
        self.assertEqual(results.count("success"), 1)
        self.assertEqual(results.count("deny"), 1)

    def test_cr1_c02_eight_concurrent_single_use_resolvers_exactly_one_success(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        _, token = self.issue(replay_policy=ReplayPolicy.SINGLE_USE)

        def resolve_once() -> str:
            try:
                self.service.resolve(token, now_epoch=1001)
                return "success"
            except EntryContextRejected:
                return "deny"

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: resolve_once(), range(8)))
        self.assertEqual(results.count("success"), 1)
        self.assertEqual(results.count("deny"), 7)

    def test_cr1_c03_eight_concurrent_reusable_resolvers_all_succeed(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        _, token = self.issue(replay_policy=ReplayPolicy.REUSABLE)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.service.resolve(token, now_epoch=1001), range(8)))
        self.assertEqual(len(results), 8)
        self.assertTrue(all(result.table_ref == "table:fixture:a1" for result in results))

    def test_cr1_a17_public_token_stays_minimized(self) -> None:
        record, token = self.issue()
        for forbidden in (record.tenant_ref, record.merchant_ref, record.location_ref, record.table_ref, record.context_binding_ref):
            if forbidden:
                self.assertNotIn(forbidden, token)

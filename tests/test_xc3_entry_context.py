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

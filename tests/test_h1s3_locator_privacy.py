from __future__ import annotations

import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.persistence.postgres.serialization import LocatorKeyRing


class H1S3LocatorPrivacyTests(unittest.TestCase):
    def test_known_vector_matches_exact_canonical_form(self) -> None:
        key = "test-key-material-not-a-real-secret"
        sender = "237600000001"
        endpoint = "meta-phone-number-id"
        material = (
            "v1\x1fwhatsapp\x1fmeta-phone-number-id\x1f237600000001"
        ).encode("utf-8")
        expected = hmac.new(
            key.encode("utf-8"),
            material,
            hashlib.sha256,
        ).hexdigest()

        actual = LocatorKeyRing.derive(
            key=key,
            channel_code="whatsapp",
            provider_endpoint_ref=endpoint,
            raw_sender=sender,
        )

        self.assertEqual(actual, expected)
        self.assertRegex(actual, r"^[0-9a-f]{64}$")

    def test_current_and_previous_key_lookup_are_supported(self) -> None:
        ring = LocatorKeyRing(
            current_key="current-test-key",
            current_version="v2",
            previous_key="previous-test-key",
            previous_version="v1",
        )
        kwargs = {
            "channel_code": "whatsapp",
            "provider_endpoint_ref": "meta-phone-number-id",
            "raw_sender": "237600000002",
        }

        candidates = ring.candidates(**kwargs)
        self.assertEqual(
            tuple(candidate.key_version for candidate in candidates),
            ("v2", "v1"),
        )
        self.assertFalse(candidates[0].needs_rehash)
        self.assertTrue(candidates[1].needs_rehash)

        previous_match = ring.match(
            stored_digest=candidates[1].digest,
            stored_key_version="v1",
            **kwargs,
        )
        self.assertIsNotNone(previous_match)
        self.assertTrue(previous_match.needs_rehash)

        current_match = ring.match(
            stored_digest=candidates[0].digest,
            stored_key_version="v2",
            **kwargs,
        )
        self.assertIsNotNone(current_match)
        self.assertFalse(current_match.needs_rehash)

    def test_raw_sender_and_hmac_keys_are_not_record_payload_fields(self) -> None:
        migration = (
            ROOT
            / "migrations"
            / "versions"
            / "0001_customer_channel_durable_runtime_state.py"
        ).read_text(encoding="utf-8").casefold()

        forbidden_column_tokens = (
            '"raw_sender"',
            '"raw_provider_sender"',
            '"whatsapp_phone"',
            '"phone_number"',
            '"locator_hmac_key"',
            '"hmac_key"',
        )
        for token in forbidden_column_tokens:
            with self.subTest(token=token):
                self.assertNotIn(token, migration)

    def test_serializer_documents_do_not_gain_provider_locator_fields(self) -> None:
        serializer = (
            ROOT
            / "src"
            / "xbos_customer_channel"
            / "persistence"
            / "postgres"
            / "serialization.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"raw_sender":', serializer)
        self.assertNotIn('"phone_number":', serializer)
        self.assertNotIn('"locator_hmac_key":', serializer)

    def test_key_ring_versions_can_be_recorded_without_key_material(self) -> None:
        ring = LocatorKeyRing(
            current_key="unit-current-key",
            current_version="v2",
            previous_key="unit-previous-key",
            previous_version="v1",
        )
        representation = json.dumps(
            {
                "current_version": ring.current_version,
                "previous_version": ring.previous_version,
            },
            sort_keys=True,
        )
        self.assertNotIn(ring.current_key, representation)
        self.assertNotIn(ring.previous_key, representation)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.identity import ConsentPurpose
from xbos_customer_channel.observability.events import SafeEvent, event_dict
from xbos_customer_channel.persistence.identity_records import ChannelIdentityRecord, ConsentAuditRecord
from xbos_customer_channel.security.identity_refs import hash_channel_user_ref


class XC2PrivacyTests(unittest.TestCase):
    def test_identity_persistence_has_no_raw_phone_or_contact_address(self) -> None:
        fields = set(ChannelIdentityRecord.__dataclass_fields__)
        forbidden = {"phone", "phone_number", "channel_address", "email", "name", "message_body"}
        self.assertFalse(fields & forbidden)
        self.assertIn("channel_user_ref_hash", fields)

    def test_channel_reference_hash_is_stable_and_not_raw_value(self) -> None:
        raw = "+237600000000"
        first = hash_channel_user_ref(channel="whatsapp", channel_user_ref=raw, secret="fixture-secret")
        second = hash_channel_user_ref(channel="whatsapp", channel_user_ref=raw, secret="fixture-secret")
        self.assertEqual(first, second)
        self.assertNotEqual(first, raw)
        self.assertNotIn(raw, first)

    def test_consent_audit_distinguishes_purpose(self) -> None:
        one = ConsentAuditRecord("consent:1", "identity:1", ConsentPurpose.TRANSACTIONAL, True, "evidence:1")
        two = ConsentAuditRecord("consent:2", "identity:1", ConsentPurpose.MARKETING, False, "evidence:2")
        self.assertNotEqual(one.purpose, two.purpose)

    def test_safe_event_does_not_add_identity_pii(self) -> None:
        payload = event_dict(SafeEvent("identity.resolved", "corr:xc2", "session:1", "identity", "recognized"))
        forbidden = {"phone", "channel_user_ref", "channel_address", "name", "email", "message_body"}
        self.assertFalse(set(payload) & forbidden)


if __name__ == "__main__":
    unittest.main()

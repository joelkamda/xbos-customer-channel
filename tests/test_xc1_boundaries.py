import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.observability.events import SafeEvent, event_dict
from xbos_customer_channel.persistence.channel_records import ChannelSessionRecord


class XC1BoundaryTests(unittest.TestCase):
    def test_channel_persistence_contains_only_channel_metadata(self) -> None:
        fields = set(ChannelSessionRecord.__dataclass_fields__)
        forbidden = {"balance", "journal", "payment", "order", "receipt", "inventory", "price"}
        self.assertFalse(fields & forbidden)

    def test_observability_envelope_has_no_customer_pii_fields(self) -> None:
        payload = event_dict(SafeEvent("journey.started", "corr:1", "session:1", "xbos", "ok"))
        forbidden = {"phone", "name", "email", "message_body", "card", "account_number"}
        self.assertFalse(set(payload) & forbidden)


if __name__ == "__main__":
    unittest.main()

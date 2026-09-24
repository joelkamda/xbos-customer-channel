from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.persistence.postgres.transport_state_stores import (
    IdempotencyConflict,
    PostgresIdempotencyResultStore,
    PostgresProviderMessageReceiptStore,
    PostgresTransportDeliveryStore,
)


class FakeCursor:
    def __init__(self, rows: list[object | None]) -> None:
        self.rows = list(rows)
        self.executed: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self, *args, **kwargs):
        return self._cursor


def receipt_row() -> dict[str, object]:
    return {
        "provider_message_receipt_ref": UUID(
            "11111111-1111-1111-1111-111111111111"
        ),
        "provider_code": "meta_whatsapp",
        "provider_endpoint_ref": "phone-number-id",
        "provider_message_ref": "wamid.unit.1",
        "event_kind": "message",
        "processing_state": "processed",
        "processing_attempt": 1,
        "row_version": 3,
    }


def idempotency_row(
    *,
    fingerprint: str = "a" * 64,
    status: str = "completed",
) -> dict[str, object]:
    return {
        "idempotency_result_ref": UUID(
            "22222222-2222-2222-2222-222222222222"
        ),
        "scope": "w1_runtime_transition",
        "idempotency_key": "meta_whatsapp:phone-number-id:wamid.unit.1",
        "request_fingerprint": fingerprint,
        "status": status,
        "result_ref": "transition-1",
        "safe_result_json": {"result": "accepted"},
        "row_version": 4,
    }


class H1S3TransportStateStoreTests(unittest.TestCase):
    def test_duplicate_provider_message_returns_existing_receipt(self) -> None:
        cursor = FakeCursor([None, receipt_row()])
        store = PostgresProviderMessageReceiptStore(
            lambda: FakeConnection(cursor)
        )
        result, created = store.get_or_create(
            provider_code="meta_whatsapp",
            provider_endpoint_ref="phone-number-id",
            provider_message_ref="wamid.unit.1",
            event_kind="message",
            conversation_ref="conversation-1",
            sender_lookup_hash="b" * 64,
            occurred_at_utc=datetime.now(timezone.utc),
            payload_digest="c" * 64,
            normalized_action_code="TEXT",
            now_utc=datetime.now(timezone.utc),
            purge_after_utc=datetime.now(timezone.utc),
        )
        self.assertFalse(created)
        self.assertEqual(result.provider_message_ref, "wamid.unit.1")
        self.assertEqual(len(cursor.executed), 2)
        self.assertIn("ON CONFLICT", cursor.executed[0][0])
        self.assertIn("DO NOTHING", cursor.executed[0][0])

    def test_same_idempotency_key_and_fingerprint_returns_existing_result(self) -> None:
        cursor = FakeCursor([None, idempotency_row()])
        store = PostgresIdempotencyResultStore(
            lambda: FakeConnection(cursor)
        )
        result, created = store.reserve(
            scope="w1_runtime_transition",
            idempotency_key="meta_whatsapp:phone-number-id:wamid.unit.1",
            request_fingerprint="a" * 64,
            conversation_ref="conversation-1",
            session_ref="session-1",
            now_utc=datetime.now(timezone.utc),
            purge_after_utc=datetime.now(timezone.utc),
        )
        self.assertFalse(created)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.request_fingerprint, "a" * 64)

    def test_same_idempotency_key_different_fingerprint_fails_closed(self) -> None:
        cursor = FakeCursor([None, idempotency_row(fingerprint="d" * 64)])
        store = PostgresIdempotencyResultStore(
            lambda: FakeConnection(cursor)
        )
        with self.assertRaisesRegex(
            IdempotencyConflict,
            "different_fingerprint",
        ):
            store.reserve(
                scope="w1_runtime_transition",
                idempotency_key="meta_whatsapp:phone-number-id:wamid.unit.1",
                request_fingerprint="a" * 64,
                conversation_ref="conversation-1",
                session_ref="session-1",
                now_utc=datetime.now(timezone.utc),
                purge_after_utc=datetime.now(timezone.utc),
            )

    def test_unknown_upstream_outcome_is_persisted_not_completed(self) -> None:
        row = idempotency_row(status="unknown")
        row["result_ref"] = None
        row["safe_result_json"] = None
        row["row_version"] = 5
        cursor = FakeCursor([row])
        store = PostgresIdempotencyResultStore(
            lambda: FakeConnection(cursor)
        )
        result = store.mark_unknown(
            idempotency_result_ref=UUID(
                "22222222-2222-2222-2222-222222222222"
            ),
            expected_row_version=4,
            now_utc=datetime.now(timezone.utc),
        )
        self.assertEqual(result.status, "unknown")
        sql = cursor.executed[0][0]
        self.assertIn("status = 'unknown'", sql)
        self.assertIn("status = 'in_progress'", sql)
        self.assertIn("row_version = %s", sql)

    def test_delivery_uniqueness_is_database_enforced_by_migration_contract(self) -> None:
        migration = (
            ROOT
            / "migrations"
            / "versions"
            / "0001_customer_channel_durable_runtime_state.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "uq_transport_delivery_receipt_ordinal",
            migration,
        )
        self.assertIn(
            "uq_transport_delivery_provider_message",
            migration,
        )
        self.assertIn(
            "provider_message_ref IS NOT NULL",
            migration,
        )

    def test_unknown_send_outcome_does_not_encode_automatic_retry(self) -> None:
        source = (
            ROOT
            / "src"
            / "xbos_customer_channel"
            / "persistence"
            / "postgres"
            / "transport_state_stores.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"unknown"', source)
        self.assertNotIn("auto_retry", source)
        self.assertNotIn("automatic_retry", source)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.entry_context import EntryPurpose
from xbos_customer_channel.session_state import ChannelState, CustomerSessionSnapshot
from xbos_customer_channel.persistence.postgres.security_state_stores import (
    ActiveIdentityBindingAmbiguous,
    ActiveSessionAmbiguous,
    PostgresCustomerSessionStore,
    PostgresEntryTokenStore,
    PostgresIdentityBindingStore,
    PostgresProvenanceStore,
    ProvenanceHandleConflict,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False

    def cursor(self, *args, **kwargs):
        return self.cursor_value


def replacement_session() -> CustomerSessionSnapshot:
    return CustomerSessionSnapshot(
        session_ref="session-2",
        conversation_ref="conversation-1",
        correlation_ref="correlation-1",
        state=ChannelState.BROWSING,
        entry_token_ref="token-2",
        owner_identity_ref="identity-1",
        tenant_ref="tenant-1",
        merchant_ref="merchant-1",
        location_ref="location-1",
        entry_purpose=EntryPurpose.TAKEAWAY,
        context_binding_ref="context-1",
        created_at_epoch=200,
        expires_at_epoch=86600,
        generation=2,
        predecessor_session_ref="session-1",
        security_binding_complete=True,
    )


def active_session_row(session_ref="session-1"):
    return {
        "session_ref": session_ref,
        "conversation_ref": "conversation-1",
        "correlation_ref": "correlation-1",
        "state": "browsing",
        "entry_token_ref": "token-1",
        "owner_identity_ref": "identity-1",
        "tenant_ref": "tenant-1",
        "merchant_ref": "merchant-1",
        "location_ref": "location-1",
        "table_ref": None,
        "dining_area_ref": None,
        "entry_purpose": "takeaway",
        "context_binding_ref": "context-1",
        "created_at_utc": datetime.fromtimestamp(100, timezone.utc),
        "expires_at_utc": datetime.fromtimestamp(1000, timezone.utc),
        "generation": 1,
        "predecessor_session_ref": None,
        "rotated_to_session_ref": None,
        "invalidated_at_utc": None,
        "cart_ref": None,
        "quote_ref": None,
        "order_ref": None,
        "payment_ref": None,
        "last_upstream_evidence_ref": None,
        "human_handoff_ref": None,
        "transition_count": 0,
    }


def identity_binding_row(binding_ref="binding-1"):
    return {
        "identity_binding_ref": binding_ref,
        "identity_ref": "identity-1",
        "canonical_channel_subject_ref": "subject-1",
        "subject_attestation_ref": "attestation-1",
        "conversation_ref": "conversation-1",
        "tenant_ref": "tenant-1",
        "merchant_ref": "merchant-1",
        "location_ref": "location-1",
        "table_ref": None,
        "dining_area_ref": None,
        "context_binding_ref": "context-1",
        "issued_at_utc": datetime.fromtimestamp(100, timezone.utc),
        "expires_at_utc": datetime.fromtimestamp(1000, timezone.utc),
    }


class H1S3SecurityStateStoreTests(unittest.TestCase):
    def test_entry_token_single_use_claim_is_atomic(self) -> None:
        cursor = FakeCursor([None])
        store = PostgresEntryTokenStore(lambda: FakeConnection(cursor))
        result = store.consume_if_unconsumed("token-1", consumed_at_epoch=500)
        self.assertIsNone(result)
        sql = cursor.executed[0][0]
        self.assertIn("consumed_at_utc IS NULL", sql)
        self.assertIn("expires_at_utc > %s", sql)
        self.assertIn("row_version = row_version + 1", sql)
    def test_provenance_handle_conflict_fails_closed(self) -> None:
        row = {
            "provenance_handle_ref": "confirmation-1",
            "client_submit_ref_claim": "submit-a",
        }
        cursor = FakeCursor([row])
        store = PostgresProvenanceStore(lambda: FakeConnection(cursor))
        with self.assertRaisesRegex(
            ProvenanceHandleConflict,
            "confirmation_handle_client_submit_conflict",
        ):
            store.claim_confirmation(
                provenance_handle_ref="confirmation-1",
                client_submit_ref="submit-b",
                updated_at_utc=datetime.now(timezone.utc),
            )
        self.assertEqual(len(cursor.executed), 1)
        self.assertIn("FOR UPDATE", cursor.executed[0][0])

    def test_session_rotation_is_lock_cas_insert_in_one_transaction(self) -> None:
        current = {
            "owner_identity_ref": "identity-1",
            "invalidated_at_utc": None,
            "rotated_to_session_ref": None,
            "expires_at_utc": datetime.fromtimestamp(1000, timezone.utc),
            "generation": 1,
            "row_version": 7,
        }
        cursor = FakeCursor([current, (8,)])
        store = PostgresCustomerSessionStore(lambda: FakeConnection(cursor))
        result = store.rotate_if_active(
            session_ref="session-1",
            expected_owner_identity_ref="identity-1",
            now_epoch=500,
            replacement=replacement_session(),
        )
        self.assertEqual(result.session_ref, "session-2")
        self.assertEqual(len(cursor.executed), 3)
        self.assertIn("FOR UPDATE", cursor.executed[0][0])
        self.assertIn("row_version = row_version + 1", cursor.executed[1][0])
        self.assertIn("INSERT INTO channel_session", cursor.executed[2][0])

    def test_rotated_predecessor_cannot_reactivate(self) -> None:
        current = {
            "owner_identity_ref": "identity-1",
            "invalidated_at_utc": datetime.fromtimestamp(400, timezone.utc),
            "rotated_to_session_ref": "session-2",
            "expires_at_utc": datetime.fromtimestamp(1000, timezone.utc),
            "generation": 1,
            "row_version": 8,
        }
        cursor = FakeCursor([current])
        store = PostgresCustomerSessionStore(lambda: FakeConnection(cursor))
        result = store.rotate_if_active(
            session_ref="session-1",
            expected_owner_identity_ref="identity-1",
            now_epoch=500,
            replacement=replacement_session(),
        )
        self.assertIsNone(result)
        self.assertEqual(len(cursor.executed), 1)

    def test_active_session_lookup_returns_exactly_one_unbound_snapshot(self) -> None:
        cursor = FakeCursor([active_session_row(), None])
        store = PostgresCustomerSessionStore(lambda: FakeConnection(cursor))
        result = store.resolve_active_for_conversation(
            "conversation-1",
            now_utc=datetime.fromtimestamp(500, timezone.utc),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.session_ref, "session-1")
        self.assertFalse(result.security_binding_complete)
        sql = cursor.executed[0][0]
        self.assertIn("invalidated_at_utc IS NULL", sql)
        self.assertIn("rotated_to_session_ref IS NULL", sql)
        self.assertIn("expires_at_utc > %s", sql)
        self.assertIn("LIMIT 2", sql)

    def test_active_session_lookup_fails_closed_on_ambiguity(self) -> None:
        cursor = FakeCursor([
            active_session_row("session-2"),
            active_session_row("session-1"),
        ])
        store = PostgresCustomerSessionStore(lambda: FakeConnection(cursor))
        with self.assertRaises(ActiveSessionAmbiguous):
            store.resolve_active_for_conversation(
                "conversation-1",
                now_utc=datetime.fromtimestamp(500, timezone.utc),
            )

    def test_active_identity_binding_lookup_matches_session_context(self) -> None:
        cursor = FakeCursor([identity_binding_row(), None])
        store = PostgresIdentityBindingStore(lambda: FakeConnection(cursor))
        session = replacement_session()
        result = store.resolve_active_for_session_context(
            session,
            now_utc=datetime.fromtimestamp(500, timezone.utc),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.identity_ref, "identity-1")
        sql = cursor.executed[0][0]
        self.assertIn("identity_ref = %s", sql)
        self.assertIn("conversation_ref = %s", sql)
        self.assertIn("IS NOT DISTINCT FROM", sql)
        self.assertIn("expires_at_utc > %s", sql)
        self.assertIn("LIMIT 2", sql)

    def test_active_identity_binding_lookup_fails_closed_on_ambiguity(self) -> None:
        cursor = FakeCursor([
            identity_binding_row("binding-2"),
            identity_binding_row("binding-1"),
        ])
        store = PostgresIdentityBindingStore(lambda: FakeConnection(cursor))
        with self.assertRaises(ActiveIdentityBindingAmbiguous):
            store.resolve_active_for_session_context(
                replacement_session(),
                now_utc=datetime.fromtimestamp(500, timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()

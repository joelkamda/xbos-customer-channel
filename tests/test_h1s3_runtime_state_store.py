from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.persistence.postgres.database import (
    DatabaseConfig,
    psycopg_connection_factory,
)
from xbos_customer_channel.persistence.postgres.runtime_state_store import (
    PostgresRuntimeStateStore,
    SessionGenerationConflict,
    StaleRuntimeStateVersion,
)


class FakeCursor:
    def __init__(self, rows: list[object | None]) -> None:
        self.rows = rows
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


class H1S3RuntimeStateStoreTests(unittest.TestCase):
    def test_runtime_state_cas_rejects_stale_row_version(self) -> None:
        cursor = FakeCursor([None])
        store = PostgresRuntimeStateStore(lambda: FakeConnection(cursor))
        with self.assertRaises(StaleRuntimeStateVersion):
            store.compare_and_swap(
                session_ref="session-1",
                expected_row_version=4,
                state_schema_version=1,
                interaction_state_json={"state_schema_version": 1},
                updated_at_utc=datetime.now(timezone.utc),
                purge_after_utc=datetime.now(timezone.utc),
                last_provider_message_ref="wamid.1",
                last_transition_ref="transition-1",
            )
        sql, params = cursor.executed[0]
        self.assertIn("row_version = row_version + 1", sql)
        self.assertIn("AND row_version = %s", sql)
        self.assertEqual(params[-1], 4)

    def test_runtime_state_cas_returns_incremented_version(self) -> None:
        cursor = FakeCursor([(5,)])
        store = PostgresRuntimeStateStore(lambda: FakeConnection(cursor))
        version = store.compare_and_swap(
            session_ref="session-1",
            expected_row_version=4,
            state_schema_version=1,
            interaction_state_json={"state_schema_version": 1},
            updated_at_utc=datetime.now(timezone.utc),
            purge_after_utc=datetime.now(timezone.utc),
            last_provider_message_ref=None,
            last_transition_ref=None,
        )
        self.assertEqual(version, 5)

    def test_session_generation_conflict_fails_closed(self) -> None:
        cursor = FakeCursor([(1,)])
        store = PostgresRuntimeStateStore(lambda: FakeConnection(cursor))
        with self.assertRaises(SessionGenerationConflict):
            store.assert_session_generation_available(
                conversation_ref="conversation-1",
                generation=3,
            )

    def test_database_configuration_is_lazy(self) -> None:
        with patch(
            "xbos_customer_channel.persistence.postgres.database.psycopg.connect"
        ) as connect:
            config = DatabaseConfig.from_environment(
                {
                    "XAFPAY_CUSTOMER_CHANNEL_DATABASE_URL":
                    "postgresql://private-db/db?sslmode=require"
                }
            )
            factory = psycopg_connection_factory(config)
            self.assertTrue(callable(factory))
            connect.assert_not_called()

    def test_database_config_requires_sslmode_require(self) -> None:
        config = DatabaseConfig.from_environment(
            {
                "XAFPAY_CUSTOMER_CHANNEL_DATABASE_URL":
                "postgresql://private-db/db"
            }
        )
        self.assertTrue(config.database_url.endswith("sslmode=require"))
        with self.assertRaisesRegex(ValueError, "sslmode_require"):
            DatabaseConfig.from_environment(
                {
                    "XAFPAY_CUSTOMER_CHANNEL_DATABASE_URL":
                    "postgresql://private-db/db?sslmode=disable"
                }
            )


if __name__ == "__main__":
    unittest.main()

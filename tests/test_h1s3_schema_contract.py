from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.persistence.postgres.schema import ACCEPTED_TABLES


class H1S3SchemaContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.migration = (
            ROOT
            / "migrations"
            / "versions"
            / "0001_customer_channel_durable_runtime_state.py"
        ).read_text(encoding="utf-8")
        self.pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    def test_exact_nine_accepted_tables_exist_in_migration_source(self) -> None:
        expected = (
            "channel_conversation",
            "channel_session",
            "w1_runtime_state",
            "channel_identity_binding",
            "provider_message_receipt",
            "idempotency_result",
            "provenance_handle",
            "transport_delivery",
            "entry_token_claim",
        )
        self.assertEqual(ACCEPTED_TABLES, expected)
        created = tuple(
            re.findall(r'op\.create_table\(\s*"([^"]+)"', self.migration)
        )
        self.assertEqual(created, expected)

    def test_required_primary_unique_and_cas_contracts_exist(self) -> None:
        required = (
            "uq_channel_conversation_active_locator",
            "uq_channel_session_conversation_generation",
            "uq_channel_session_legacy_alias",
            "uq_provider_message_receipt_event",
            "uq_idempotency_result_scope_key",
            "uq_transport_delivery_receipt_ordinal",
            "uq_transport_delivery_provider_message",
            "row_version",
            "state_schema_version",
            "interaction_state_json",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.migration)
        self.assertIn("closed_at_utc IS NULL", self.migration)
        self.assertIn("provider_message_ref IS NOT NULL", self.migration)

    def test_no_financial_authority_tables_are_created(self) -> None:
        created = set(
            re.findall(r'op\.create_table\(\s*"([^"]+)"', self.migration)
        )
        forbidden = {
            "payment",
            "payment_request",
            "settlement",
            "accounting",
            "treasury",
            "provider_account",
            "merchant_ledger",
            "restaurant_order",
        }
        self.assertTrue(created.isdisjoint(forbidden))

    def test_dependency_pins_are_exact_and_bounded_to_authorized_families(self) -> None:
        self.assertIn('"psycopg[binary]==3.3.5"', self.pyproject)
        self.assertIn('"alembic==1.17.2"', self.pyproject)
        self.assertIn('"SQLAlchemy==2.0.41"', self.pyproject)
        self.assertNotIn("redis", self.pyproject.lower())

    def test_migration_is_one_head_and_staging_connection_is_not_embedded(self) -> None:
        self.assertIn('revision = "cc_h1s3_0001"', self.migration)
        self.assertIn("down_revision = None", self.migration)
        forbidden = (
            "postgresql://",
            "postgres://",
            "render.com",
            "internal_database_url",
        )
        for token in forbidden:
            self.assertNotIn(token, self.migration.casefold())


if __name__ == "__main__":
    unittest.main()

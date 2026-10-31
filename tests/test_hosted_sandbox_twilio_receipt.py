"""Isolated non-provider tests for the hosted CC receipt boundary."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.persistence.postgres import hosted_twilio_receipt as unit
from xbos_customer_channel.persistence.postgres.database import PostgresUnavailable
from xbos_customer_channel.runtime.app import _sandbox_receipt_ledger
from xbos_customer_channel.transports.twilio_signed_ingress import (
    IngressNotConfigured,
    LocalNoCommerceReplayLedger,
)

KEY = "a" * 64
DIGEST = "b" * 64


class FakeCursor:
    def __init__(self, *, db=unit.STAGING_DATABASE, prior=None, prior_state="processed"):
        self.db = db
        self.prior = prior
        self.prior_state = prior_state
        self.state = ""
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def execute(self, query, values=None):
        self.state = " ".join(query.split())
        self.statements.append((self.state, values))

    def fetchone(self):
        if "SELECT current_database()" in self.state:
            return {"name": self.db}
        if "FROM pg_roles WHERE rolname = current_user" in self.state:
            return {"rolname": "cc_swes_receipt_test", "rolsuper": False,
                    "rolcreaterole": False, "rolcreatedb": False,
                    "rolbypassrls": False, "rolreplication": False,
                    "authenticated_role": "cc_swes_receipt_test"}
        if "SELECT has_schema_privilege" in self.state:
            return {"schema_usage": True, "schema_create": False,
                    "can_select": True, "can_insert": True, "can_update": True,
                    "can_delete": False, "can_truncate": False,
                    "can_reference": False, "can_trigger": False}
        if "INSERT INTO provider_message_receipt" in self.state:
            return {"payload_digest": DIGEST} if self.prior is None else None
        if "SELECT payload_digest,processing_state" in self.state:
            return {"payload_digest": self.prior, "processing_state": self.prior_state}
        if "UPDATE provider_message_receipt" in self.state:
            if "processing_state='failed_retryable'" in self.state:
                return {"provider_message_receipt_ref": "fake-uuid"} if self.prior_state == "failed_retryable" else None
            return {"provider_message_receipt_ref": "fake-uuid"}
        raise AssertionError("unexpected SQL")


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, **kwargs):
        return self._cursor


@contextmanager
def fake_transaction(connection_factory):
    yield connection_factory()


@pytest.mark.parametrize("prior,outcome", [
    (None, "accepted"), (DIGEST, "duplicate"), ("c" * 64, "collision"),
])
def test_transactional_unique_claim_and_collision(prior, outcome):
    cursor = FakeCursor(prior=prior)
    ledger = unit.HostedTwilioReceiptLedger(lambda: FakeConnection(cursor))
    with patch.object(unit, "transaction", fake_transaction):
        assert ledger.check_and_claim(
            key=KEY, digest=DIGEST, now_epoch=int(datetime.now(UTC).timestamp())
        ) == outcome
    assert any("ON CONFLICT" in sql for sql, _ in cursor.statements)
    assert not any("SECRET" in sql or "customer" in sql for sql, _ in cursor.statements)


def test_wrong_database_never_inserts_receipt():
    cursor = FakeCursor(db="unapproved_database")
    ledger = unit.HostedTwilioReceiptLedger(lambda: FakeConnection(cursor))
    with patch.object(unit, "transaction", fake_transaction):
        with pytest.raises(PostgresUnavailable, match="not_approved"):
            ledger.check_and_claim(key=KEY, digest=DIGEST, now_epoch=1000)
    assert len(cursor.statements) == 1


def test_bad_event_claim_denied_without_database():
    ledger = unit.HostedTwilioReceiptLedger(lambda: (_ for _ in ()).throw(
        AssertionError("must_not_connect")
    ))
    assert ledger.check_and_claim(key="raw_sid", digest=DIGEST, now_epoch=1000) == "unavailable"
    assert ledger.check_and_claim(key=KEY, digest="bad", now_epoch=1000) == "unavailable"


def test_default_legacy_receipt_only_when_hosted_e2e_not_enabled():
    with patch.dict(os.environ, {"CC_HOSTED_SANDBOX_E2E_ENABLED": "0"}):
        assert isinstance(_sandbox_receipt_ledger(), LocalNoCommerceReplayLedger)


def test_hosted_e2e_requires_separately_admitted_durable_store():
    with patch.dict(os.environ, {
        "CC_HOSTED_SANDBOX_E2E_ENABLED": "1",
        "CC_HOSTED_SANDBOX_DURABLE_INGRESS_ENABLED": "0",
    }):
        with pytest.raises(IngressNotConfigured):
            _sandbox_receipt_ledger()

def test_failed_claim_is_reattempted_once():
    cursor = FakeCursor(prior=DIGEST, prior_state="failed_retryable")
    ledger = unit.HostedTwilioReceiptLedger(lambda: FakeConnection(cursor))
    with patch.object(unit, "transaction", fake_transaction):
        assert ledger.check_and_claim(key=KEY, digest=DIGEST, now_epoch=1000) == "accepted"
    assert any("UPDATE provider_message_receipt" in sql for sql, _ in cursor.statements)


@pytest.mark.parametrize("success", [True, False])
def test_claim_finish_is_postgres_guarded(success):
    cursor = FakeCursor()
    ledger = unit.HostedTwilioReceiptLedger(lambda: FakeConnection(cursor))
    with patch.object(unit, "transaction", fake_transaction):
        ledger.finish_claim(key=KEY, now_epoch=1000, success=success)
    assert len(cursor.statements) == 4
    assert "processing_state=%s" in cursor.statements[-1][0]
    assert cursor.statements[-1][1][0] == ("processed" if success else "failed_retryable")


@pytest.mark.parametrize("role_overrides", [
    {"rolname": "xafpay_customer_channel_staging_db_user"},
    {"rolsuper": True},
    {"rolcreaterole": True},
    {"rolcreatedb": True},
    {"rolbypassrls": True},
    {"rolreplication": True},
    {"authenticated_role": "xafpay_customer_channel_staging_db_user"},
])
def test_privileged_or_unapproved_receipt_role_fails_before_write(role_overrides):
    class RoleCursor(FakeCursor):
        def fetchone(self):
            if "FROM pg_roles WHERE rolname = current_user" in self.state:
                role = {"rolname": "cc_swes_receipt_test", "rolsuper": False,
                        "rolcreaterole": False, "rolcreatedb": False,
                        "rolbypassrls": False, "rolreplication": False, "authenticated_role": "cc_swes_receipt_test"}
                role.update(role_overrides)
                return role
            return super().fetchone()
    cursor = RoleCursor()
    ledger = unit.HostedTwilioReceiptLedger(lambda: FakeConnection(cursor))
    with patch.object(unit, "transaction", fake_transaction):
        with pytest.raises(PostgresUnavailable, match="receipt_role_not_least_privileged"):
            ledger.check_and_claim(key=KEY, digest=DIGEST, now_epoch=1000)
    assert not any("INSERT INTO" in sql or "UPDATE " in sql
                   for sql, _ in cursor.statements)


@pytest.mark.parametrize("grant_override", [
    {"schema_usage": False}, {"schema_create": True},
    {"can_select": False}, {"can_insert": False}, {"can_update": False},
    {"can_delete": True}, {"can_truncate": True},
    {"can_reference": True}, {"can_trigger": True},
    {"can_update": None},
])
def test_excessive_or_insufficient_receipt_permissions_fail_before_write(grant_override):
    class PermissionCursor(FakeCursor):
        def fetchone(self):
            if "SELECT has_schema_privilege" in self.state:
                grants = super().fetchone()
                grants.update(grant_override)
                return grants
            return super().fetchone()
    cursor = PermissionCursor()
    ledger = unit.HostedTwilioReceiptLedger(lambda: FakeConnection(cursor))
    with patch.object(unit, "transaction", fake_transaction):
        with pytest.raises(PostgresUnavailable, match="receipt_table_privilege_scope_not_admitted"):
            ledger.check_and_claim(key=KEY, digest=DIGEST, now_epoch=1000)
    assert not any("INSERT INTO" in statement or "UPDATE " in statement
                   for statement, _ in cursor.statements)

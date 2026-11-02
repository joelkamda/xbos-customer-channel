"""R48 CC -> WND real signed-provider event contract: positive and fail-closed."""
from __future__ import annotations

import hashlib
import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from xbos_customer_channel.persistence.postgres.hosted_twilio_receipt import (
    ProcessedTwilioReceipt,
)
from xbos_customer_channel.persistence.postgres.serialization import LocatorKeyRing
from xbos_customer_channel.transports.swes_r48_verified_event import (
    MERCHANT,
    SCHEMA,
    TENANT,
    VerifiedEventNotAdmitted,
    build_verified_wnd_event,
    wnd_delivery_headers,
)
from xbos_customer_channel.transports.twilio_signed_ingress import (
    LocalNoCommerceReplayLedger,
    SandboxIngressConfig,
    verify_sandbox_inbound,
)
from xbos_customer_channel.transports.twilio_whatsapp import (
    TwilioWhatsAppConfig,
    canonical_twilio_form_digest,
    twilio_signature,
)

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
TOKEN = "fixture-only-signing-key"
ACCOUNT = "AC" + "a" * 32
SID = "SM" + "f" * 32
SENDER = "whatsapp:+237600000001"
SANDBOX = "whatsapp:+14155238886"
WEBHOOK = "https://xafpay-customer-channel-staging.onrender.com/webhooks/twilio/whatsapp"
SETTINGS = SandboxIngressConfig(
    TwilioWhatsAppConfig(ACCOUNT, TOKEN, SANDBOX, WEBHOOK),
    SENDER, NOW + timedelta(hours=2), "xafpay-customer-channel-staging.onrender.com"
)
KEY_RING = LocatorKeyRing("fixture-only-private-hmac-key", "key-v1")
FORM = {
    "AccountSid": ACCOUNT, "MessageSid": SID,
    "From": SENDER, "To": SANDBOX, "Body": "MENU",
    "NumMedia": "0", "WaId": "237600000001",
}


def signed(form=FORM):
    return twilio_signature(auth_token=TOKEN, url=WEBHOOK, form=form)


def raw(form=FORM):
    return urlencode(form).encode("utf-8")


def claim(form=FORM, signature=None, now=NOW, ledger=None):
    return verify_sandbox_inbound(
        settings=SETTINGS,
        ledger=ledger or LocalNoCommerceReplayLedger(),
        raw_body=raw(form),
        content_type="application/x-www-form-urlencoded",
        signature=signature or signed(form),
        request_host="xafpay-customer-channel-staging.onrender.com",
        forwarded_proto="https",
        now=now,
    )


def processed(form=FORM, now=NOW):
    digest = hashlib.sha256(
        (form["MessageSid"]+"\x00"+form["To"]+"\x00"+form["AccountSid"]).encode()
    ).hexdigest()
    return ProcessedTwilioReceipt(
        digest, canonical_twilio_form_digest(form), "processed", 1, 2, now
    )


def build(*, form=FORM, signature=None, verified=None, receipt=None,
          settings=SETTINGS, now=NOW, key_ring=KEY_RING):
    return build_verified_wnd_event(
        settings=settings,
        verified=verified or claim(form, signature=signature, now=now),
        raw_body=raw(form),
        signature=signature or signed(form),
        content_type="application/x-www-form-urlencoded",
        received_at=now,
        receipt=receipt or processed(form, now),
        sender_key_ring=key_ring,
    )


def test_exact_real_provider_signature_durable_receipt_to_wnd_contract():
    first = build()
    body = first.as_private_payload()
    assert body["schema"] == SCHEMA
    assert body["tenant_id"] == TENANT
    assert body["merchant_public_id"] == MERCHANT
    assert body["channel"] == "twilio_whatsapp"
    assert body["provider_message_id"] == SID
    assert body["server_received_at_utc"] == NOW.isoformat()
    assert body["verification"]["timestamp_source"] == "cc_server_received"
    assert body["verification"]["verification_state"] == "channel_verified"
    assert body["verification"]["identity_resolution_state"] == "channel_identified"
    assert body["verification"]["authentication_reference"] is None
    assert body["receipt"]["processing_state"] == "processed"
    assert body["receipt"]["processing_attempt"] == 1
    assert len(body["event_id"]) == 64
    assert body["correlation_ref"]
    assert len(body["sender_reference"]["value"]) == 64
    assert SENDER not in json.dumps(body)
    assert "237600000001" not in json.dumps(body)
    assert TOKEN not in json.dumps(body)
    assert body["audience"] == "wnd-austin-track-a-ia0"
    assert body["context_purpose"] == "restaurant.customer_channel.checkout"
    assert build().as_private_payload() == body


def test_distinct_signed_message_has_same_sender_different_event_and_correlation():
    second_form = {**FORM, "MessageSid": "SM" + "1" * 32}
    first = build()
    second = build(form=second_form)
    assert second.event_id != first.event_id
    assert second.correlation_ref != first.correlation_ref
    assert second.sender_lookup_ref == first.sender_lookup_ref


@pytest.mark.parametrize("change", [
    {"processing_state": "processing"},
    {"processing_state": "failed_retryable"},
    {"processing_attempt": 2},
    {"processing_attempt": 0},
    {"row_version": 1},
    {"provider_message_ref_hash": "0" * 64},
    {"payload_digest": "0" * 64},
    {"occurred_at_utc": NOW + timedelta(minutes=3)},
    {"occurred_at_utc": NOW - timedelta(minutes=3)},
])
def test_nonfinal_or_mismatched_receipt_never_builds_event(change):
    with pytest.raises(VerifiedEventNotAdmitted):
        build(receipt=replace(processed(), **change))


def test_duplicate_verified_event_never_builds_second_envelope():
    ledger = LocalNoCommerceReplayLedger()
    first = claim(ledger=ledger)
    duplicate = claim(ledger=ledger)
    assert first.disposition == "accepted"
    assert duplicate.disposition == "duplicate"
    with pytest.raises(VerifiedEventNotAdmitted):
        build(verified=duplicate)


@pytest.mark.parametrize("change", [
    {"From": "whatsapp:+237600000099"},
    {"To": "whatsapp:+14150000000"},
    {"AccountSid": "AC"+"b"*32},
    {"NumMedia": "1"},
    {"Body": "X"*501},
])
def test_unadmitted_signed_provider_form_denied(change):
    mutated = {**FORM, **change}
    with pytest.raises((VerifiedEventNotAdmitted, ValueError)):
        build(form=mutated,verified=claim())


def test_signed_form_content_alteration_invalidates_canonical_receipt():
    mutated = {**FORM, "Body": "PAY"}
    with pytest.raises(VerifiedEventNotAdmitted):
        build(form=mutated, signature=signed(mutated), verified=claim(),
              receipt=processed())


def test_unsigned_and_tampered_transport_payload_rejected():
    with pytest.raises(VerifiedEventNotAdmitted):
        build(signature="not-a-valid-twilio-signature",verified=claim())


def test_renewal_expiry_and_invalid_legacy_merchant_fail_closed():
    expired = replace(SETTINGS,tester_expires_utc=NOW - timedelta(seconds=1))
    with pytest.raises(VerifiedEventNotAdmitted):
        build(settings=expired)
    assert MERCHANT != "4b196022-65a1-548c-b122-f1f9e4b78bea"


def test_wnd_delivery_header_requires_real_bearer_and_idempotency():
    event = build()
    with pytest.raises(VerifiedEventNotAdmitted):
        wnd_delivery_headers(event,bearer="")
    with pytest.raises(VerifiedEventNotAdmitted):
        wnd_delivery_headers(event,bearer="header\rinjection")
    headers = wnd_delivery_headers(event,bearer="only-fixture-bearer")
    assert headers["X-CC-Event-Id"] == event.event_id
    assert headers["X-Correlation-Ref"] == event.correlation_ref
    assert headers["X-CC-Event-Schema"] == SCHEMA
    assert headers["Authorization"].startswith("Bearer ")


def test_signed_form_cannot_alone_claim_external_customer_authentication():
    event = build().as_private_payload()
    assert event["verification"]["authentication_authority"] is None
    assert event["verification"]["authentication_reference"] is None
    assert event["verification"]["verification_state"] != "authenticated_external"


class _ReceiptCursor:
    def __init__(self, row=None, db="xafpay_customer_channel_staging_db"):
        self.row = row
        self.db = db
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, values=None):
        self.sql = " ".join(sql.split())
        if "SELECT provider_message_ref,payload_digest" in self.sql:
            assert values == ("twilio_whatsapp", "hosted_sandbox_test_only", processed().provider_message_ref_hash)

    def fetchone(self):
        if "current_database" in self.sql:
            return {"name": self.db}
        if "FROM pg_roles" in self.sql:
            return {
                "rolname": "cc_swes_receipt_test", "authenticated_role": "cc_swes_receipt_test",
                "rolsuper": False, "rolcreaterole": False, "rolcreatedb": False,
                "rolbypassrls": False, "rolreplication": False,
            }
        if "has_schema_privilege" in self.sql:
            return {
                "schema_usage": True, "schema_create": False,
                "can_select": True, "can_insert": True, "can_update": True,
                "can_delete": False, "can_truncate": False,
                "can_reference": False, "can_trigger": False,
            }
        return self.row


class _ReceiptConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, **kwargs):
        return self._cursor


@contextmanager
def _receipt_test_transaction(factory):
    yield factory()


def test_restricted_real_pg_readback_sql_accepts_only_first_processed_row():
    from xbos_customer_channel.persistence.postgres import hosted_twilio_receipt as pg

    entry = processed()
    for status, attempts, version, accepted in [
        ("processed", 1, 2, True),
        ("processing", 1, 1, False),
        ("failed_retryable", 1, 2, False),
        ("processed", 2, 3, False),
        ("processed", 1, 1, False),
    ]:
        row = {
            "provider_message_ref": entry.provider_message_ref_hash,
            "payload_digest": entry.payload_digest,
            "processing_state": status,
            "processing_attempt": attempts,
            "row_version": version,
            "occurred_at_utc": NOW,
        }
        cursor = _ReceiptCursor(row=row)
        ledger = pg.HostedTwilioReceiptLedger(lambda cursor=cursor: _ReceiptConnection(cursor))
        with patch.object(pg, "transaction", _receipt_test_transaction):
            result = ledger.read_processed_first(
                key=entry.provider_message_ref_hash, payload_digest=entry.payload_digest
            )
        assert (result == entry) is accepted


def test_restricted_receipt_readback_denies_wrong_db_and_payload_without_mutations():
    from xbos_customer_channel.persistence.postgres import hosted_twilio_receipt as pg
    from xbos_customer_channel.persistence.postgres.database import PostgresUnavailable

    entry = processed()
    cursor = _ReceiptCursor(db="not_cc_staging")
    ledger = pg.HostedTwilioReceiptLedger(lambda cursor=cursor: _ReceiptConnection(cursor))
    with (
        patch.object(pg, "transaction", _receipt_test_transaction),
        pytest.raises(PostgresUnavailable, match="not_approved"),
    ):
        ledger.read_processed_first(
                key=entry.provider_message_ref_hash, payload_digest=entry.payload_digest
            )
    assert "current_database" in cursor.sql
    with pytest.raises(PostgresUnavailable, match="invalid_receipt_readback_identity"):
        ledger.read_processed_first(key="raw_provider_sid",payload_digest=entry.payload_digest)

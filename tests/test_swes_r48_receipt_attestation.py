"""R48 genuine restricted-receipt attestation, default-off and strictly scoped."""
from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid5

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from xbos_customer_channel.persistence.postgres.hosted_twilio_receipt import (
    ProcessedTwilioReceipt,
)
from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.transports.swes_r48_receipt_attestor import (
    ReceiptAttestationDenied,
    WNDAttestorTrust,
    attest_cc_processed_receipt,
)
from xbos_customer_channel.transports.swes_r48_verified_event import (
    CORRELATION_NAMESPACE,
)
from xbos_customer_channel.transports.twilio_signed_ingress import SandboxIngressConfig
from xbos_customer_channel.transports.twilio_whatsapp import TwilioWhatsAppConfig

NOW = datetime.now(UTC).replace(microsecond=0)
ACCOUNT = "AC" + "a" * 32
SID = "SM" + "a" * 32
RECIPIENT = "whatsapp:+14155238886"
BEARER = "this-is-an-isolated-test-only-credential-0001"
DIGEST = "b" * 64
KEY = hashlib.sha256((SID+"\x00"+RECIPIENT+"\x00"+ACCOUNT).encode()).hexdigest()
EVENT_ID = hashlib.sha256(("cc-r48-v1:"+KEY).encode("ascii")).hexdigest()
CORRELATION = str(uuid5(CORRELATION_NAMESPACE,"cc-r48-ia0:"+KEY))
HOST = "xafpay-customer-channel-staging.onrender.com"
URL = "https://" + HOST + "/webhooks/twilio/whatsapp"
PATH = "/internal/swes/track-a/test-only/receipt-attestations"
SETTINGS = SandboxIngressConfig(
    TwilioWhatsAppConfig(ACCOUNT, "fixture-secret", RECIPIENT, URL),
    "whatsapp:+237600000001", NOW+timedelta(hours=1), HOST,
)
TRUST = WNDAttestorTrust(
    hashlib.sha256(BEARER.encode()).hexdigest(), NOW+timedelta(hours=1),
)


def event():
    return {
        "schema": "xafpay.cc.signed_twilio_ia0_ingress.v1",
        "tenant_id": 910001,
        "merchant_public_id": "4b196022-55a1-548c-b122-f1f9e4b78bea",
        "audience": "wnd-austin-track-a-ia0",
        "context_purpose": "restaurant.customer_channel.checkout",
        "channel": "twilio_whatsapp",
        "event_id": EVENT_ID,
        "correlation_ref": CORRELATION,
        "provider_message_id": SID,
        "provider_endpoint_ref": RECIPIENT.removeprefix("whatsapp:"),
        "provider_account_ref": ACCOUNT,
        "server_received_at_utc": NOW.isoformat(),
        "delivered_before_utc": (NOW+timedelta(minutes=5)).isoformat(),
        "sender_reference": {"scheme": "cc_locator_hmac_sha256_v1",
                             "version": "1", "value": "a"*64},
        "receipt": {"authority": "xafpay.cc.hosted_pg17_signed_twilio_receipt",
                    "reference": KEY, "payload_sha256": DIGEST,
                    "processing_state": "processed", "processing_attempt": 1},
        "verification": {"verification_state": "channel_verified"},
    }


class Ledger:
    def __init__(self, result=True):
        self.result = result
        self.calls = []
    def read_processed_first(self, *, key, payload_digest):
        self.calls.append((key,payload_digest))
        if not self.result:
            return None
        return ProcessedTwilioReceipt(KEY, DIGEST, "processed", 1, 2, NOW)


def execute(payload=None, *, bearer=BEARER, ledger=None, now=NOW, trust=TRUST):
    return attest_cc_processed_receipt(
        payload=payload if payload is not None else event(),
        bearer=bearer, trust=trust, settings=SETTINGS,
        ledger=ledger if ledger is not None else Ledger(),
        now=now
    )


def test_restricted_receipt_is_verified_without_falsely_attesting_sender():
    ledger = Ledger()
    response = execute(ledger=ledger)
    assert response["status"] == "processed_receipt_verified"
    assert response["event_id"] == EVENT_ID
    assert response["correlation_ref"] == CORRELATION
    assert response["receipt_reference"] == KEY
    assert response["payload_sha256"] == DIGEST
    assert response["first_processed_attempt"] == 1
    assert response["sender_provenance_attested"] is False
    assert response["customer_authenticated_external"] is False
    assert ledger.calls == [(KEY,DIGEST)]
    assert SID not in str(response)
    assert "237600000001" not in str(response)


@pytest.mark.parametrize("changes", [
    {"tenant_id": 2},
    {"merchant_public_id": "4b196022-65a1-548c-b122-f1f9e4b78bea"},
    {"audience": "wrong"},
    {"schema": "unrecognized"},
    {"context_purpose": "payments.create"},
    {"provider_message_id": "SM"+"c"*32},
    {"provider_account_ref": "AC"+"c"*32},
    {"provider_endpoint_ref": "14150000000"},
    {"event_id": "0"*64},
    {"correlation_ref": "00000000-0000-0000-0000-000000000000"},
    {"server_received_at_utc": (NOW-timedelta(minutes=6)).isoformat()},
    {"delivered_before_utc": (NOW-timedelta(seconds=1)).isoformat()},
])
def test_untrusted_scope_provider_and_binding_claims_denied_before_db(changes):
    payload={**event(),**changes}
    ledger=Ledger()
    with pytest.raises(ReceiptAttestationDenied):
        execute(payload,ledger=ledger)
    assert ledger.calls == []


def test_bad_provider_receipt_or_digest_denied():
    for change in [
        {"reference":"1"*64}, {"payload_sha256":"not-hash"},
        {"processing_state":"processing"},{"processing_attempt":2}
    ]:
        payload=event()
        payload["receipt"]={**payload["receipt"],**change}
        with pytest.raises(ReceiptAttestationDenied):
            execute(payload)


def test_wrong_service_identity_never_looks_up_receipt():
    ledger=Ledger()
    with pytest.raises(ReceiptAttestationDenied):
        execute(bearer="untrusted-short",ledger=ledger)
    assert ledger.calls==[]
    with pytest.raises(ReceiptAttestationDenied):
        execute(bearer="wrong-but-still-long-private-service-token-012345",ledger=ledger)
    assert ledger.calls==[]
    with pytest.raises(ReceiptAttestationDenied):
        execute(now=NOW+timedelta(hours=2),ledger=ledger)
    assert ledger.calls==[]


def test_no_genuine_restricted_pg_receipt_denied():
    with pytest.raises(ReceiptAttestationDenied,match="not_found"):
        execute(ledger=Ledger(False))


def env(**overrides):
    values={
        "CC_R48_WND_ATTEST_ENABLED":"1",
        "CC_R48_WND_ATTEST_TOKEN_SHA256":TRUST.token_sha256,
        "CC_R48_WND_ATTEST_EXPIRES_UTC":TRUST.expires_utc.isoformat(),
        "CC_TWILIO_SANDBOX_INGRESS_ENABLED":"1",
        "CC_TWILIO_NONFINANCIAL_ONLY":"1",
        "CC_TWILIO_ALLOWED_TESTER":SETTINGS.allowed_tester,
        "CC_TWILIO_TESTER_JOIN_EXPIRES_UTC":SETTINGS.tester_expires_utc.isoformat(),
        "TWILIO_ACCOUNT_SID":ACCOUNT,
        "TWILIO_AUTH_TOKEN":"fixture-secret",
        "TWILIO_WHATSAPP_FROM":RECIPIENT,
        "TWILIO_WHATSAPP_WEBHOOK_URL":URL,
    }
    values.update(overrides)
    return values


def test_http_attestor_is_default_off_without_real_independent_credential():
    client=TestClient(app,base_url="https://"+HOST)
    with patch.dict("os.environ",{},clear=True):
        response=client.post(PATH,json=event())
    assert response.status_code==503
    assert "attestation_unavailable" in response.text


def test_http_attestor_rejects_bad_bearer_without_database_read():
    client=TestClient(app,base_url="https://"+HOST)
    with (
        patch.dict("os.environ",env(),clear=True),
        patch("xbos_customer_channel.runtime.app._sandbox_receipt_ledger",return_value=Ledger()) as factory,
    ):
        response=client.post(PATH,json=event(),headers={"Authorization":"Bearer wrong"})
        assert factory.call_count <= 1
    assert response.status_code==403
    assert "attestation_denied" in response.text


def test_authenticated_http_receipt_attestation_requires_live_pg_readback():
    client=TestClient(app,base_url="https://"+HOST)
    ledger=Ledger()
    with (
        patch.dict("os.environ",env(),clear=True),
        patch("xbos_customer_channel.runtime.app._sandbox_receipt_ledger",return_value=ledger),
    ):
        response=client.post(PATH,json=event(),headers={"Authorization":"Bearer "+BEARER})
    assert response.status_code==200
    assert response.json()["status"]=="processed_receipt_verified"
    assert response.json()["sender_provenance_attested"] is False
    assert len(ledger.calls)==1

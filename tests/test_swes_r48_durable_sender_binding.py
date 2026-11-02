"""R48 TEST signed Twilio sender HMAC bound to same committed PG receipt."""
from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.persistence.postgres import hosted_twilio_receipt as pg
from xbos_customer_channel.persistence.postgres.serialization import LocatorKeyRing
from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.transports.swes_r48_receipt_attestor import (
    WNDAttestorTrust,
    attest_cc_processed_receipt,
)
from xbos_customer_channel.transports.swes_r48_signed_sender_binding import (
    SignedSenderBindingUnavailable,
    derive_first_signed_sender_hash,
)
from xbos_customer_channel.transports.swes_r48_verified_event import (
    build_verified_wnd_event,
)
from xbos_customer_channel.transports.twilio_signed_ingress import (
    SandboxIngressConfig,
    verify_sandbox_inbound,
)
from xbos_customer_channel.transports.twilio_whatsapp import (
    TwilioWhatsAppConfig,
    canonical_twilio_form_digest,
    twilio_signature,
)

NOW = datetime(2026, 11, 2, 11, 0, tzinfo=UTC)
ACCOUNT = "AC" + "a"*32
SID = "SM" + "1"*32
SENDER = "whatsapp:+237600000001"
SANDBOX = "whatsapp:+14155238886"
HOST = "xafpay-customer-channel-staging.onrender.com"
URL = "https://" + HOST + "/webhooks/twilio/whatsapp"
TOKEN = "synthetic-twilio-hmac-fixture"
RING = LocatorKeyRing("r48-hmac-test-key", "ring-v1")
FORM = {"AccountSid":ACCOUNT,"MessageSid":SID,"From":SENDER,"To":SANDBOX,
        "Body":"MENU","NumMedia":"0"}
KEY=hashlib.sha256((SID+"\x00"+SANDBOX+"\x00"+ACCOUNT).encode()).hexdigest()
SETTINGS=SandboxIngressConfig(
    TwilioWhatsAppConfig(ACCOUNT,TOKEN,SANDBOX,URL),
    SENDER,NOW+timedelta(hours=1),HOST,
)
AUTH="independent-wnd-test-bearer-fixture-12345"
TRUST=WNDAttestorTrust(hashlib.sha256(AUTH.encode()).hexdigest(),
                      NOW+timedelta(hours=1))


def sign(form=FORM):
    return twilio_signature(auth_token=TOKEN,url=URL,form=form)


def raw(form=FORM):
    return urlencode(form).encode("utf-8")


class PersistentFixtureLedger(pg.HostedTwilioReceiptLedger):
    """Only for local tests: same claimed hash/digest and transaction result."""
    def __init__(self):
        self.records={}
        self.finalized=[]
    def check_and_claim(self, *, key, digest, now_epoch):
        if key in self.records:
            return "duplicate" if self.records[key]["digest"] == digest else "collision"
        self.records[key]={"digest":digest,"state":"processing","sender":None,"when":NOW}
        return "accepted"
    def finish_claim(self, *, key, now_epoch, success, sender_lookup_hash=None):
        row=self.records[key]
        if row["state"]!="processing":
            raise AssertionError("cannot finalize twice")
        if sender_lookup_hash is not None:
            assert success and len(sender_lookup_hash)==64
        row["state"]="processed" if success else "failed_retryable"
        row["sender"]=sender_lookup_hash
        self.finalized.append((success,sender_lookup_hash))
    def read_processed_first(self, *, key, payload_digest):
        row=self.records.get(key)
        if not row or row["state"]!="processed" or row["digest"]!=payload_digest:
            return None
        return pg.ProcessedTwilioReceipt(
            key,payload_digest,"processed",1,2,row["when"],row["sender"]
        )


def scenario_env(*, enabled=True, with_key=True):
    e={
        "TWILIO_ACCOUNT_SID":ACCOUNT,"TWILIO_AUTH_TOKEN":TOKEN,
        "TWILIO_WHATSAPP_FROM":SANDBOX,
        "TWILIO_WHATSAPP_WEBHOOK_URL":URL,
        "CC_TWILIO_ALLOWED_TESTER":SENDER,
        "CC_TWILIO_TESTER_JOIN_EXPIRES_UTC":SETTINGS.tester_expires_utc.isoformat(),
        "CC_TWILIO_SANDBOX_INGRESS_ENABLED":"1",
        "CC_TWILIO_NONFINANCIAL_ONLY":"1",
        "CC_HOSTED_SANDBOX_E2E_ENABLED":"1",
        "CC_HOSTED_SANDBOX_DURABLE_INGRESS_ENABLED":"1",
        "CC_R48_SENDER_BINDING_ENABLED":"1" if enabled else "0",
    }
    if with_key:
        e["XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_KEY_CURRENT"]=RING.current_key
        e["XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_VERSION_CURRENT"]=RING.current_version
    return e


def post_signed(ledger, form=FORM, *, bad_signature=False, enabled=True):
    client=TestClient(app,base_url="https://"+HOST)
    headers={"X-Twilio-Signature":"invalid" if bad_signature else sign(form),
             "X-Forwarded-Proto":"https"}
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)
    with (
        patch.dict("os.environ",scenario_env(enabled=enabled),clear=True),
        patch("xbos_customer_channel.runtime.app._sandbox_receipt_ledger",
              return_value=ledger),
        patch("xbos_customer_channel.runtime.app.datetime",FixedClock),
    ):
        return client.post("/webhooks/twilio/whatsapp",
                           data=form,headers=headers)


def verified_first(form=FORM):
    class FirstClaim:
        def check_and_claim(self, **kwargs):
            return "accepted"
    return verify_sandbox_inbound(
        settings=SETTINGS,ledger=FirstClaim(),
        raw_body=raw(form),content_type="application/x-www-form-urlencoded",
        signature=sign(form),request_host=HOST,forwarded_proto="https",now=NOW,
    )


def test_source_signed_first_receipt_has_keyed_sender_not_raw_phone():
    verified=verified_first()
    value=derive_first_signed_sender_hash(
        settings=SETTINGS,verified=verified,raw_body=raw(),
        signature=sign(),content_type="application/x-www-form-urlencoded",
        received_at=NOW,key_ring=RING,
    )
    assert value==RING.current(
        channel_code="whatsapp",provider_endpoint_ref=SANDBOX.removeprefix("whatsapp:"),
        raw_sender=SENDER.removeprefix("whatsapp:")).digest
    assert SENDER not in value and len(value)==64


@pytest.mark.parametrize("change", [
    {"MessageSid":"SM"+"2"*32},
    {"From":"whatsapp:+237600000002"},
    {"AccountSid":"AC"+"2"*32},
    {"To":"whatsapp:+14150000000"},
    {"Body":"TAMPER"},
])
def test_signed_form_wrong_claim_denied(change):
    verified=verified_first()
    mutated={**FORM,**change}
    with pytest.raises(SignedSenderBindingUnavailable):
        derive_first_signed_sender_hash(
            settings=SETTINGS,verified=verified,raw_body=raw(mutated),
            signature=sign(mutated),content_type="application/x-www-form-urlencoded",
            received_at=NOW,key_ring=RING,
        )


def test_duplicate_signed_ingress_only_first_can_supply_binding():
    ledger=PersistentFixtureLedger()
    first=verified_first()
    assert first.disposition=="accepted"
    ledger.check_and_claim(key=KEY,digest=canonical_twilio_form_digest(FORM),
                           now_epoch=int(NOW.timestamp()))
    ledger.finish_claim(key=KEY,now_epoch=int(NOW.timestamp()),success=True,
                        sender_lookup_hash=RING.current(
                            channel_code="whatsapp",
                            provider_endpoint_ref=SANDBOX.removeprefix("whatsapp:"),
                            raw_sender=SENDER.removeprefix("whatsapp:")
                        ).digest)
    duplicate=verify_sandbox_inbound(
        settings=SETTINGS,ledger=ledger,raw_body=raw(),
        content_type="application/x-www-form-urlencoded",
        signature=sign(),request_host=HOST,forwarded_proto="https",now=NOW
    )
    assert duplicate.disposition=="duplicate"
    with pytest.raises(SignedSenderBindingUnavailable):
        derive_first_signed_sender_hash(
            settings=SETTINGS,verified=duplicate,raw_body=raw(),
            signature=sign(),content_type="application/x-www-form-urlencoded",
            received_at=NOW,key_ring=RING,
        )


def test_attestation_true_only_for_matching_stored_signed_sender():
    ledger=PersistentFixtureLedger()
    key=KEY
    digest=canonical_twilio_form_digest(FORM)
    ledger.check_and_claim(key=key,digest=digest,now_epoch=int(NOW.timestamp()))
    ledger.finish_claim(key=key,now_epoch=int(NOW.timestamp()),success=True,
                        sender_lookup_hash=RING.current(
                            channel_code="whatsapp",
                            provider_endpoint_ref=SANDBOX.removeprefix("whatsapp:"),
                            raw_sender=SENDER.removeprefix("whatsapp:")
                        ).digest)
    event=build_verified_wnd_event(
        settings=SETTINGS,verified=verified_first(),raw_body=raw(),
        signature=sign(),content_type="application/x-www-form-urlencoded",
        received_at=NOW,receipt=ledger.read_processed_first(
            key=key,payload_digest=digest),sender_key_ring=RING
    ).as_private_payload()
    with patch.dict("os.environ",scenario_env(),clear=True):
        proof=attest_cc_processed_receipt(
            payload=event,bearer=AUTH,trust=TRUST,settings=SETTINGS,
            ledger=ledger,now=NOW,
        )
        assert proof["sender_provenance_attested"] is True
        assert proof["customer_authenticated_external"] is False
        altered={**event,"sender_reference":{
            **event["sender_reference"],"value":"0"*64
        }}
        assert attest_cc_processed_receipt(
            payload=altered,bearer=AUTH,trust=TRUST,settings=SETTINGS,
            ledger=ledger,now=NOW,
        )["sender_provenance_attested"] is False
        wrong_ver={**event,"sender_reference":{
            **event["sender_reference"],"version":"not-active"
        }}
        assert attest_cc_processed_receipt(
            payload=wrong_ver,bearer=AUTH,trust=TRUST,settings=SETTINGS,
            ledger=ledger,now=NOW,
        )["sender_provenance_attested"] is False


def test_legacy_null_sender_cannot_attest_true():
    ledger=PersistentFixtureLedger()
    ledger.check_and_claim(key=KEY,digest=canonical_twilio_form_digest(FORM),
                           now_epoch=int(NOW.timestamp()))
    ledger.finish_claim(key=KEY,now_epoch=int(NOW.timestamp()),success=True)
    proof=build_verified_wnd_event(
        settings=SETTINGS,verified=verified_first(),raw_body=raw(),
        signature=sign(),content_type="application/x-www-form-urlencoded",
        received_at=NOW,receipt=ledger.read_processed_first(
            key=KEY,payload_digest=canonical_twilio_form_digest(FORM)
        ),sender_key_ring=RING,
    ).as_private_payload()
    with patch.dict("os.environ",scenario_env(),clear=True):
        assert attest_cc_processed_receipt(
            payload=proof,bearer=AUTH,trust=TRUST,settings=SETTINGS,
            ledger=ledger,now=NOW,
        )["sender_provenance_attested"] is False


def test_actual_signed_http_ingress_persists_sender_on_first_and_not_duplicate():
    ledger=PersistentFixtureLedger()
    first=post_signed(ledger)
    duplicate=post_signed(ledger)
    assert first.status_code==200
    assert first.headers["X-XafPay-Ingress"]=="accepted"
    assert duplicate.status_code==200
    assert duplicate.headers["X-XafPay-Ingress"]=="duplicate"
    assert len(ledger.finalized)==1
    assert ledger.records[KEY]["state"]=="processed"
    assert ledger.records[KEY]["sender"]==RING.current(
        channel_code="whatsapp",
        provider_endpoint_ref=SANDBOX.removeprefix("whatsapp:"),
        raw_sender=SENDER.removeprefix("whatsapp:"),
    ).digest


def test_invalid_signed_webhook_never_persists_sender_or_receipt():
    ledger=PersistentFixtureLedger()
    result=post_signed(ledger,bad_signature=True)
    assert result.status_code==403
    assert ledger.finalized==[]
    assert ledger.records=={}


def test_default_off_durable_receipt_retains_null_sender():
    ledger=PersistentFixtureLedger()
    result=post_signed(ledger,enabled=False)
    assert result.status_code==200
    assert ledger.records[KEY]["sender"] is None
    assert ledger.finalized==[(True,None)]


def test_sql_finalization_atomically_binds_sender_and_rejects_invalid_hash():
    from test_hosted_sandbox_twilio_receipt import (
        FakeConnection,
        FakeCursor,
        fake_transaction,
    )

    cursor=FakeCursor()
    ledger=pg.HostedTwilioReceiptLedger(lambda: FakeConnection(cursor))
    opaque="a"*64
    with patch.object(pg,"transaction",fake_transaction):
        ledger.finish_claim(
            key=KEY,now_epoch=int(NOW.timestamp()),success=True,
            sender_lookup_hash=opaque,
        )
    sql,parameters=cursor.statements[-1]
    assert "processing_state=%s" in sql
    assert "sender_lookup_hash=COALESCE(sender_lookup_hash, %s)" in sql
    assert "sender_lookup_hash IS NULL" in sql
    assert "sender_lookup_hash IS NOT DISTINCT FROM %s" in sql
    assert parameters[0]=="processed"
    assert parameters[1]==opaque
    assert parameters[-1]==opaque

    with pytest.raises(pg.PostgresUnavailable,match="invalid_signed_sender_binding"):
        ledger.finish_claim(
            key=KEY,now_epoch=int(NOW.timestamp()),success=True,
            sender_lookup_hash="raw-phone",
        )
    with pytest.raises(pg.PostgresUnavailable,match="invalid_signed_sender_binding"):
        ledger.finish_claim(
            key=KEY,now_epoch=int(NOW.timestamp()),success=False,
            sender_lookup_hash=opaque,
        )


def test_sql_binding_conflict_fails_closed_without_success_reply():
    from test_hosted_sandbox_twilio_receipt import (
        FakeConnection,
        FakeCursor,
        fake_transaction,
    )
    class NoReturnCursor(FakeCursor):
        def fetchone(self):
            if "UPDATE provider_message_receipt" in self.state:
                return None
            return super().fetchone()
    cursor=NoReturnCursor()
    ledger=pg.HostedTwilioReceiptLedger(lambda: FakeConnection(cursor))
    with (
        patch.object(pg,"transaction",fake_transaction),
        pytest.raises(pg.PostgresUnavailable,match="transition_conflict"),
    ):
        ledger.finish_claim(
            key=KEY,now_epoch=int(NOW.timestamp()),success=True,
            sender_lookup_hash="a"*64,
        )

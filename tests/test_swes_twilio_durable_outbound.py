"""Provider REST qualifications and idempotent delivery: zero real network calls."""
from __future__ import annotations
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import httpx
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))

from xbos_customer_channel.channel_contract import ChannelOutboundIntent
from xbos_customer_channel.persistence.postgres.transport_state_stores import (
    IdempotencyConflict, IdempotencyResult
)
from xbos_customer_channel.transports.swes_twilio_sandbox_outbound_gate import (
    SWESSandboxOutboundNotAdmitted, SWESSandboxOutboundPolicy, SWESSandboxStatusSender
)
from xbos_customer_channel.transports.swes_twilio_durable_outbound import (
    SWESDurableSandboxStatusSender, SWESOutboundDeliveryUncertain,
    SWESOutboundFingerprintConflict
)
from xbos_customer_channel.transports.twilio_whatsapp import (
    TwilioWhatsAppConfig,TwilioWhatsAppOutboundAdapter,TwilioHttpResponse,
)

NOW=datetime(2026,10,31,14,0,tzinfo=timezone.utc)
FROM="whatsapp:+14155238886"
TO="whatsapp:+237600000001"
CONFIG=TwilioWhatsAppConfig(
    "AC00000000000000000000000000000001",
    "test-transport-secret",FROM,"https://sandbox.example.test/whatsapp"
)
POLICY=SWESSandboxOutboundPolicy(True,True,True,TO,FROM,NOW+timedelta(hours=6))
SID="SM00000000000000000000000000000001"


def intent(**override):
    return replace(ChannelOutboundIntent(
        recipient=TO,channel="whatsapp",
        merchant_ref="merchant:approved",conversation_ref="conversation:approved",
        session_ref="session:approved",purpose="sandbox_status_confirmation",
        event_type="wnd_order_status",projection={"body":"TEST ONLY: WND order status confirmed."},
        template_variables={},correlation_id="correlation:governed-status"
    ),**override)


class TrustedVerifier:
    def __init__(self, accepted=True):
        self.accepted=accepted
        self.calls=0
    def verify(self,*,intent):
        self.calls+=1
        return self.accepted


class ProviderHttp:
    def __init__(self,code=201,body=None,error=None):
        self.code=code
        self.body=body
        self.error=error
        self.calls=[]
    def post_form(self,**kwargs):
        self.calls.append({**kwargs,"auth_token":"redacted"})
        if self.error:
            raise self.error
        body=self.body
        if body is None:
            body=json.dumps({"sid":SID}).encode()
        return TwilioHttpResponse(self.code,body)


class DurableStore:
    def __init__(self):
        self.rows={}
        self.reserve_calls=0
        self.unknown_calls=0
        self.complete_calls=0
        self.fail_reserve=False
        self.fail_complete=False
    def reserve(self,**kwargs):
        self.reserve_calls+=1
        if self.fail_reserve:
            raise ConnectionError("database_unavailable")
        key=(kwargs["scope"],kwargs["idempotency_key"])
        row=self.rows.get(key)
        if row is not None:
            if row.request_fingerprint!=kwargs["request_fingerprint"]:
                raise IdempotencyConflict("fingerprint_conflict")
            return row,False
        row=IdempotencyResult(
            idempotency_result_ref=UUID("00000000-0000-0000-0000-000000000777"),
            scope=kwargs["scope"],idempotency_key=kwargs["idempotency_key"],
            request_fingerprint=kwargs["request_fingerprint"],
            status="in_progress",result_ref=None,safe_result_json=None,row_version=1
        )
        self.rows[key]=row
        return row,True
    def _update(self,uuid,expected,**kwargs):
        key,row=next(iter(self.rows.items()))
        assert uuid==row.idempotency_result_ref
        assert expected==row.row_version
        updated=replace(row,row_version=row.row_version+1,**kwargs)
        self.rows[key]=updated
        return updated
    def mark_unknown(self,**kwargs):
        self.unknown_calls+=1
        return self._update(kwargs["idempotency_result_ref"],kwargs["expected_row_version"],status="unknown")
    def complete(self,**kwargs):
        self.complete_calls+=1
        if self.fail_complete:
            raise ConnectionError("commit_lost")
        return self._update(kwargs["idempotency_result_ref"],kwargs["expected_row_version"],
                            status="completed",result_ref=kwargs["result_ref"])


def setup(http=None,store=None,policy=POLICY,evidence=True):
    http=http or ProviderHttp()
    store=store or DurableStore()
    transport=TwilioWhatsAppOutboundAdapter(config=CONFIG,http_client=http)
    gate=SWESSandboxStatusSender(
        config=CONFIG,policy=policy,evidence=TrustedVerifier(evidence),
        transport=transport
    )
    sender=SWESDurableSandboxStatusSender(gate=gate,provider=transport,store=store)
    return sender,http,store


def test_rest_exact_form_credentials_endpoint_and_duplicate_no_second_send():
    sender,http,store=setup()
    first=sender.send(intent=intent(),now=NOW)
    second=sender.send(intent=intent(),now=NOW)
    assert first.provider_message_ref==SID and not first.duplicate
    assert second.provider_message_ref==SID and second.duplicate
    assert len(http.calls)==1 and store.reserve_calls==2 and store.complete_calls==1
    call=http.calls[0]
    assert call["url"]=="https://api.twilio.com/2010-04-01/Accounts/AC00000000000000000000000000000001/Messages.json"
    assert call["account_sid"]==CONFIG.account_sid
    assert call["auth_token"]=="redacted"
    assert call["form"]=={"From":FROM,"To":TO,"Body":"TEST ONLY: WND order status confirmed."}
    assert store.unknown_calls==0
    assert len(store.rows)==1

def test_changed_status_same_correlation_cannot_resend():
    sender,http,_=setup()
    sender.send(intent=intent(),now=NOW)
    with pytest.raises(SWESOutboundDeliveryUncertain,match="reservation_unavailable"):
        sender.send(intent=intent(projection={"body":"Changed status claim"}),now=NOW)
    assert len(http.calls)==1

@pytest.mark.parametrize("code,body",[
    (400,b'{"code":21614}'),
    (401,b'{"code":20003}'),
    (403,b'{"code":20003}'),
    (429,b'{"code":20429}'),
    (500,b'{"code":20500}'),
    (503,b'{"code":20503}'),
    (200,b'{}'),
    (201,b'not-json'),
    (201,b'{"sid":"BAD"}'),
])
def test_provider_errors_mark_unknown_and_never_auto_retry(code,body):
    sender,http,store=setup(ProviderHttp(code,body))
    with pytest.raises(SWESOutboundDeliveryUncertain):
        sender.send(intent=intent(),now=NOW)
    assert len(http.calls)==1
    assert store.unknown_calls==1 and next(iter(store.rows.values())).status=="unknown"
    with pytest.raises(SWESOutboundDeliveryUncertain,match="already_reserved_or_unknown"):
        sender.send(intent=intent(),now=NOW)
    assert len(http.calls)==1

@pytest.mark.parametrize("exc",[
    httpx.ConnectTimeout("connect timed out"),
    httpx.ReadTimeout("response timed out"),
    httpx.NetworkError("link dropped"),
])
def test_ambiguous_transport_failure_never_auto_retries(exc):
    sender,http,store=setup(ProviderHttp(error=exc))
    with pytest.raises(SWESOutboundDeliveryUncertain):
        sender.send(intent=intent(),now=NOW)
    with pytest.raises(SWESOutboundDeliveryUncertain):
        sender.send(intent=intent(),now=NOW)
    assert len(http.calls)==1 and store.unknown_calls==1

def test_completion_db_failure_leaves_unknown_not_replayed():
    store=DurableStore()
    store.fail_complete=True
    sender,http,_=setup(store=store)
    with pytest.raises(SWESOutboundDeliveryUncertain):
        sender.send(intent=intent(),now=NOW)
    assert store.unknown_calls==1
    with pytest.raises(SWESOutboundDeliveryUncertain):
        sender.send(intent=intent(),now=NOW)
    assert len(http.calls)==1

def test_storage_unavailable_fails_before_provider():
    store=DurableStore()
    store.fail_reserve=True
    sender,http,_=setup(store=store)
    with pytest.raises(SWESOutboundDeliveryUncertain,match="reservation_unavailable"):
        sender.send(intent=intent(),now=NOW)
    assert not http.calls

def test_unknown_existing_reservation_without_send_fails_closed():
    store=DurableStore()
    sender,http,_=setup(store=store)
    sender.send(intent=intent(),now=NOW)
    row_key,row=next(iter(store.rows.items()))
    store.rows[row_key]=replace(row,status="in_progress")
    with pytest.raises(SWESOutboundDeliveryUncertain):
        sender.send(intent=intent(),now=NOW)
    assert len(http.calls)==1

@pytest.mark.parametrize("changes",[
    {"enabled":False},{"nonfinancial_only":False},{"tester_consented":False},
    {"allowed_tester":"whatsapp:+237600000002"},
    {"approved_sender":"whatsapp:+14150000000"},
])
def test_policy_failures_do_not_reserve_or_send(changes):
    sender,http,store=setup(policy=replace(POLICY,**changes))
    with pytest.raises(SWESSandboxOutboundNotAdmitted):
        sender.send(intent=intent(),now=NOW)
    assert store.reserve_calls==0 and not http.calls

def test_unverified_status_does_not_reserve_or_send():
    sender,http,store=setup(evidence=False)
    with pytest.raises(SWESSandboxOutboundNotAdmitted):
        sender.send(intent=intent(),now=NOW)
    assert store.reserve_calls==0 and not http.calls

def test_rejected_status_body_does_not_reserve_or_send():
    sender,http,store=setup()
    with pytest.raises(SWESOutboundFingerprintConflict):
        sender.send(intent=intent(projection={"body":"   "}),now=NOW)
    assert store.reserve_calls==0 and not http.calls

def test_expired_tester_no_reservation_or_send():
    sender,http,store=setup()
    with pytest.raises(SWESSandboxOutboundNotAdmitted):
        sender.send(intent=intent(),now=NOW+timedelta(days=1))
    assert store.reserve_calls==0 and not http.calls

"""R48 real signed CC -> WND event dispatch: off by default, exact HTTPS/ack."""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_swes_r48_durable_sender_binding import (
    FORM,
    HOST,
    NOW,
    PersistentFixtureLedger,
    scenario_env,
    sign,
)
from test_swes_r48_verified_event_contract import build, claim, processed

from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.transports.swes_r48_verified_event import (
    VerifiedEventNotAdmitted,
    build_verified_wnd_event,
)
from xbos_customer_channel.transports.swes_r48_wnd_delivery import (
    WNDDeliveryUnavailable,
    WNDEventDeliveryConfig,
    send_verified_cc_event,
)

BEARER="private-service-credential-test-only-abcdefghijklmnopqrstuvwxyz"
URL=("https://wnd-xafpay-demo-austin-backend.onrender.com"
     "/internal/swes/track-a-sandbox/first-ia0")


def test_default_off_and_unqualified_bearer_rejected():
    for env in [{},{"CC_R48_WND_IA0_DELIVERY_ENABLED":"1"},
                {"CC_R48_WND_IA0_DELIVERY_ENABLED":"1",
                 "CC_R48_WND_IA0_BEARER":"weak"}]:
        with pytest.raises(WNDDeliveryUnavailable):
            WNDEventDeliveryConfig.from_environment(env)
    assert WNDEventDeliveryConfig.from_environment({
        "CC_R48_WND_IA0_DELIVERY_ENABLED":"1",
        "CC_R48_WND_IA0_BEARER":BEARER,
    }).bearer == BEARER


def fixture():
    event=build()
    return event,replace(processed(),sender_lookup_hash=event.sender_lookup_ref)


def test_authenticated_https_exact_202_ia0_ack_and_no_redirect():
    event,receipt=fixture()
    real_ids=[str(uuid4()),str(uuid4()),str(uuid4()),str(uuid4())]
    def exchange(req):
        assert str(req.url)==URL
        assert req.headers["Authorization"] == "Bearer " + BEARER
        assert req.headers["X-CC-Event-Id"] == event.event_id
        assert req.headers["X-Idempotency-Key"] == event.event_id
        assert req.headers["X-Correlation-Ref"] == event.correlation_ref
        body=__import__("json").loads(req.content)
        assert body["tenant_id"]==910001
        assert body["merchant_public_id"]=="4b196022-55a1-548c-b122-f1f9e4b78bea"
        assert body["verification"]["authentication_reference"] is None
        return httpx.Response(202,json={
            "event_id":event.event_id,"correlation_ref":event.correlation_ref,
            "verification_state":"channel_verified",
            "ia0_conversation_public_id":real_ids[0],
            "ia0_participant_public_id":real_ids[1],
            "ia0_session_public_id":real_ids[2],
            "context_binding_ref":real_ids[3],
        })
    with httpx.Client(transport=httpx.MockTransport(exchange)) as client:
        ack=send_verified_cc_event(
            event=event,receipt=receipt,config=WNDEventDeliveryConfig(BEARER),
            now=datetime.fromisoformat(event.server_received_at_utc),
            http_client=client,
        )
    assert str(ack.session_public_id)==real_ids[2]
    assert str(ack.context_binding_ref)==real_ids[3]


@pytest.mark.parametrize("modification", [
    {"sender_lookup_hash":None},
    {"sender_lookup_hash":"f"*64},
    {"payload_digest":"f"*64},
    {"processing_state":"processing"},
    {"processing_attempt":2},
    {"row_version":1},
])
def test_untrusted_receipt_never_sends(modification):
    event,receipt=fixture()
    calls=[]
    with (
        httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req))) as client,
        pytest.raises(WNDDeliveryUnavailable),
    ):
        send_verified_cc_event(
            event=event,receipt=replace(receipt,**modification),
            config=WNDEventDeliveryConfig(BEARER),
            now=datetime.fromisoformat(event.server_received_at_utc),
            http_client=client,
        )
    assert calls==[]


def test_expiry_and_malformed_or_redirect_response_fail_closed():
    event,receipt=fixture()
    expiry=datetime.fromisoformat(event.delivered_before_utc)
    with pytest.raises(WNDDeliveryUnavailable,match="expired"):
        send_verified_cc_event(
            event=event,receipt=receipt,config=WNDEventDeliveryConfig(BEARER),
            now=expiry,http_client=None
        )
    for status,result in [(307,None),(401,None),(202,{}),(200,{"event_id":"wrong"})]:
        with httpx.Client(transport=httpx.MockTransport(
            lambda req, status=status, result=result: httpx.Response(status,json=result) if result is not None
            else httpx.Response(status,headers={"Location":"https://example.com"})
        )) as client, pytest.raises(WNDDeliveryUnavailable):
            send_verified_cc_event(
                event=event,receipt=receipt,config=WNDEventDeliveryConfig(BEARER),
                now=datetime.fromisoformat(event.server_received_at_utc),
                http_client=client,
            )


def test_genuine_provider_redelivery_uses_same_first_processed_sender_bound_row():
    from test_swes_r48_verified_event_contract import KEY_RING, SETTINGS, raw, signed

    from xbos_customer_channel.transports.twilio_signed_ingress import (
        LocalNoCommerceReplayLedger,
    )
    ledger=LocalNoCommerceReplayLedger()
    claim(ledger=ledger)
    duplicate=claim(ledger=ledger)
    assert duplicate.disposition=="duplicate"
    event,receipt=fixture()
    assert build_verified_wnd_event(
        settings=SETTINGS,verified=duplicate,raw_body=raw(),
        signature=signed(),content_type="application/x-www-form-urlencoded",
        received_at=receipt.occurred_at_utc,receipt=receipt,
        sender_key_ring=KEY_RING,allow_processed_duplicate=True,
    ).event_id==event.event_id
    with pytest.raises(VerifiedEventNotAdmitted,match="durable_sender_binding_required"):
        build_verified_wnd_event(
            settings=SETTINGS,verified=duplicate,raw_body=raw(),
            signature=signed(),content_type="application/x-www-form-urlencoded",
            received_at=receipt.occurred_at_utc,receipt=replace(receipt,sender_lookup_hash=None),
            sender_key_ring=KEY_RING,allow_processed_duplicate=True,
        )


def test_actual_webhook_gated_delivery_first_and_duplicate_are_same_event():
    ledger=PersistentFixtureLedger()
    deliveries=[]
    env=scenario_env()
    env.update({
        "CC_R48_WND_IA0_DELIVERY_ENABLED":"1",
        "CC_R48_WND_IA0_BEARER":BEARER
    })
    class FixedClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return NOW.astimezone(tz or UTC)
    def fake_send(**kwargs):
        deliveries.append(kwargs)
        return object()
    client=TestClient(app,base_url="https://"+HOST)
    with (
        patch.dict("os.environ",env,clear=True),
        patch("xbos_customer_channel.runtime.app._sandbox_receipt_ledger",return_value=ledger),
        patch("xbos_customer_channel.runtime.app.datetime",FixedClock),
        patch("xbos_customer_channel.runtime.app.send_verified_cc_event",side_effect=fake_send),
    ):
        first=client.post("/webhooks/twilio/whatsapp",data=FORM,headers={
            "X-Twilio-Signature":sign(),"X-Forwarded-Proto":"https"
        })
        duplicate=client.post("/webhooks/twilio/whatsapp",data=FORM,headers={
            "X-Twilio-Signature":sign(),"X-Forwarded-Proto":"https"
        })
    assert first.status_code==200
    assert duplicate.status_code==200
    assert len(deliveries)==2
    assert deliveries[0]["event"].event_id==deliveries[1]["event"].event_id
    assert deliveries[0]["receipt"].sender_lookup_hash is not None
    assert len(ledger.finalized)==1


def test_delivery_flag_cannot_bypass_sender_binding_flag():
    env=scenario_env(enabled=False)
    env["CC_R48_WND_IA0_DELIVERY_ENABLED"]="1"
    client=TestClient(app,base_url="https://"+HOST)
    with patch.dict("os.environ",env,clear=True):
        result=client.post("/webhooks/twilio/whatsapp",data=FORM,headers={
            "X-Twilio-Signature":sign(),"X-Forwarded-Proto":"https"
        })
    assert result.status_code==503

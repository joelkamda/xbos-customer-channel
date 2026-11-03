"""Gated signed Twilio test_order -> WND IA0 -> canonical order contracts."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_swes_r48_durable_sender_binding import (
    FORM,
    HOST,
    NOW,
    PersistentFixtureLedger,
    scenario_env,
    sign,
    verified_first,
)
from test_swes_r48_wnd_delivery import fixture

from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.transports.swes_r48_wnd_delivery import (
    WNDSessionAcknowledgment,
)
from xbos_customer_channel.transports.swes_r48_wnd_order_delivery import (
    WNDOrderDeliveryConfig,
    WNDOrderDeliveryUnavailable,
    send_verified_cc_order,
)

IA0 = "private-signed-ia0-bearer-abcdefghijklmnopqrstuvwxyz"
CHECKOUT = "distinct-canonical-order-bearer-abcdefghijklmnopqrstuvwxyz"
UUIDS = [str(uuid4()) for _ in range(4)]
ORDER = str(uuid4())


def config(**kwargs):
    env = {
        "CC_R48_WND_ORDER_DELIVERY_ENABLED": "1",
        "CC_R48_WND_IA0_DELIVERY_ENABLED": "1",
        "CC_R48_SENDER_BINDING_ENABLED": "1",
        "CC_R48_WND_ORDER_BEARER": CHECKOUT,
        "CC_R48_WND_IA0_BEARER": IA0,
    }
    env.update(kwargs)
    return env


def data():
    event, receipt = fixture()
    ack = WNDSessionAcknowledgment(
        event_id=event.event_id,
        correlation_ref=event.correlation_ref,
        conversation_public_id=uuid4(),
        participant_public_id=uuid4(),
        session_public_id=uuid4(),
        context_binding_ref=uuid4(),
    )
    verified = replace(verified_first(), action="test_order")
    return verified, event, receipt, ack


def test_separate_order_bearer_default_off_and_distinct():
    for env in (
        {}, {"CC_R48_WND_ORDER_DELIVERY_ENABLED": "1"},
        config(CC_R48_WND_ORDER_BEARER=IA0),
        config(CC_R48_WND_ORDER_BEARER="weak"),
        config(CC_R48_WND_IA0_DELIVERY_ENABLED="0"),
        config(CC_R48_SENDER_BINDING_ENABLED="0"),
    ):
        with pytest.raises(WNDOrderDeliveryUnavailable):
            WNDOrderDeliveryConfig.from_environment(env)
    assert WNDOrderDeliveryConfig.from_environment(config()).bearer == CHECKOUT


def test_positive_mock_https_order_response_matches_exact_verified_ia0():
    verified, event, receipt, ack = data()
    calls = []
    def exchange(req):
        calls.append(req)
        assert req.method == "POST"
        assert str(req.url) == (
            "https://wnd-xafpay-demo-austin-backend.onrender.com"
            "/internal/swes/track-a-sandbox/canonical-order"
        )
        assert req.headers["Authorization"] == "Bearer " + CHECKOUT
        assert req.headers["X-CC-Event-Id"] == event.event_id
        assert req.headers["X-Correlation-Ref"] == event.correlation_ref
        b = json.loads(req.content)
        assert set(b) == {
            "context_binding_ref", "correlation_ref", "event_id", "action",
        }
        assert b["context_binding_ref"] == str(ack.context_binding_ref)
        assert b["action"] == "order wnd test"
        return httpx.Response(201, json={
            "schema": "xafpay.wnd.swes.track_a.canonical_order.v1",
            "order_ref": ORDER, "order_state": "SUBMITTED",
            "payment_state": "NOT_INITIATED", "tenant_id": 910001,
            "location_id": 5,
            "merchant_public_id": "4b196022-55a1-548c-b122-f1f9e4b78bea",
            "currency": "XAF", "canonical_amount": "1000",
            "context_binding_ref": str(ack.context_binding_ref),
            "correlation_ref": event.correlation_ref,
        })
    with httpx.Client(transport=httpx.MockTransport(exchange)) as client:
        result = send_verified_cc_order(
            verified=verified, event=event, receipt=receipt, ia0=ack,
            config=WNDOrderDeliveryConfig(CHECKOUT),
            now=datetime.fromisoformat(event.server_received_at_utc),
            http_client=client,
        )
    assert str(result.order_ref) == ORDER
    assert result.canonical_amount == 1000
    assert result.order_state == "SUBMITTED"
    assert len(calls) == 1


@pytest.mark.parametrize("action", ["greeting", "receipt_only"])
def test_greeting_or_menu_cannot_trigger_order(action):
    verified, event, receipt, ack = data()
    verified = replace(verified, action=action)
    with pytest.raises(WNDOrderDeliveryUnavailable):
        send_verified_cc_order(
            verified=verified, event=event, receipt=receipt, ia0=ack,
            config=WNDOrderDeliveryConfig(CHECKOUT),
            now=datetime.fromisoformat(event.server_received_at_utc),
        )


def test_wrong_context_and_durable_sender_identity_never_request_network():
    verified, event, receipt, ack = data()
    for wrong_ack, wrong_receipt in (
        (replace(ack, context_binding_ref=__import__("uuid").UUID(int=0)), receipt),
        (replace(ack, event_id="x" * 64), receipt),
        (ack, replace(receipt, sender_lookup_hash="f" * 64)),
        (ack, replace(receipt, processing_state="processing")),
    ):
        with pytest.raises(WNDOrderDeliveryUnavailable):
            send_verified_cc_order(
                verified=verified, event=event, receipt=wrong_receipt,
                ia0=wrong_ack, config=WNDOrderDeliveryConfig(CHECKOUT),
                now=datetime.fromisoformat(event.server_received_at_utc),
            )


def test_expired_event_rejected_even_with_service_bearer():
    verified, event, receipt, ack = data()
    with pytest.raises(WNDOrderDeliveryUnavailable, match="expired"):
        send_verified_cc_order(
            verified=verified, event=event, receipt=receipt, ia0=ack,
            config=WNDOrderDeliveryConfig(CHECKOUT),
            now=datetime.fromisoformat(event.delivered_before_utc),
        )


@pytest.mark.parametrize("status,bad", [
    (307, {}), (403, {}), (200, {}),
    (201, {"canonical_amount": "999"}),
    (201, {"tenant_id": 2}), (201, {"order_state": "PAID"}),
    (201, {"context_binding_ref": str(uuid4())}),
])
def test_wrong_status_amount_tenant_or_binding_response_fail_closed(status, bad):
    verified, event, receipt, ack = data()
    canonical = {
        "schema": "xafpay.wnd.swes.track_a.canonical_order.v1",
        "order_ref": ORDER, "order_state": "SUBMITTED",
        "payment_state": "NOT_INITIATED", "tenant_id": 910001,
        "location_id": 5,
        "merchant_public_id": "4b196022-55a1-548c-b122-f1f9e4b78bea",
        "currency": "XAF", "canonical_amount": "1000",
        "context_binding_ref": str(ack.context_binding_ref),
        "correlation_ref": event.correlation_ref,
    }
    canonical.update(bad)
    with (
        httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(status, json=canonical)
        )) as client,
        pytest.raises(WNDOrderDeliveryUnavailable),
    ):
        send_verified_cc_order(
            verified=verified, event=event, receipt=receipt, ia0=ack,
            config=WNDOrderDeliveryConfig(CHECKOUT),
            now=datetime.fromisoformat(event.server_received_at_utc),
            http_client=client,
        )


def test_real_verified_order_webhook_runs_ia0_then_order_and_not_legacy_bridge():
    form = FORM | {"Body": "order wnd test"}
    env = scenario_env()
    env.update(config())
    ledger = PersistentFixtureLedger()
    calls = []
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)
    def fake_ia0(**kwargs):
        calls.append(("ia0", kwargs["event"].event_id))
        ev = kwargs["event"]
        return WNDSessionAcknowledgment(
            ev.event_id, ev.correlation_ref, uuid4(), uuid4(), uuid4(), uuid4()
        )
    def fake_order(**kwargs):
        assert kwargs["verified"].action == "test_order"
        assert kwargs["config"].bearer == CHECKOUT
        assert kwargs["event"].event_id == kwargs["ia0"].event_id
        calls.append(("order", kwargs["event"].event_id))
        return object()
    def forbidden_legacy(**_kwargs):
        pytest.fail("legacy P0D test-order bridge must not run")
    client = TestClient(app, base_url="https://" + HOST)
    with (
        patch.dict("os.environ", env, clear=True),
        patch("xbos_customer_channel.runtime.app._sandbox_receipt_ledger",
              return_value=ledger),
        patch("xbos_customer_channel.runtime.app.datetime", FixedClock),
        patch("xbos_customer_channel.runtime.app.send_verified_cc_event",
              side_effect=fake_ia0),
        patch("xbos_customer_channel.runtime.app.send_verified_cc_order",
              side_effect=fake_order),
        patch("xbos_customer_channel.runtime.app.dispatch_p0d_r5_test_order",
              side_effect=forbidden_legacy),
    ):
        first = client.post(
            "/webhooks/twilio/whatsapp", data=form,
            headers={"X-Twilio-Signature": sign(form), "X-Forwarded-Proto": "https"},
        )
        duplicate = client.post(
            "/webhooks/twilio/whatsapp", data=form,
            headers={"X-Twilio-Signature": sign(form), "X-Forwarded-Proto": "https"},
        )
    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200
    assert first.headers["X-XafPay-R5-Commerce"] == "order_submitted"
    assert duplicate.headers["X-XafPay-R5-Commerce"] == "order_reconciled"
    assert [kind for kind, _ in calls] == ["ia0", "order", "ia0", "order"]
    assert len({item for _, item in calls}) == 1

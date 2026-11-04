"""Qualified TEST-only outbound order confirmation: no network or hosted writes."""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.transports.swes_r48_order_confirmation import (
    SWESOrderConfirmationUnavailable,
    make_order_confirmation,
    send_order_confirmation,
)
from xbos_customer_channel.transports.swes_r48_wnd_order_delivery import (
    WNDOrderAcknowledgment,
)
from xbos_customer_channel.transports.swes_twilio_durable_outbound import (
    SWESOutboundDeliveryResult,
)
from xbos_customer_channel.transports.twilio_signed_ingress import (
    SandboxIngressConfig,
    VerifiedSandboxIngress,
)
from xbos_customer_channel.transports.twilio_whatsapp import TwilioWhatsAppConfig

NOW = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
ENV = {
    "CC_R48_WND_ORDER_CONFIRMATION_ENABLED": "1",
    "CC_R48_WND_ORDER_DELIVERY_ENABLED": "1",
    "CC_TWILIO_NONFINANCIAL_ONLY": "1",
    "CC_R48_ORDER_CONFIRMATION_TESTER_CONSENTED": "1",
}
SETTINGS = SandboxIngressConfig(
    provider=TwilioWhatsAppConfig(
        account_sid="AC" + "1" * 32,
        auth_token="test-secret", sender="whatsapp:+14155238886",
        webhook_url="https://xafpay-customer-channel-staging.onrender.com/webhooks/twilio/whatsapp",
    ),
    allowed_tester="whatsapp:+237600000001",
    tester_expires_utc=NOW + timedelta(hours=24),
    expected_host="xafpay-customer-channel-staging.onrender.com",
)
ACK = WNDOrderAcknowledgment(
    order_ref=uuid4(), context_binding_ref=uuid4(),
    correlation_ref="signed-wnd-context", canonical_amount=1000,
    order_state="SUBMITTED",
)
VERIFIED = VerifiedSandboxIngress(
    disposition="accepted", event_digest="a" * 20,
    action="test_order", receipt_key="c" * 64,
    form_payload_digest="f" * 64,
)


class Sender:
    def __init__(self):
        self.calls = []

    def send(self, *, intent, now):
        self.calls.append((intent, now))
        return SWESOutboundDeliveryResult("SM" + "3" * 32, False)


def test_true_wnd_ack_generates_customer_visible_unpaid_test_status():
    sender = Sender()
    result = send_order_confirmation(
        ack=ACK, verified=VERIFIED, settings=SETTINGS, now=NOW, env=ENV,
        sender=sender,
    )
    assert not result.duplicate
    assert len(sender.calls) == 1
    intent, _ = sender.calls[0]
    body = intent.projection["body"]
    assert str(ACK.order_ref) in body
    assert "1,000 XAF" in body
    assert "NOT INITIATED" in body
    assert "No payment has been made" in body
    assert "\\n" not in body  # literal slash-n never reaches WhatsApp
    assert "\n" in body
    assert intent.recipient == SETTINGS.allowed_tester
    assert intent.correlation_id == str(ACK.order_ref)


@pytest.mark.parametrize("overrides", [
    {"verified": replace(VERIFIED, disposition="duplicate")},
    {"verified": replace(VERIFIED, action="greeting")},
    {"verified": replace(VERIFIED, action="receipt_only")},
    {"ack": replace(ACK, canonical_amount=999)},
    {"ack": replace(ACK, order_state="PAID")},
    {"ack": replace(ACK, order_ref=__import__("uuid").UUID(int=0))},
    {"env": {**ENV, "CC_R48_WND_ORDER_CONFIRMATION_ENABLED": "0"}},
    {"env": {**ENV, "CC_R48_ORDER_CONFIRMATION_TESTER_CONSENTED": "0"}},
    {"env": {**ENV, "CC_R48_WND_ORDER_DELIVERY_ENABLED": "0"}},
    {"now": SETTINGS.tester_expires_utc},
])
def test_denials_never_attempt_provider_or_reservation(overrides):
    sender = Sender()
    values = {
        "ack": ACK, "verified": VERIFIED, "settings": SETTINGS,
        "now": NOW, "env": ENV, "sender": sender,
    }
    values.update(overrides)
    with pytest.raises(SWESOrderConfirmationUnavailable):
        send_order_confirmation(**values)
    assert not sender.calls


def test_no_restricted_outbound_database_means_no_twilio_network():
    with pytest.raises(SWESOrderConfirmationUnavailable, match="restricted_outbound_identity"):
        send_order_confirmation(
            ack=ACK, verified=VERIFIED, settings=SETTINGS, now=NOW,
            env=ENV,
        )


def test_canonical_order_uuid_is_notification_idempotency_identity():
    a = make_order_confirmation(
        ack=ACK, verified=VERIFIED, settings=SETTINGS, now=NOW, env=ENV
    )
    b = make_order_confirmation(
        ack=ACK, verified=VERIFIED, settings=SETTINGS, now=NOW, env=ENV
    )
    assert a == b
    assert a.correlation_id == str(ACK.order_ref)
    assert a.session_ref == str(ACK.context_binding_ref)


def test_webhook_verified_order_confirmation_once_after_wnd_ack_and_not_on_replay():
    from unittest.mock import patch

    from fastapi.testclient import TestClient
    from test_swes_r48_durable_sender_binding import (
        FORM,
        HOST,
        PersistentFixtureLedger,
        scenario_env,
        sign,
    )
    from test_swes_r48_durable_sender_binding import (
        NOW as SIGNED_NOW,
    )

    from xbos_customer_channel.runtime.app import app
    from xbos_customer_channel.transports.swes_r48_wnd_delivery import (
        WNDSessionAcknowledgment,
    )

    form = FORM | {"Body": "order wnd test"}
    env = scenario_env()
    env.update({
        "CC_R48_WND_ORDER_DELIVERY_ENABLED": "1",
        "CC_R48_WND_IA0_DELIVERY_ENABLED": "1",
        "CC_R48_SENDER_BINDING_ENABLED": "1",
        "CC_R48_WND_ORDER_BEARER": "distinct-outbound-order-bearer-abcdefghijklmnopqrstuvwxyz",
        "CC_R48_WND_IA0_BEARER": "other-ingest-service-bearer-abcdefghijklmnopqrstuvwxyz",
        "CC_R48_WND_ORDER_CONFIRMATION_ENABLED": "1",
    })
    ledger = PersistentFixtureLedger()
    notifications = []
    order_calls = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return SIGNED_NOW.astimezone(tz or UTC)

    def ia0(**kwargs):
        event = kwargs["event"]
        return WNDSessionAcknowledgment(
            event.event_id, event.correlation_ref,
            uuid4(), uuid4(), uuid4(), uuid4(),
        )

    def order(**kwargs):
        order_calls.append(kwargs["verified"].disposition)
        return replace(ACK, context_binding_ref=kwargs["ia0"].context_binding_ref)

    def notify(**kwargs):
        notifications.append(kwargs)
        return SWESOutboundDeliveryResult("SM" + "4" * 32, False)

    with (
        patch.dict("os.environ", env, clear=True),
        patch("xbos_customer_channel.runtime.app._sandbox_receipt_ledger", return_value=ledger),
        patch("xbos_customer_channel.runtime.app.datetime", Clock),
        patch("xbos_customer_channel.runtime.app.send_verified_cc_event", side_effect=ia0),
        patch("xbos_customer_channel.runtime.app.send_verified_cc_order", side_effect=order),
        patch("xbos_customer_channel.runtime.app.send_order_confirmation", side_effect=notify),
    ):
        client = TestClient(app, base_url="https://" + HOST)
        headers = {"X-Twilio-Signature": sign(form), "X-Forwarded-Proto": "https"}
        first = client.post("/webhooks/twilio/whatsapp", data=form, headers=headers)
        duplicate = client.post("/webhooks/twilio/whatsapp", data=form, headers=headers)
    assert first.status_code == duplicate.status_code == 200
    assert order_calls == ["accepted", "duplicate"]
    assert len(notifications) == 1
    assert str(notifications[0]["ack"].order_ref) == str(ACK.order_ref)
    assert first.headers["X-XafPay-R5-Commerce"] == "order_submitted"
    assert duplicate.headers["X-XafPay-R5-Commerce"] == "order_reconciled"


def test_notification_failure_does_not_undo_canonical_order_or_trigger_second_send():
    from unittest.mock import patch

    from fastapi.testclient import TestClient
    from test_swes_r48_durable_sender_binding import (
        FORM,
        HOST,
        PersistentFixtureLedger,
        scenario_env,
        sign,
    )
    from test_swes_r48_durable_sender_binding import (
        NOW as SIGNED_NOW,
    )

    from xbos_customer_channel.runtime.app import app
    from xbos_customer_channel.transports.swes_r48_wnd_delivery import (
        WNDSessionAcknowledgment,
    )
    from xbos_customer_channel.transports.swes_twilio_durable_outbound import (
        SWESOutboundDeliveryUncertain,
    )

    form = FORM | {"Body": "order wnd test"}
    env = scenario_env() | {
        "CC_R48_WND_ORDER_DELIVERY_ENABLED": "1",
        "CC_R48_WND_IA0_DELIVERY_ENABLED": "1",
        "CC_R48_SENDER_BINDING_ENABLED": "1",
        "CC_R48_WND_ORDER_BEARER": "distinct-outbound-order-bearer-abcdefghijklmnopqrstuvwxyz",
        "CC_R48_WND_IA0_BEARER": "other-ingest-service-bearer-abcdefghijklmnopqrstuvwxyz",
        "CC_R48_WND_ORDER_CONFIRMATION_ENABLED": "1",
    }
    calls = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return SIGNED_NOW.astimezone(tz or UTC)

    def ia0(**kwargs):
        ev = kwargs["event"]
        return WNDSessionAcknowledgment(ev.event_id, ev.correlation_ref, uuid4(), uuid4(), uuid4(), uuid4())

    def order(**kwargs):
        calls.append("canonical")
        return replace(ACK, context_binding_ref=kwargs["ia0"].context_binding_ref)

    def notification(**kwargs):
        calls.append("notification")
        raise SWESOutboundDeliveryUncertain("provider_result_uncertain")

    with (
        patch.dict("os.environ", env, clear=True),
        patch("xbos_customer_channel.runtime.app._sandbox_receipt_ledger",
              return_value=PersistentFixtureLedger()),
        patch("xbos_customer_channel.runtime.app.datetime", Clock),
        patch("xbos_customer_channel.runtime.app.send_verified_cc_event", side_effect=ia0),
        patch("xbos_customer_channel.runtime.app.send_verified_cc_order", side_effect=order),
        patch("xbos_customer_channel.runtime.app.send_order_confirmation", side_effect=notification),
    ):
        client = TestClient(app, base_url="https://" + HOST)
        response = client.post(
            "/webhooks/twilio/whatsapp", data=form,
            headers={"X-Twilio-Signature": sign(form), "X-Forwarded-Proto": "https"},
        )
    assert response.status_code == 200
    assert response.headers["X-XafPay-R5-Commerce"] == "order_submitted_notification_unconfirmed"
    assert calls == ["canonical", "notification"]

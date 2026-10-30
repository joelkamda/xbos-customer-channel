from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.transports.twilio_signed_ingress import (
    IngressNotConfigured,
    IngressRejected,
    LocalNoCommerceReplayLedger,
    SandboxIngressConfig,
    parse_bounded_form,
    verify_sandbox_inbound,
)
from xbos_customer_channel.transports.twilio_whatsapp import twilio_signature


PUBLIC_URL = (
    "https://xafpay-customer-channel-staging.onrender.com"
    "/webhooks/twilio/whatsapp"
)
ACCOUNT_SID = "AC00000000000000000000000000000001"
AUTH_TOKEN = "r4-test-auth-token"
SANDBOX_SENDER = "whatsapp:+14155238886"
TESTER = "whatsapp:+237600000001"
NOW = datetime(2026, 10, 29, 5, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def historical_sandbox_clock(monkeypatch):
    """Freeze the R4 historical test scenario only, never the hosted app."""
    import xbos_customer_channel.runtime.app as app_runtime
    import xbos_customer_channel.transports.twilio_signed_ingress as ingress

    class ScenarioClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc).astimezone(
                tz or timezone.utc
            )

    monkeypatch.setattr(app_runtime, "datetime", ScenarioClock)
    monkeypatch.setattr(ingress, "datetime", ScenarioClock)


def env(*, enabled: bool = True, expires: str = "2026-10-30T05:30:00Z"):
    values = {
        "TWILIO_ACCOUNT_SID": ACCOUNT_SID,
        "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
        "TWILIO_WHATSAPP_FROM": SANDBOX_SENDER,
        "TWILIO_WHATSAPP_WEBHOOK_URL": PUBLIC_URL,
        "CC_TWILIO_ALLOWED_TESTER": TESTER,
        "CC_TWILIO_TESTER_JOIN_EXPIRES_UTC": expires,
        "CC_TWILIO_NONFINANCIAL_ONLY": "1",
    }
    if enabled:
        values["CC_TWILIO_SANDBOX_INGRESS_ENABLED"] = "1"
    return values


def form(*, sid: str = "SMR40001", body: str = "Hello WND", sender: str = TESTER):
    return {
        "AccountSid": ACCOUNT_SID,
        "MessageSid": sid,
        "From": sender,
        "To": SANDBOX_SENDER,
        "Body": body,
        "NumMedia": "0",
        "WaId": "237600000001",
    }


def raw(values: dict[str, str]) -> bytes:
    from urllib.parse import urlencode

    return urlencode(values).encode("utf-8")


def settings(**kwargs) -> SandboxIngressConfig:
    with patch.dict(os.environ, env(**kwargs), clear=True):
        return SandboxIngressConfig.from_environment()


def signed(values: dict[str, str]) -> str:
    return twilio_signature(auth_token=AUTH_TOKEN, url=PUBLIC_URL, form=values)


def verify(
    values: dict[str, str],
    *,
    ledger: LocalNoCommerceReplayLedger | None = None,
    signature: str | None = None,
    host: str = "xafpay-customer-channel-staging.onrender.com",
    proto: str | None = "https",
    now: datetime = NOW,
):
    return verify_sandbox_inbound(
        settings=settings(),
        ledger=ledger or LocalNoCommerceReplayLedger(),
        raw_body=raw(values),
        content_type="application/x-www-form-urlencoded",
        signature=signature if signature is not None else signed(values),
        request_host=host,
        forwarded_proto=proto,
        now=now,
    )


def test_feature_is_fail_closed_when_disabled():
    with patch.dict(os.environ, env(enabled=False), clear=True):
        with pytest.raises(IngressNotConfigured, match="sandbox_ingress_disabled"):
            SandboxIngressConfig.from_environment()


def test_exact_signed_sandbox_form_is_accepted_without_commerce_dispatch():
    result = verify(form())
    assert result.disposition == "accepted"
    assert result.merchant_dispatch_enabled is False
    assert len(result.event_digest) == 20
    assert TESTER not in result.event_digest
    assert "SMR40001" not in result.event_digest


def test_duplicate_retry_is_acknowledged_but_has_no_second_effect():
    ledger = LocalNoCommerceReplayLedger()
    values = form(sid="SMR40002")
    first = verify(values, ledger=ledger)
    second = verify(values, ledger=ledger)
    assert first.disposition == "accepted"
    assert second.disposition == "duplicate"
    assert second.merchant_dispatch_enabled is False


def test_same_message_sid_with_changed_signed_body_is_rejected_as_collision():
    ledger = LocalNoCommerceReplayLedger()
    first = form(sid="SMR40003", body="Hello WND")
    changed = form(sid="SMR40003", body="changed")
    assert verify(first, ledger=ledger).disposition == "accepted"
    with pytest.raises(IngressRejected, match="provider_receipt_conflict_or_capacity"):
        verify(changed, ledger=ledger)


@pytest.mark.parametrize(
    ("change", "signature_override", "host", "proto"),
    [
        ({}, "invalid", "xafpay-customer-channel-staging.onrender.com", "https"),
        ({}, None, "evil.example.test", "https"),
        ({}, None, "xafpay-customer-channel-staging.onrender.com", "http"),
        ({"From": "whatsapp:+237600000099"}, None, "xafpay-customer-channel-staging.onrender.com", "https"),
        ({"AccountSid": "AC99999999999999999999999999999999"}, None, "xafpay-customer-channel-staging.onrender.com", "https"),
        ({"To": "whatsapp:+14150000000"}, None, "xafpay-customer-channel-staging.onrender.com", "https"),
        ({"NumMedia": "1"}, None, "xafpay-customer-channel-staging.onrender.com", "https"),
    ],
)
def test_wrong_signature_host_scheme_sender_account_recipient_or_media_fail_closed(
    change, signature_override, host, proto
):
    values = form(sid="SMR40010")
    values.update(change)
    if signature_override is None:
        signature_override = signed(values)
    with pytest.raises(IngressRejected):
        verify(
            values,
            signature=signature_override,
            host=host,
            proto=proto,
        )


def test_body_tamper_after_signature_fails_closed():
    original = form(sid="SMR40011", body="Hello WND")
    tampered = form(sid="SMR40011", body="tampered")
    with pytest.raises(IngressRejected, match="signature_or_payload_rejected"):
        verify(tampered, signature=signed(original))


def test_expired_join_fails_before_accepting_message():
    with patch.dict(
        os.environ,
        env(expires="2026-10-29T05:00:00Z"),
        clear=True,
    ):
        expired = SandboxIngressConfig.from_environment()
    with pytest.raises(IngressNotConfigured, match="tester_join_expired"):
        verify_sandbox_inbound(
            settings=expired,
            ledger=LocalNoCommerceReplayLedger(),
            raw_body=raw(form(sid="SMR40012")),
            content_type="application/x-www-form-urlencoded",
            signature=signed(form(sid="SMR40012")),
            request_host="xafpay-customer-channel-staging.onrender.com",
            forwarded_proto="https",
            now=NOW,
        )


def test_non_form_and_oversized_payloads_are_rejected():
    with pytest.raises(IngressRejected, match="form_encoding_required"):
        parse_bounded_form(b"a=b", "application/json")
    with pytest.raises(IngressRejected, match="form_size_out_of_bounds"):
        parse_bounded_form(b"x" * 9000, "application/x-www-form-urlencoded")


def test_deployed_app_route_is_default_off_without_activation_environment():
    with patch.dict(os.environ, {}, clear=True):
        client = TestClient(
            app,
            base_url="https://xafpay-customer-channel-staging.onrender.com",
        )
        response = client.post(
            "/webhooks/twilio/whatsapp",
            data=form(sid="SMR40020"),
            headers={"X-Twilio-Signature": "unused"},
        )
    assert response.status_code == 503
    assert response.json()["detail"] == "sandbox_ingress_unavailable"


def test_deployed_app_route_accepts_only_valid_signed_form_and_never_dispatches_commerce():
    values = form(sid="SMR40021")
    with patch.dict(os.environ, env(), clear=True):
        client = TestClient(
            app,
            base_url="https://xafpay-customer-channel-staging.onrender.com",
        )
        response = client.post(
            "/webhooks/twilio/whatsapp",
            data=values,
            headers={
                "X-Twilio-Signature": signed(values),
                "X-Forwarded-Proto": "https",
            },
        )
    assert response.status_code == 200
    assert response.headers["X-XafPay-Ingress"] == "accepted"
    assert response.text == "<Response></Response>"
    assert "merchant" not in response.text.casefold()
    assert "payment" not in response.text.casefold()


def test_existing_meta_webhook_is_preserved():
    paths = {route.path for route in app.routes}
    assert "/webhooks/meta/whatsapp" in paths
    assert "/webhooks/twilio/whatsapp" in paths

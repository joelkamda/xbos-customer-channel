from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import xbos_customer_channel.runtime.app as app_module
from xbos_customer_channel.application.p0d_r5_test_only_bridge import (
    R5_WND_STAGING_HOST,
    R5_WND_STAGING_URL,
    P0DR5BridgeConfig,
    P0DR5BridgeNotConfigured,
    P0DR5BridgeRejected,
    dispatch_p0d_r5_test_order,
)
from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.transports.twilio_signed_ingress import (
    LocalNoCommerceReplayLedger,
)
from xbos_customer_channel.transports.twilio_whatsapp import twilio_signature

PUBLIC_URL = (
    "https://xafpay-customer-channel-staging.onrender.com"
    "/webhooks/twilio/whatsapp"
)
ACCOUNT_SID = "AC00000000000000000000000000000001"
AUTH_TOKEN = "r5-test-auth-token"
SANDBOX_SENDER = "whatsapp:+14155238886"
TESTER = "whatsapp:+237600000001"
NOW = datetime(2026, 10, 29, 6, 30, tzinfo=UTC)

# Historical fake-only test messages have a fixed October 30 join expiry.
# Freeze only these tests at the intended October 29 scenario; never change
# the real application clock or extend the genuine Twilio Sandbox membership.
@pytest.fixture(autouse=True)
def historical_sandbox_clock(monkeypatch):
    import xbos_customer_channel.transports.twilio_signed_ingress as ingress

    class ScenarioClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 29, 7, 0, tzinfo=UTC).astimezone(tz or UTC)

    monkeypatch.setattr(app_module, "datetime", ScenarioClock)
    monkeypatch.setattr(ingress, "datetime", ScenarioClock)

MERCHANT = "10000000-0000-4000-8000-000000000003"
LOCATION = "10000000-0000-4000-8000-000000000002"
CONTEXT = "10000000-0000-4000-8000-000000000008"
SERVICE = "10000000-0000-4000-8000-000000000010"
ENTRY = "10000000-0000-4000-8000-000000000014"
CONTACT = "10000000-0000-4000-8000-000000000009"
DRAFT = "20000000-0000-4000-8000-000000000001"
ORDER = "20000000-0000-4000-8000-000000000002"
CONFIRMATION = "20000000-0000-4000-8000-000000000003"


def ingress_env() -> dict[str, str]:
    return {
        "TWILIO_ACCOUNT_SID": ACCOUNT_SID,
        "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
        "TWILIO_WHATSAPP_FROM": SANDBOX_SENDER,
        "TWILIO_WHATSAPP_WEBHOOK_URL": PUBLIC_URL,
        "CC_TWILIO_ALLOWED_TESTER": TESTER,
        "CC_TWILIO_TESTER_JOIN_EXPIRES_UTC": "2026-10-30T06:30:00Z",
        "CC_TWILIO_NONFINANCIAL_ONLY": "1",
        "CC_TWILIO_SANDBOX_INGRESS_ENABLED": "1",
    }


def bridge_env() -> dict[str, str]:
    return {
        "CC_P0D_R5_WND_BRIDGE_ENABLED": "1",
        "CC_P0D_R5_WND_MERCHANT_ADMITTED": "1",
        "CC_P0D_R5_WND_SERVICE_ADMITTED": "1",
        "CC_P0D_R5_WND_BASE_URL": R5_WND_STAGING_URL,
        "CC_P0D_R5_WND_EXPECTED_HOST": R5_WND_STAGING_HOST,
        "CC_P0D_R5_WND_SERVICE_BEARER": "local-r5-distinct-wnd-bearer",
        "CC_P0D_R5_WND_SERVICE_CREDENTIAL_REF": "wnd-channel-service:r5-existing-test",
        "CC_P0D_R5_WND_TENANT_ID": "910001",
        "CC_P0D_R5_WND_MERCHANT_PUBLIC_ID": MERCHANT,
        "CC_P0D_R5_WND_LOCATION_PUBLIC_ID": LOCATION,
        "CC_P0D_R5_WND_LOCATION_ID": "910201",
        "CC_P0D_R5_WND_CONTEXT_BINDING_PUBLIC_ID": CONTEXT,
        "CC_P0D_R5_WND_SERVICE_IDENTITY_PUBLIC_ID": SERVICE,
        "CC_P0D_R5_WND_CATALOG_ENTRY_REF": ENTRY,
        "CC_P0D_R5_WND_CONTACT_REF": CONTACT,
    }


def form(*, sid: str, body: str) -> dict[str, str]:
    return {
        "AccountSid": ACCOUNT_SID,
        "MessageSid": sid,
        "From": TESTER,
        "To": SANDBOX_SENDER,
        "Body": body,
        "NumMedia": "0",
        "WaId": "237600000001",
    }


def signed(values: dict[str, str]) -> str:
    return twilio_signature(auth_token=AUTH_TOKEN, url=PUBLIC_URL, form=values)


def post(values: dict[str, str]):
    client = TestClient(
        app,
        base_url="https://xafpay-customer-channel-staging.onrender.com",
    )
    return client.post(
        "/webhooks/twilio/whatsapp",
        content=urlencode(values),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": signed(values),
            "X-Forwarded-Proto": "https",
        },
    )


def reset_ledger(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "_twilio_ingress_receipts",
        LocalNoCommerceReplayLedger(),
    )


class RecordingWNDHttp:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def request(self, method, url, *, headers, json, timeout, follow_redirects):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json": json,
                "follow_redirects": follow_redirects,
            }
        )
        path = url.removeprefix(R5_WND_STAGING_URL)
        if path == "/internal/customer-channel/v1/order-drafts":
            payload = {
                "draft_ref": DRAFT,
                "order_ref": ORDER,
                "order_state": "DRAFT",
                "draft_version": 1,
            }
        elif path == f"/internal/customer-channel/v1/order-drafts/{DRAFT}/commands":
            payload = {
                "order_ref": ORDER,
                "currency": "XAF",
                "canonical_total": "3500",
                "lines": [
                    {
                        "unit_price": "1750",
                        "quantity": "2",
                        "line_total": "3500",
                    }
                ],
            }
        elif path == f"/internal/customer-channel/v1/order-drafts/{DRAFT}/confirmation":
            payload = {
                "confirmation_ref": CONFIRMATION,
                "merchant_ref": MERCHANT,
                "location_ref": 910201,
                "service_context_ref": CONTEXT,
                "currency": "XAF",
                "canonical_total": "3500",
            }
        elif path == f"/internal/customer-channel/v1/order-drafts/{DRAFT}/submit":
            payload = {
                "order_ref": ORDER,
                "order_state": "submitted",
            }
        else:
            return httpx.Response(404, json={"error": "unexpected"})
        return httpx.Response(200, json=payload)


def test_hello_wnd_is_receipt_only_and_never_dispatches(monkeypatch):
    reset_ledger(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        app_module,
        "dispatch_p0d_r5_test_order",
        lambda **kwargs: calls.append(kwargs["event_digest"]),
    )
    with patch.dict(os.environ, ingress_env(), clear=True):
        response = post(form(sid="SMR5HELLO01", body="Hello WND"))
    assert response.status_code == 200
    assert response.headers["X-XafPay-R5-Action"] == "greeting"
    assert response.headers["X-XafPay-R5-Commerce"] == "receipt_only"
    assert calls == []


def test_explicit_test_order_is_only_commerce_trigger(monkeypatch):
    reset_ledger(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        app_module,
        "dispatch_p0d_r5_test_order",
        lambda **kwargs: calls.append(kwargs["event_digest"]),
    )
    with patch.dict(os.environ, ingress_env(), clear=True):
        response = post(form(sid="SMR5ORDER01", body="order wnd test"))
    assert response.status_code == 200
    assert response.headers["X-XafPay-R5-Action"] == "test_order"
    assert response.headers["X-XafPay-R5-Commerce"] == "order_submitted"
    assert len(calls) == 1
    assert len(calls[0]) == 20


def test_duplicate_provider_retry_does_not_dispatch_second_order(monkeypatch):
    reset_ledger(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        app_module,
        "dispatch_p0d_r5_test_order",
        lambda **kwargs: calls.append(kwargs["event_digest"]),
    )
    values = form(sid="SMR5ORDER02", body="order wnd test")
    with patch.dict(os.environ, ingress_env(), clear=True):
        first = post(values)
        second = post(values)
    assert first.status_code == 200
    assert first.headers["X-XafPay-R5-Commerce"] == "order_submitted"
    assert second.status_code == 200
    assert second.headers["X-XafPay-Ingress"] == "duplicate"
    assert second.headers["X-XafPay-R5-Commerce"] == "duplicate_no_dispatch"
    assert len(calls) == 1


def test_unrecognized_signed_message_is_receipt_only(monkeypatch):
    reset_ledger(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        app_module,
        "dispatch_p0d_r5_test_order",
        lambda **kwargs: calls.append(kwargs["event_digest"]),
    )
    with patch.dict(os.environ, ingress_env(), clear=True):
        response = post(form(sid="SMR5OTHER01", body="show me the menu"))
    assert response.status_code == 200
    assert response.headers["X-XafPay-R5-Action"] == "receipt_only"
    assert response.headers["X-XafPay-R5-Commerce"] == "receipt_only"
    assert calls == []


def test_test_order_fails_closed_when_bridge_not_admitted(monkeypatch):
    reset_ledger(monkeypatch)
    monkeypatch.setattr(
        app_module,
        "dispatch_p0d_r5_test_order",
        lambda **kwargs: (_ for _ in ()).throw(
            P0DR5BridgeNotConfigured("wnd_bridge_disabled")
        ),
    )
    with patch.dict(os.environ, ingress_env(), clear=True):
        response = post(form(sid="SMR5ORDER03", body="order wnd test"))
    assert response.status_code == 503
    assert response.json()["detail"] == "trusted_wnd_bridge_unavailable"


def test_bridge_config_rejects_tenant2_and_gateway_credential_reference():
    values = bridge_env()
    values["CC_P0D_R5_WND_TENANT_ID"] = "2"
    with pytest.raises(P0DR5BridgeRejected, match="only_r5_synthetic_tenant_allowed"):
        P0DR5BridgeConfig.from_environment(values)

    values = bridge_env()
    values["CC_P0D_R5_WND_SERVICE_CREDENTIAL_REF"] = "gateway:wrong"
    with pytest.raises(
        P0DR5BridgeRejected,
        match="distinct_wnd_service_credential_ref_required",
    ):
        P0DR5BridgeConfig.from_environment(values)


def test_bridge_requires_explicit_feature_and_identity_admission():
    values = bridge_env()
    values.pop("CC_P0D_R5_WND_BRIDGE_ENABLED")
    with pytest.raises(P0DR5BridgeNotConfigured, match="wnd_bridge_disabled"):
        P0DR5BridgeConfig.from_environment(values)

    values = bridge_env()
    values["CC_P0D_R5_WND_SERVICE_ADMITTED"] = "0"
    with pytest.raises(
        P0DR5BridgeNotConfigured,
        match="wnd_bridge_identity_not_admitted",
    ):
        P0DR5BridgeConfig.from_environment(values)


def test_connected_bridge_uses_only_server_owned_binding_and_canonical_wnd_price():
    cfg = P0DR5BridgeConfig.from_environment(bridge_env())
    http = RecordingWNDHttp()
    result = dispatch_p0d_r5_test_order(
        event_digest="a" * 20,
        settings=cfg,
        http_client=http,
    )
    assert result.order_ref == ORDER
    assert result.canonical_total == 3500
    assert result.state == "confirmed"
    assert len(http.calls) == 4
    for call in http.calls:
        headers = call["headers"]
        assert headers["Authorization"] == "Bearer local-r5-distinct-wnd-bearer"
        assert headers["X-XBOS-Context-Binding-Ref"] == CONTEXT
        assert headers["X-Correlation-Ref"] == "p0d-r5:" + "a" * 20
        assert call["follow_redirects"] is False
        assert str(call["url"]).startswith(R5_WND_STAGING_URL)
    assert http.calls[0]["json"] == {
        "client_command_ref": "p0d-r5:" + "a" * 20 + ":create",
        "service_mode": "takeaway",
        "contact_ref": CONTACT,
    }
    assert "amount" not in str(http.calls).casefold()
    assert "gateway:" not in cfg.credential_ref


def test_invalid_event_digest_never_reaches_network():
    cfg = P0DR5BridgeConfig.from_environment(bridge_env())
    http = RecordingWNDHttp()
    with pytest.raises(P0DR5BridgeRejected, match="verified_event_digest_required"):
        dispatch_p0d_r5_test_order(
            event_digest="provider-message-id",
            settings=cfg,
            http_client=http,
        )
    assert http.calls == []


def test_bridge_config_repr_does_not_expose_bearer():
    cfg = P0DR5BridgeConfig.from_environment(bridge_env())
    rendered = repr(cfg)
    assert "local-r5-distinct-wnd-bearer" not in rendered
    assert "bearer=" not in rendered

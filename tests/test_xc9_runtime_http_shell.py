from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.runtime.config import RuntimeConfig


ENV = {
    "META_WHATSAPP_GRAPH_API_VERSION": "v99.0",
    "META_WHATSAPP_PHONE_NUMBER_ID": "phone-id",
    "META_WHATSAPP_APP_SECRET": "runtime-app-secret",
    "META_WHATSAPP_VERIFY_TOKEN": "runtime-verify-token",
    "META_WHATSAPP_ACCESS_TOKEN": "runtime-access-token",
    "XAFPAY_CUSTOMER_CHANNEL_XBOS_PRIVATE_BASE_URL": "http://private-xbos.example:10000",
}


def callback_payload() -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "phone-id"},
                                "messages": [
                                    {
                                        "id": "wamid.runtime.1",
                                        "from": "raw-provider-locator",
                                        "timestamp": "10",
                                        "type": "text",
                                        "text": {"body": "hello"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def signature(body: bytes) -> str:
    digest = hmac.new(
        ENV["META_WHATSAPP_APP_SECRET"].encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return "sha256=" + digest


class XC9RuntimeHttpShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app)

    def test_health_returns_200_without_runtime_config_or_external_calls(self) -> None:
        with patch(
            "xbos_customer_channel.runtime.app._config",
            side_effect=AssertionError("health must not load runtime integration config"),
        ):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["w1_dispatch"], "disabled")

    def test_valid_meta_subscription_returns_challenge(self) -> None:
        response = self.client.get(
            "/webhooks/meta/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": ENV["META_WHATSAPP_VERIFY_TOKEN"],
                "hub.challenge": "challenge-123",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "challenge-123")

    def test_invalid_meta_subscription_fails_closed(self) -> None:
        response = self.client.get(
            "/webhooks/meta/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge-123",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(ENV["META_WHATSAPP_VERIFY_TOKEN"], response.text)

    def test_valid_callback_is_normalized_and_acknowledged_without_w1_dispatch(self) -> None:
        body = callback_payload()
        response = self.client.post(
            "/webhooks/meta/whatsapp",
            content=body,
            headers={"X-Hub-Signature-256": signature(body)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "accepted", "w1_dispatch": "disabled"},
        )

    def test_invalid_signature_is_rejected_before_malformed_payload_processing(self) -> None:
        response = self.client.post(
            "/webhooks/meta/whatsapp",
            content=b"{",
            headers={"X-Hub-Signature-256": "sha256=" + ("0" * 64)},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "invalid_webhook_signature")

    def test_malformed_callback_with_valid_signature_is_rejected(self) -> None:
        body = b"{"
        response = self.client.post(
            "/webhooks/meta/whatsapp",
            content=body,
            headers={"X-Hub-Signature-256": signature(body)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "invalid_webhook_payload")

    def test_secret_values_are_not_exposed_by_runtime_responses_or_source(self) -> None:
        health = self.client.get("/health").text
        invalid = self.client.get(
            "/webhooks/meta/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge-123",
            },
        ).text
        combined = health + invalid + inspect.getsource(app.__class__)
        for secret in (
            ENV["META_WHATSAPP_APP_SECRET"],
            ENV["META_WHATSAPP_VERIFY_TOKEN"],
            ENV["META_WHATSAPP_ACCESS_TOKEN"],
        ):
            self.assertNotIn(secret, combined)

    def test_xbos_base_url_loads_from_environment_only(self) -> None:
        config = RuntimeConfig.from_environment(ENV)
        self.assertEqual(
            config.xbos_private_base_url,
            "http://private-xbos.example:10000",
        )

    def test_runtime_shell_contains_no_real_xbos_or_fixture_state_composition(self) -> None:
        import xbos_customer_channel.runtime.app as runtime_app

        source = inspect.getsource(runtime_app)
        self.assertNotIn("W1WhatsAppRuntime", source)
        self.assertNotIn("InMemoryCustomerSessionStore", source)
        self.assertNotIn("InMemoryEntryTokenStore", source)
        self.assertNotIn("xbos_private_base_url", source)
        self.assertNotIn("httpx", source)


if __name__ == "__main__":
    unittest.main()

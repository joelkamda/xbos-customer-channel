from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, sentinel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from xbos_customer_channel.adapters.real_xbos_catalog import RealXBOSCatalogAdapter
from xbos_customer_channel.adapters.real_xbos_context import RealXBOSContextAdapter
from xbos_customer_channel.adapters.xbos_private_http import PrivateXBOSHTTPClient
from xbos_customer_channel.persistence.postgres.schema import DATABASE_URL_ENV
from xbos_customer_channel.persistence.postgres.serialization import LocatorKeyRing
from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.runtime.config import RuntimeConfig
from xbos_customer_channel.runtime.durable_w1 import (
    compose_durable_w1_session_runtime,
)


BASE_ENV = {
    DATABASE_URL_ENV: "postgresql://runtime:secret@private-db/customer_channel",
    "XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_KEY_CURRENT": "locator-key-current",
    "XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_VERSION_CURRENT": "v1",
    "XAFPAY_CUSTOMER_CHANNEL_XBOS_PRIVATE_BASE_URL": "http://private-xbos.example:10000",
    "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_READ_TOKEN": "catalog-read-token",
    "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_SERVICE_PRINCIPAL": "customer-channel-catalog-reader",
    "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_SERVICE_SCOPE": "restaurant.menu.read",
}

META_ENV = {
    "META_WHATSAPP_GRAPH_API_VERSION": "v99.0",
    "META_WHATSAPP_PHONE_NUMBER_ID": "meta-phone-id",
    "META_WHATSAPP_APP_SECRET": "meta-app-secret",
    "META_WHATSAPP_VERIFY_TOKEN": "meta-verify-token",
    "META_WHATSAPP_ACCESS_TOKEN": "meta-access-token",
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
                                "metadata": {"phone_number_id": "meta-phone-id"},
                                "messages": [
                                    {
                                        "id": "wamid.c2.1",
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
    return "sha256=" + hmac.new(
        META_ENV["META_WHATSAPP_APP_SECRET"].encode(),
        body,
        hashlib.sha256,
    ).hexdigest()


class C2ProviderConfigSeparationTests(unittest.TestCase):
    def tearDown(self) -> None:
        if hasattr(app.state, "durable_w1_session_runtime"):
            delattr(app.state, "durable_w1_session_runtime")

    def test_primary_config_composes_without_meta(self) -> None:
        config = RuntimeConfig.from_environment(BASE_ENV)
        self.assertEqual(
            config.xbos_private_base_url,
            "http://private-xbos.example:10000",
        )
        self.assertEqual(config.xbos_catalog_adapter, "fake")
        self.assertFalse(hasattr(config, "meta"))

    def test_locator_keyring_accepts_both_previous_values_absent(self) -> None:
        ring = LocatorKeyRing.from_environment(BASE_ENV)
        self.assertIsNone(ring.previous_key)
        self.assertIsNone(ring.previous_version)

    def test_health_passes_without_meta(self) -> None:
        with patch.dict(os.environ, BASE_ENV, clear=True):
            response = TestClient(app).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_meta_verify_fails_closed_when_meta_config_absent(self) -> None:
        with patch.dict(os.environ, BASE_ENV, clear=True), patch(
            "xbos_customer_channel.persistence.postgres.database.psycopg.connect",
            side_effect=AssertionError("meta absent must not open postgres"),
        ), patch(
            "xbos_customer_channel.adapters.xbos_private_http.PrivateXBOSHTTPClient._post",
            side_effect=AssertionError("meta absent must not call XBOS"),
        ):
            response = TestClient(app).get(
                "/webhooks/meta/whatsapp",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "anything",
                    "hub.challenge": "challenge",
                },
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "provider_configuration_unavailable",
        )

    def test_meta_post_fails_closed_before_payload_trust_when_meta_config_absent(self) -> None:
        with patch.dict(os.environ, BASE_ENV, clear=True), patch(
            "xbos_customer_channel.persistence.postgres.database.psycopg.connect",
            side_effect=AssertionError("meta absent must not open postgres"),
        ), patch(
            "xbos_customer_channel.adapters.xbos_private_http.PrivateXBOSHTTPClient._post",
            side_effect=AssertionError("meta absent must not call XBOS"),
        ):
            response = TestClient(app).post(
                "/webhooks/meta/whatsapp",
                content=b"{not-provider-json",
                headers={"X-Hub-Signature-256": "sha256=" + ("0" * 64)},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "provider_configuration_unavailable",
        )

    def test_meta_configured_verify_behavior_is_preserved(self) -> None:
        env = {**BASE_ENV, **META_ENV}
        with patch.dict(os.environ, env, clear=True):
            client = TestClient(app)
            valid = client.get(
                "/webhooks/meta/whatsapp",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": META_ENV["META_WHATSAPP_VERIFY_TOKEN"],
                    "hub.challenge": "challenge-c2",
                },
            )
            invalid = client.get(
                "/webhooks/meta/whatsapp",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong",
                    "hub.challenge": "challenge-c2",
                },
            )
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.text, "challenge-c2")
        self.assertEqual(invalid.status_code, 403)

    def test_meta_callback_passes_exact_phone_id_to_durable_composition(self) -> None:
        env = {**BASE_ENV, **META_ENV}
        body = callback_payload()
        with patch.dict(os.environ, env, clear=True), patch(
            "xbos_customer_channel.runtime.app.compose_durable_w1_session_runtime",
            return_value=sentinel.runtime,
        ) as compose:
            response = TestClient(app).post(
                "/webhooks/meta/whatsapp",
                content=body,
                headers={"X-Hub-Signature-256": signature(body)},
            )
        self.assertEqual(response.status_code, 200)
        compose.assert_called_once()
        self.assertEqual(
            compose.call_args.kwargs["configured_endpoint_ref"],
            META_ENV["META_WHATSAPP_PHONE_NUMBER_ID"],
        )

    def test_durable_composition_uses_real_xbos_even_when_selector_defaults_fake(self) -> None:
        config = RuntimeConfig.from_environment(BASE_ENV)
        self.assertEqual(config.xbos_catalog_adapter, "fake")
        with patch(
            "xbos_customer_channel.persistence.postgres.database.psycopg.connect",
            side_effect=AssertionError("composition must remain lazy"),
        ), patch(
            "xbos_customer_channel.adapters.xbos_private_http.PrivateXBOSHTTPClient._post",
            side_effect=AssertionError("composition must not call XBOS"),
        ):
            runtime = compose_durable_w1_session_runtime(
                config,
                configured_endpoint_ref="meta-phone-id",
                environ=BASE_ENV,
            )
        self.assertIsInstance(
            runtime.state_port._xbos_context,
            RealXBOSContextAdapter,
        )
        self.assertIsInstance(
            runtime.state_port._xbos_catalog,
            RealXBOSCatalogAdapter,
        )
        self.assertIsInstance(
            runtime.state_port._xbos_context._client,
            PrivateXBOSHTTPClient,
        )
        self.assertEqual(
            runtime.state_port._configured_endpoint_ref,
            "meta-phone-id",
        )


if __name__ == "__main__":
    unittest.main()

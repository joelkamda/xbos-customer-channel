from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.adapters.xbos_private_http import (
    CATALOG_SERVICE_PRINCIPAL,
    CATALOG_SERVICE_SCOPE,
    MENU_PATH,
    PrivateXBOSHTTPClient,
)
from xbos_customer_channel.application.w1_composition import (
    XBOSW1ContractUnavailable,
    compose_real_xbos_boundaries,
)
from xbos_customer_channel.runtime.config import (
    RuntimeConfig,
    XBOS_CATALOG_BEARER_CREDENTIAL_REFERENCE,
    XBOS_CATALOG_READ_TOKEN_ENV,
    XBOS_CATALOG_SERVICE_PRINCIPAL_ENV,
    XBOS_CATALOG_SERVICE_SCOPE_ENV,
    XBOS_PRIVATE_BASE_URL_ENV,
)


NOW = datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc)
TOKEN = "synthetic-catalog-read-token"
BASE_URL = "http://private-xbos.example:10000"


def base_env() -> dict[str, str]:
    return {
        "META_WHATSAPP_GRAPH_API_VERSION": "v99.0",
        "META_WHATSAPP_PHONE_NUMBER_ID": "phone-id",
        "META_WHATSAPP_APP_SECRET": "synthetic-meta-secret",
        "META_WHATSAPP_VERIFY_TOKEN": "synthetic-verify",
        "META_WHATSAPP_ACCESS_TOKEN": "synthetic-access",
        XBOS_PRIVATE_BASE_URL_ENV: BASE_URL,
        "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_ADAPTER": "real",
        XBOS_CATALOG_READ_TOKEN_ENV: TOKEN,
        XBOS_CATALOG_SERVICE_PRINCIPAL_ENV: CATALOG_SERVICE_PRINCIPAL,
        XBOS_CATALOG_SERVICE_SCOPE_ENV: CATALOG_SERVICE_SCOPE,
    }


class LoopBR2R2R3R1CatalogRuntimeAuthTests(unittest.TestCase):
    def test_config_contract_is_exact_and_secret_is_redacted(self):
        config = RuntimeConfig.from_environment(base_env())
        self.assertEqual(config.xbos_catalog_service_principal, CATALOG_SERVICE_PRINCIPAL)
        self.assertEqual(config.xbos_catalog_service_scope, CATALOG_SERVICE_SCOPE)
        self.assertEqual(
            config.xbos_catalog_bearer_credential_reference,
            XBOS_CATALOG_BEARER_CREDENTIAL_REFERENCE,
        )
        self.assertEqual(
            XBOS_CATALOG_BEARER_CREDENTIAL_REFERENCE,
            f"env:{XBOS_CATALOG_READ_TOKEN_ENV}",
        )
        self.assertNotIn(TOKEN, repr(config))

    def test_missing_token_fails_closed_before_network(self):
        env = base_env()
        env.pop(XBOS_CATALOG_READ_TOKEN_ENV)
        config = RuntimeConfig.from_environment(env)
        with self.assertRaisesRegex(
            XBOSW1ContractUnavailable,
            "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_READ_TOKEN_REQUIRED",
        ):
            compose_real_xbos_boundaries(config)

    def test_wrong_principal_fails_closed(self):
        env = base_env()
        env[XBOS_CATALOG_SERVICE_PRINCIPAL_ENV] = "customer-channel"
        with self.assertRaisesRegex(
            ValueError,
            "invalid_customer_channel_xbos_catalog_service_principal",
        ):
            RuntimeConfig.from_environment(env)

    def test_wrong_scope_fails_closed(self):
        env = base_env()
        env[XBOS_CATALOG_SERVICE_SCOPE_ENV] = "restaurant.orders.write"
        with self.assertRaisesRegex(
            ValueError,
            "invalid_customer_channel_xbos_catalog_service_scope",
        ):
            RuntimeConfig.from_environment(env)

    def test_multiple_scopes_fail_closed(self):
        env = base_env()
        env[XBOS_CATALOG_SERVICE_SCOPE_ENV] = (
            "restaurant.menu.read restaurant.orders.write"
        )
        with self.assertRaisesRegex(
            ValueError,
            "invalid_customer_channel_xbos_catalog_service_scope",
        ):
            RuntimeConfig.from_environment(env)

    def test_base_url_is_required(self):
        env = base_env()
        env.pop(XBOS_PRIVATE_BASE_URL_ENV)
        with self.assertRaisesRegex(
            ValueError,
            "missing_customer_channel_configuration",
        ):
            RuntimeConfig.from_environment(env)

    def test_menu_request_emits_exact_frozen_auth_contract(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"tenant_id": 2})

        http = httpx.Client(transport=httpx.MockTransport(handler))
        client = PrivateXBOSHTTPClient(
            base_url=BASE_URL,
            bearer_token=TOKEN,
            service_principal=CATALOG_SERVICE_PRINCIPAL,
            service_scope=CATALOG_SERVICE_SCOPE,
            client=http,
            sleeper=lambda _: None,
        )
        request = SimpleNamespace(
            tenant_id=2,
            catalog_public_id=UUID("00000000-0000-0000-0000-000000000101"),
            effective_at=NOW,
            price_code="STANDARD",
            currency="XAF",
            scope_type="tenant",
            scope_id=None,
        )
        client.menu(
            request,
            binding_ref="binding-1",
            binding_version=1,
            correlation_ref="corr-r2-r2-r3-r1",
        )

        self.assertEqual(len(seen), 1)
        sent = seen[0]
        self.assertEqual(sent.url.path, MENU_PATH)
        self.assertEqual(sent.headers["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(
            sent.headers["x-service-principal"],
            CATALOG_SERVICE_PRINCIPAL,
        )
        self.assertEqual(sent.headers["x-service-scopes"], CATALOG_SERVICE_SCOPE)
        self.assertEqual(len(sent.headers["x-service-scopes"].split()), 1)

    def test_retry_preserves_auth_headers_body_and_effective_at(self):
        seen: list[httpx.Request] = []
        sleeps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(503, json={"error": "temporary"})
            return httpx.Response(200, json={"binding_ref": "binding-1"})

        http = httpx.Client(transport=httpx.MockTransport(handler))
        client = PrivateXBOSHTTPClient(
            base_url=BASE_URL,
            bearer_token=TOKEN,
            service_principal=CATALOG_SERVICE_PRINCIPAL,
            service_scope=CATALOG_SERVICE_SCOPE,
            client=http,
            sleeper=sleeps.append,
        )
        client.resolve_catalog_binding(
            merchant_ref="merchant-1",
            location_ref="location-1",
            effective_at=NOW,
            correlation_ref="corr-r2-r2-r3-r1",
        )
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].headers, seen[1].headers)
        self.assertEqual(seen[0].content, seen[1].content)
        payload = json.loads(seen[0].content.decode("utf-8"))
        self.assertEqual(payload["effective_at"], NOW.isoformat())
        self.assertEqual(sleeps, [0.1])


if __name__ == "__main__":
    unittest.main()

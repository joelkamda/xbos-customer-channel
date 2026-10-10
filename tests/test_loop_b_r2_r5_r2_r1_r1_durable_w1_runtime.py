from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from xbos_customer_channel.persistence.postgres.database import DatabaseConfig
from xbos_customer_channel.persistence.postgres.runtime_state_store import (
    PostgresRuntimeStateStore,
)
from xbos_customer_channel.persistence.postgres.schema import DATABASE_URL_ENV
from xbos_customer_channel.persistence.postgres.security_state_stores import (
    PostgresCustomerSessionStore,
    PostgresIdentityBindingStore,
)
from xbos_customer_channel.persistence.postgres.transport_state_stores import (
    PostgresIdempotencyResultStore,
    PostgresProviderMessageReceiptStore,
)
from xbos_customer_channel.persistence.postgres.w1_runtime_state_port import (
    PostgresW1RuntimeStatePort,
)
from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.runtime.config import RuntimeConfig
from xbos_customer_channel.runtime.durable_w1 import (
    DurableW1SessionRuntime,
    compose_durable_w1_session_runtime,
)


ENV = {
    "META_WHATSAPP_GRAPH_API_VERSION": "v99.0",
    "META_WHATSAPP_PHONE_NUMBER_ID": "phone-id",
    "META_WHATSAPP_APP_SECRET": "runtime-app-secret",
    "META_WHATSAPP_VERIFY_TOKEN": "runtime-verify-token",
    "META_WHATSAPP_ACCESS_TOKEN": "runtime-access-token",
    "XAFPAY_CUSTOMER_CHANNEL_XBOS_PRIVATE_BASE_URL": "http://private-xbos.example:10000",
    "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_READ_TOKEN": "catalog-read-token",
    "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_SERVICE_PRINCIPAL": "customer-channel-catalog-reader",
    "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_SERVICE_SCOPE": "restaurant.menu.read",
    "XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_KEY_CURRENT": "locator-key-current",
    "XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_VERSION_CURRENT": "v1",
    DATABASE_URL_ENV: "postgresql://runtime:secret@private-db/customer_channel",
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
                                        "id": "wamid.r1r1.1",
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


class DurableW1RuntimeMaterializationTests(unittest.TestCase):
    def tearDown(self) -> None:
        if hasattr(app.state, "durable_w1_session_runtime"):
            delattr(app.state, "durable_w1_session_runtime")

    def test_canonical_database_key_is_the_only_database_config_input(self) -> None:
        config = DatabaseConfig.from_environment(
            {DATABASE_URL_ENV: ENV[DATABASE_URL_ENV]}
        )
        self.assertEqual(config.database_url, ENV[DATABASE_URL_ENV] + "?sslmode=require")

        for alias in ("DATABASE_URL", "POSTGRES_URL"):
            with self.subTest(alias=alias):
                with self.assertRaisesRegex(
                    ValueError,
                    "XAFPAY_CUSTOMER_CHANNEL_DATABASE_URL",
                ):
                    DatabaseConfig.from_environment({alias: ENV[DATABASE_URL_ENV]})

    def test_composition_materializes_existing_postgres_stores_and_state_port(self) -> None:
        runtime_config = RuntimeConfig.from_environment(ENV)

        with patch(
            "xbos_customer_channel.persistence.postgres.database.psycopg.connect",
            side_effect=AssertionError("composition must not open postgres"),
        ):
            runtime = compose_durable_w1_session_runtime(
                runtime_config,
                configured_endpoint_ref=ENV["META_WHATSAPP_PHONE_NUMBER_ID"],
                environ=ENV,
            )

        self.assertIsInstance(runtime, DurableW1SessionRuntime)
        self.assertIsInstance(runtime.runtime_store, PostgresRuntimeStateStore)
        self.assertIsInstance(runtime.session_store, PostgresCustomerSessionStore)
        self.assertIsInstance(runtime.identity_binding_store, PostgresIdentityBindingStore)
        self.assertIsInstance(runtime.receipt_store, PostgresProviderMessageReceiptStore)
        self.assertIsInstance(runtime.idempotency_store, PostgresIdempotencyResultStore)
        self.assertIsInstance(runtime.state_port, PostgresW1RuntimeStatePort)

    def test_same_canonical_database_config_feeds_all_durable_stores(self) -> None:
        runtime = compose_durable_w1_session_runtime(
            RuntimeConfig.from_environment(ENV),
            configured_endpoint_ref=ENV["META_WHATSAPP_PHONE_NUMBER_ID"],
            environ=ENV,
        )

        factories = {
            id(runtime.runtime_store._connection_factory),
            id(runtime.session_store._connection_factory),
            id(runtime.identity_binding_store._connection_factory),
            id(runtime.receipt_store._connection_factory),
            id(runtime.idempotency_store._connection_factory),
        }
        self.assertEqual(len(factories), 1)
        self.assertEqual(
            runtime.database_config.database_url,
            ENV[DATABASE_URL_ENV] + "?sslmode=require",
        )

    def test_fastapi_callback_materializes_and_retains_durable_state_port(self) -> None:
        body = callback_payload()
        with patch.dict(os.environ, ENV, clear=True), patch(
            "xbos_customer_channel.persistence.postgres.database.psycopg.connect",
            side_effect=AssertionError("callback composition must not open postgres"),
        ), patch(
            "xbos_customer_channel.adapters.xbos_private_http.PrivateXBOSHTTPClient._post",
            side_effect=AssertionError("callback composition must not call XBOS"),
        ):
            response = TestClient(app).post(
                "/webhooks/meta/whatsapp",
                content=body,
                headers={"X-Hub-Signature-256": signature(body)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["w1_dispatch"], "disabled")
        runtime = app.state.durable_w1_session_runtime
        self.assertIsInstance(runtime, DurableW1SessionRuntime)
        self.assertIsInstance(runtime.state_port, PostgresW1RuntimeStatePort)

    def test_absent_canonical_database_key_does_not_materialize_runtime(self) -> None:
        env = dict(ENV)
        env.pop(DATABASE_URL_ENV)
        env["DATABASE_URL"] = ENV[DATABASE_URL_ENV]
        env["POSTGRES_URL"] = ENV[DATABASE_URL_ENV]
        body = callback_payload()

        with patch.dict(os.environ, env, clear=True):
            response = TestClient(app).post(
                "/webhooks/meta/whatsapp",
                content=body,
                headers={"X-Hub-Signature-256": signature(body)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(hasattr(app.state, "durable_w1_session_runtime"))

    def test_source_composition_has_no_migration_session_bootstrap_or_business_call(self) -> None:
        from xbos_customer_channel.runtime import durable_w1

        source = Path(durable_w1.__file__).read_text(encoding="utf-8")
        forbidden = (
            "alembic",
            "create_all",
            ".insert(",
            ".put(",
            ".rotate_if_active(",
            ".get_catalog_bound(",
            ".resolve(",
            "submit_order(",
            "payment_options(",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, source)


if __name__ == "__main__":
    unittest.main()

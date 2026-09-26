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
    BINDING_PATH,
    CONTEXT_PATH,
    MENU_PATH,
    PrivateXBOSHTTPClient,
    PrivateXBOSHTTPError,
)


NOW = datetime(2026, 9, 26, 12, 30, tzinfo=timezone.utc)
BASE_URL = "http://private-xbos.example:10000"
TOKEN = "synthetic-read-token"
CORRELATION = "corr-loop-b-r2-r1"


def body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


class LoopBR2R1PrivateXBOSHTTPTests(unittest.TestCase):
    def client(self, handler, sleeps=None):
        recorder = [] if sleeps is None else sleeps
        http = httpx.Client(transport=httpx.MockTransport(handler))
        return PrivateXBOSHTTPClient(
            base_url=BASE_URL,
            bearer_token=TOKEN,
            client=http,
            sleeper=recorder.append,
        )

    def test_context_request_path_headers_and_explicit_binding_are_exact(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "tenant_ref": "2",
                    "merchant_ref": "merchant-1",
                    "location_ref": "location-1",
                    "table_ref": None,
                    "dining_area_ref": None,
                    "purpose": "takeaway",
                    "context_binding_ref": "ctx-1",
                    "projection": {},
                },
            )

        client = self.client(handler)
        client.attest_context(
            context_binding_ref="ctx-1",
            merchant_ref="merchant-1",
            location_ref="location-1",
            table_ref=None,
            dining_area_ref=None,
            purpose="takeaway",
            effective_at=NOW,
            correlation_ref=CORRELATION,
        )
        self.assertEqual(seen[0].url.path, CONTEXT_PATH)
        self.assertEqual(seen[0].headers["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(seen[0].headers["x-correlation-ref"], CORRELATION)
        payload = body(seen[0])
        self.assertEqual(payload["context_binding_ref"], "ctx-1")
        self.assertEqual(payload["effective_at"], NOW.isoformat())

    def test_binding_retry_reuses_identical_effective_at_and_body(self):
        seen = []
        sleeps = []

        def handler(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(503, json={"error": "temporary"})
            return httpx.Response(
                200,
                json={
                    "binding_ref": "bind-1",
                    "binding_version": 4,
                    "tenant_id": 2,
                    "catalog_public_id": "00000000-0000-0000-0000-000000000101",
                    "price_code": "retail",
                    "currency": "XAF",
                    "scope_type": "location",
                    "scope_id": 9,
                },
            )

        client = self.client(handler, sleeps)
        client.resolve_catalog_binding(
            merchant_ref="merchant-1",
            location_ref="location-1",
            effective_at=NOW,
            correlation_ref=CORRELATION,
        )
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].url.path, BINDING_PATH)
        self.assertEqual(seen[1].url.path, BINDING_PATH)
        self.assertEqual(body(seen[0]), body(seen[1]))
        self.assertEqual(body(seen[0])["effective_at"], NOW.isoformat())
        self.assertEqual(sleeps, [0.1])

    def test_menu_request_forwards_exact_seven_inputs_plus_binding_metadata(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"tenant_id": 2})

        client = self.client(handler)
        request = SimpleNamespace(
            tenant_id=2,
            catalog_public_id=UUID("00000000-0000-0000-0000-000000000101"),
            effective_at=NOW,
            price_code="retail",
            currency="XAF",
            scope_type="location",
            scope_id=9,
        )
        client.menu(
            request,
            binding_ref="bind-1",
            binding_version=4,
            correlation_ref=CORRELATION,
        )
        self.assertEqual(seen[0].url.path, MENU_PATH)
        payload = body(seen[0])
        self.assertEqual(
            set(payload),
            {
                "schema",
                "binding_ref",
                "binding_version",
                "tenant_id",
                "catalog_public_id",
                "effective_at",
                "price_code",
                "currency",
                "scope_type",
                "scope_id",
            },
        )
        self.assertEqual(payload["binding_ref"], "bind-1")
        self.assertEqual(payload["binding_version"], 4)
        self.assertEqual(payload["effective_at"], NOW.isoformat())

    def test_auth_failure_is_not_retried(self):
        calls = []
        sleeps = []

        def handler(request):
            calls.append(request)
            return httpx.Response(401, json={"error": "auth"})

        client = self.client(handler, sleeps)
        with self.assertRaisesRegex(PrivateXBOSHTTPError, "auth_failure"):
            client.resolve_catalog_binding(
                merchant_ref="merchant-1",
                location_ref="location-1",
                effective_at=NOW,
                correlation_ref=CORRELATION,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_semantic_conflict_is_not_retried(self):
        calls = []
        sleeps = []

        def handler(request):
            calls.append(request)
            return httpx.Response(409, json={"error": "binding_conflict"})

        client = self.client(handler, sleeps)
        with self.assertRaisesRegex(PrivateXBOSHTTPError, "409"):
            client.resolve_catalog_binding(
                merchant_ref="merchant-1",
                location_ref="location-1",
                effective_at=NOW,
                correlation_ref=CORRELATION,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_missing_binding_404_fails_closed_without_retry(self):
        calls = []
        sleeps = []

        def handler(request):
            calls.append(request)
            return httpx.Response(404, json={"error": "binding_not_found"})

        client = self.client(handler, sleeps)
        with self.assertRaisesRegex(PrivateXBOSHTTPError, "404"):
            client.resolve_catalog_binding(
                merchant_ref="merchant-1",
                location_ref="location-1",
                effective_at=NOW,
                correlation_ref=CORRELATION,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_binding_version_conflict_409_fails_closed_without_retry(self):
        calls = []
        sleeps = []

        def handler(request):
            calls.append(request)
            return httpx.Response(409, json={"error": "binding_version_mismatch"})

        client = self.client(handler, sleeps)
        request = SimpleNamespace(
            tenant_id=2,
            catalog_public_id=UUID("00000000-0000-0000-0000-000000000101"),
            effective_at=NOW,
            price_code="retail",
            currency="XAF",
            scope_type="location",
            scope_id=9,
        )
        with self.assertRaisesRegex(PrivateXBOSHTTPError, "409"):
            client.menu(
                request,
                binding_ref="bind-1",
                binding_version=4,
                correlation_ref=CORRELATION,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_timeout_retries_at_most_once(self):
        calls = []
        sleeps = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("timeout", request=request)

        client = self.client(handler, sleeps)
        with self.assertRaisesRegex(
            PrivateXBOSHTTPError,
            "xbos_private_transport_timeout",
        ):
            client.resolve_catalog_binding(
                merchant_ref="merchant-1",
                location_ref="location-1",
                effective_at=NOW,
                correlation_ref=CORRELATION,
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.1])

    def test_missing_correlation_fails_before_transport(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={})

        client = self.client(handler)
        with self.assertRaisesRegex(PrivateXBOSHTTPError, "correlation_ref_required"):
            client.resolve_catalog_binding(
                merchant_ref="merchant-1",
                location_ref="location-1",
                effective_at=NOW,
                correlation_ref="",
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

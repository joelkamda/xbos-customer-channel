from __future__ import annotations

import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.adapters.real_xbos_catalog import (
    PrivateXBOSCatalogBindingResolver,
    PrivateXBOSMenuReadClient,
    RealXBOSCatalogAdapter,
    RealXBOSCatalogUnavailable,
)
from xbos_customer_channel.adapters.xbos_private_http import (
    BINDING_PATH,
    CATALOG_SERVICE_PRINCIPAL,
    CATALOG_SERVICE_SCOPE,
    MAX_RETRY_COUNT,
    MENU_PATH,
    PrivateXBOSHTTPClient,
    PrivateXBOSHTTPError,
)
from xbos_customer_channel.session_state import CustomerSessionSnapshot


NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
BASE_URL = "http://private-xbos.example:10000"
TOKEN = "synthetic-r2-r5-r1-r1-r1-token"
CONTEXT = "00000000-0000-0000-0000-000000000001"
MERCHANT = "00000000-0000-0000-0000-000000000002"
LOCATION = "00000000-0000-0000-0000-000000000003"
BINDING = "00000000-0000-0000-0000-000000000004"
CATALOG = UUID("00000000-0000-0000-0000-000000000005")
CORR = "corr-r2-r5-r1-r1-r1"


def body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8"))


def binding_response() -> dict:
    return {
        "binding_ref": BINDING,
        "binding_version": 1,
        "tenant_id": 2,
        "catalog_public_id": str(CATALOG),
        "price_code": "STANDARD",
        "currency": "XAF",
        "scope_type": "tenant",
        "scope_id": None,
    }


def menu_response() -> dict:
    return {
        "tenant_id": 2,
        "catalog_reference": {
            "public_id": str(CATALOG),
            "code": "WND-MENU",
            "name": "Wine & Dine Menu",
        },
        "effective_at": NOW.isoformat(),
        "pricing_context": {
            "price_code": "STANDARD",
            "currency": "XAF",
            "scope_type": "tenant",
            "scope_id": None,
        },
        "sections": [],
        "creates_financial_truth": False,
    }


def client(handler, *, sleeps=None) -> PrivateXBOSHTTPClient:
    return PrivateXBOSHTTPClient(
        base_url=BASE_URL,
        bearer_token=TOKEN,
        service_principal=CATALOG_SERVICE_PRINCIPAL,
        service_scope=CATALOG_SERVICE_SCOPE,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=(lambda _: None) if sleeps is None else sleeps.append,
    )


def resolve(c: PrivateXBOSHTTPClient):
    return c.resolve_catalog_binding(
        context_binding_ref=CONTEXT,
        merchant_ref=MERCHANT,
        location_ref=LOCATION,
        effective_at=NOW,
        correlation_ref=CORR,
    )


def menu(c: PrivateXBOSHTTPClient):
    request = SimpleNamespace(effective_at=NOW)
    return c.menu(
        request,
        context_binding_ref=CONTEXT,
        merchant_ref=MERCHANT,
        location_ref=LOCATION,
        binding_ref=BINDING,
        binding_version=1,
        correlation_ref=CORR,
    )


def adapter(c: PrivateXBOSHTTPClient) -> RealXBOSCatalogAdapter:
    return RealXBOSCatalogAdapter(
        client=PrivateXBOSMenuReadClient(c),
        binding_resolver=PrivateXBOSCatalogBindingResolver(c),
        effective_at_factory=lambda: NOW,
    )


def logical_read(c: PrivateXBOSHTTPClient):
    return adapter(c).get_catalog_bound(
        context_binding_ref=CONTEXT,
        merchant_ref=MERCHANT,
        location_ref=LOCATION,
        effective_at=NOW,
        correlation_ref=CORR,
    )


def assert_auth(request: httpx.Request):
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["x-service-principal"] == CATALOG_SERVICE_PRINCIPAL
    assert request.headers["x-service-scopes"] == CATALOG_SERVICE_SCOPE
    assert request.headers["x-correlation-ref"] == CORR


def identity(request: httpx.Request):
    return (
        request.method,
        request.url.path,
        request.content,
        request.headers["authorization"],
        request.headers["x-service-principal"],
        request.headers["x-service-scopes"],
        request.headers["x-correlation-ref"],
    )


def test_resolver_exact_four_field_contract_and_first_attempt_success():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=binding_response())

    result = resolve(client(handler))
    assert len(seen) == 1
    assert seen[0].url.path == BINDING_PATH
    assert set(body(seen[0])) == {
        "context_binding_ref",
        "merchant_public_id",
        "location_public_id",
        "effective_at",
    }
    assert len(body(seen[0])) == 4
    assert body(seen[0]) == {
        "context_binding_ref": CONTEXT,
        "merchant_public_id": MERCHANT,
        "location_public_id": LOCATION,
        "effective_at": NOW.isoformat(),
    }
    assert "schema" not in body(seen[0])
    assert "merchant_ref" not in body(seen[0])
    assert "location_ref" not in body(seen[0])
    assert result["binding_ref"] == BINDING
    assert result["binding_version"] == 1
    assert_auth(seen[0])


@pytest.mark.parametrize("mode", ["503", "timeout"])
def test_resolver_retry_is_identical_and_bounded(mode):
    seen = []
    sleeps = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            if mode == "503":
                return httpx.Response(503, json={"error": "temporary"})
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json=binding_response())

    result = resolve(client(handler, sleeps=sleeps))
    assert result["binding_ref"] == BINDING
    assert len(seen) == 2
    assert identity(seen[0]) == identity(seen[1])
    assert body(seen[0])["effective_at"] == NOW.isoformat()
    assert sleeps == [0.1]


def test_resolver_two_failures_stop_before_menu():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(503, json={"error": "still-temporary"})

    with pytest.raises(RealXBOSCatalogUnavailable, match="catalog_binding_failed"):
        logical_read(client(handler))
    assert [r.url.path for r in seen] == [BINDING_PATH, BINDING_PATH]
    assert len(seen) == MAX_RETRY_COUNT + 1


def test_menu_exact_six_field_contract_and_first_attempt_success():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=menu_response())

    result = menu(client(handler))
    assert result["tenant_id"] == 2
    assert len(seen) == 1
    assert seen[0].url.path == MENU_PATH
    payload = body(seen[0])
    assert set(payload) == {
        "context_binding_ref",
        "merchant_public_id",
        "location_public_id",
        "binding_ref",
        "binding_version",
        "effective_at",
    }
    assert len(payload) == 6
    assert payload == {
        "context_binding_ref": CONTEXT,
        "merchant_public_id": MERCHANT,
        "location_public_id": LOCATION,
        "binding_ref": BINDING,
        "binding_version": 1,
        "effective_at": NOW.isoformat(),
    }
    for forbidden in (
        "schema",
        "tenant_id",
        "catalog_public_id",
        "price_code",
        "currency",
        "scope_type",
        "scope_id",
    ):
        assert forbidden not in payload
    assert_auth(seen[0])


@pytest.mark.parametrize("mode", ["503", "timeout"])
def test_menu_retry_is_identical_and_bounded(mode):
    seen = []
    sleeps = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            if mode == "503":
                return httpx.Response(503, json={"error": "temporary"})
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json=menu_response())

    result = menu(client(handler, sleeps=sleeps))
    assert result["tenant_id"] == 2
    assert len(seen) == 2
    assert identity(seen[0]) == identity(seen[1])
    assert sleeps == [0.1]


def test_menu_two_failures_has_no_third_attempt():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(503, json={"error": "still-temporary"})

    with pytest.raises(PrivateXBOSHTTPError, match="xbos_private_http_503"):
        menu(client(handler))
    assert [r.url.path for r in seen] == [MENU_PATH, MENU_PATH]
    assert len(seen) == MAX_RETRY_COUNT + 1


def test_full_two_step_read_no_retry_is_two_actual_attempts_and_preserves_standard():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == BINDING_PATH:
            return httpx.Response(200, json=binding_response())
        if request.url.path == MENU_PATH:
            return httpx.Response(200, json=menu_response())
        raise AssertionError(request.url.path)

    projection = logical_read(client(handler))
    assert [r.url.path for r in seen] == [BINDING_PATH, MENU_PATH]
    assert len(seen) == 2
    resolver_body = body(seen[0])
    menu_body = body(seen[1])
    assert resolver_body["effective_at"] == menu_body["effective_at"] == NOW.isoformat()
    assert menu_body["binding_ref"] == binding_response()["binding_ref"]
    assert menu_body["binding_version"] == binding_response()["binding_version"]
    assert menu_body["context_binding_ref"] == resolver_body["context_binding_ref"]
    assert menu_body["merchant_public_id"] == resolver_body["merchant_public_id"]
    assert menu_body["location_public_id"] == resolver_body["location_public_id"]
    assert projection.catalog_ref == str(CATALOG)
    assert projection.currency == "XAF"
    source = inspect.getsource(RealXBOSCatalogAdapter._request_for_binding)
    assert "binding.price_code.strip()" in source
    assert "binding.price_code.strip().lower()" not in source


def test_full_two_step_read_both_steps_retried_is_four_actual_attempts():
    seen = []
    counts = {BINDING_PATH: 0, MENU_PATH: 0}

    def handler(request):
        seen.append(request)
        path = request.url.path
        counts[path] += 1
        if counts[path] == 1:
            return httpx.Response(503, json={"error": "temporary"})
        if path == BINDING_PATH:
            return httpx.Response(200, json=binding_response())
        if path == MENU_PATH:
            return httpx.Response(200, json=menu_response())
        raise AssertionError(path)

    projection = logical_read(client(handler))
    assert projection.catalog_ref == str(CATALOG)
    assert [r.url.path for r in seen] == [
        BINDING_PATH,
        BINDING_PATH,
        MENU_PATH,
        MENU_PATH,
    ]
    assert len(seen) == 4
    assert identity(seen[0]) == identity(seen[1])
    assert identity(seen[2]) == identity(seen[3])


def test_menu_failure_does_not_outer_retry_resolver_or_create_second_logical_read():
    seen = []
    counts = {BINDING_PATH: 0, MENU_PATH: 0}

    def handler(request):
        seen.append(request)
        path = request.url.path
        counts[path] += 1
        if path == BINDING_PATH:
            return httpx.Response(200, json=binding_response())
        if path == MENU_PATH:
            return httpx.Response(503, json={"error": "temporary"})
        raise AssertionError(path)

    with pytest.raises(RealXBOSCatalogUnavailable, match="catalog_read_failed"):
        logical_read(client(handler))
    assert counts[BINDING_PATH] == 1
    assert counts[MENU_PATH] == 2
    assert len(seen) == 3


def test_price_code_case_preserved_and_no_new_persisted_binding_state():
    from xbos_customer_channel.adapters.real_xbos_catalog import XBOSMenuReadBinding

    binding = XBOSMenuReadBinding(
        merchant_ref=MERCHANT,
        location_ref=LOCATION,
        tenant_id=2,
        catalog_public_id=CATALOG,
        price_code="STANDARD",
        currency="XAF",
        scope_type="tenant",
        scope_id=None,
        binding_ref=BINDING,
        binding_version=1,
    )
    request = RealXBOSCatalogAdapter._request_for_binding(
        binding,
        merchant_ref=MERCHANT,
        location_ref=LOCATION,
        effective_at=NOW,
    )
    assert request.price_code == "STANDARD"
    assert "binding_ref" not in CustomerSessionSnapshot.__dataclass_fields__
    assert "binding_version" not in CustomerSessionSnapshot.__dataclass_fields__
    source = inspect.getsource(RealXBOSCatalogAdapter)
    assert "binding.price_code.strip().lower()" not in source


def test_retry_policy_and_auth_contract_are_unchanged():
    assert MAX_RETRY_COUNT == 1
    assert CATALOG_SERVICE_PRINCIPAL == "customer-channel-catalog-reader"
    assert CATALOG_SERVICE_SCOPE == "restaurant.menu.read"

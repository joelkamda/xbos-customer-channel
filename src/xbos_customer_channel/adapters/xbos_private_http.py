from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import httpx


CONTEXT_PATH = "/internal/customer-channel/v1/context-attestations"
BINDING_PATH = "/internal/customer-channel/v1/catalog-bindings/resolve"
MENU_PATH = "/internal/customer-channel/v1/catalog/menu"
CONNECT_TIMEOUT_SECONDS = 1.0
READ_TIMEOUT_SECONDS = 3.0
MAX_RETRY_COUNT = 1
RETRY_BACKOFF_SECONDS = 0.1
_RETRYABLE_STATUS = frozenset({502, 503, 504})


class PrivateXBOSHTTPError(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.reason = reason
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(reason)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("xbos_effective_at_must_be_aware")
    return value.astimezone(timezone.utc)


class PrivateXBOSHTTPClient:
    """Narrow Customer Channel client for the accepted XBOS private seam."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        selected_url = base_url.strip().rstrip("/")
        selected_token = bearer_token.strip()
        if not selected_url.startswith(("http://", "https://")):
            raise ValueError("invalid_xbos_private_base_url")
        if not selected_token:
            raise ValueError("xbos_catalog_read_token_required")
        self._base_url = selected_url
        self._bearer_token = selected_token
        self._client = client or httpx.Client()
        self._sleeper = sleeper
        self._timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_SECONDS,
            read=READ_TIMEOUT_SECONDS,
            write=READ_TIMEOUT_SECONDS,
            pool=CONNECT_TIMEOUT_SECONDS,
        )

    def attest_context(
        self,
        *,
        context_binding_ref: str,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        dining_area_ref: str | None,
        purpose: str,
        effective_at: datetime,
        correlation_ref: str,
    ) -> dict[str, Any]:
        if not context_binding_ref.strip():
            raise PrivateXBOSHTTPError("context_binding_ref_required")
        payload = {
            "schema": "xafpay.customer-channel.xbos-context-attestation.v1",
            "context_binding_ref": context_binding_ref,
            "merchant_ref": merchant_ref,
            "location_ref": location_ref,
            "table_ref": table_ref,
            "dining_area_ref": dining_area_ref,
            "purpose": purpose,
            "effective_at": _aware_utc(effective_at).isoformat(),
        }
        return self._post(CONTEXT_PATH, payload, correlation_ref=correlation_ref)

    def resolve_catalog_binding(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        effective_at: datetime,
        correlation_ref: str,
    ) -> dict[str, Any]:
        payload = {
            "schema": "xafpay.customer-channel.xbos-catalog-binding-read.v1",
            "merchant_ref": merchant_ref,
            "location_ref": location_ref,
            "effective_at": _aware_utc(effective_at).isoformat(),
        }
        return self._post(BINDING_PATH, payload, correlation_ref=correlation_ref)

    def menu(
        self,
        request: Any,
        *,
        binding_ref: str,
        binding_version: int,
        correlation_ref: str,
    ) -> dict[str, Any]:
        if not binding_ref.strip() or binding_version <= 0:
            raise PrivateXBOSHTTPError("catalog_binding_reference_invalid")
        payload = {
            "schema": "xafpay.customer-channel.xbos-menu-read.v1",
            "binding_ref": binding_ref,
            "binding_version": binding_version,
            "tenant_id": request.tenant_id,
            "catalog_public_id": str(request.catalog_public_id),
            "effective_at": _aware_utc(request.effective_at).isoformat(),
            "price_code": request.price_code,
            "currency": request.currency,
            "scope_type": request.scope_type,
            "scope_id": request.scope_id,
        }
        return self._post(MENU_PATH, payload, correlation_ref=correlation_ref)

    def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        correlation_ref: str,
    ) -> dict[str, Any]:
        trace = correlation_ref.strip()
        if not trace:
            raise PrivateXBOSHTTPError("correlation_ref_required")
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "X-Correlation-Ref": trace,
            "Content-Type": "application/json",
        }
        body = dict(payload)
        attempts = MAX_RETRY_COUNT + 1
        for attempt in range(attempts):
            try:
                response = self._client.post(
                    self._base_url + path,
                    headers=headers,
                    json=body,
                    timeout=self._timeout,
                )
            except (httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                if attempt < MAX_RETRY_COUNT:
                    self._sleeper(RETRY_BACKOFF_SECONDS)
                    continue
                raise PrivateXBOSHTTPError(
                    "xbos_private_transport_timeout",
                    retryable=True,
                ) from exc
            except (httpx.ConnectError, httpx.ReadError, httpx.NetworkError) as exc:
                if attempt < MAX_RETRY_COUNT:
                    self._sleeper(RETRY_BACKOFF_SECONDS)
                    continue
                raise PrivateXBOSHTTPError(
                    "xbos_private_transport_unavailable",
                    retryable=True,
                ) from exc

            if response.status_code in _RETRYABLE_STATUS:
                if attempt < MAX_RETRY_COUNT:
                    self._sleeper(RETRY_BACKOFF_SECONDS)
                    continue
                raise PrivateXBOSHTTPError(
                    f"xbos_private_http_{response.status_code}",
                    status_code=response.status_code,
                    retryable=True,
                )

            if response.status_code >= 400:
                reason = (
                    "auth_failure"
                    if response.status_code in {401, 403}
                    else f"xbos_private_http_{response.status_code}"
                )
                raise PrivateXBOSHTTPError(
                    reason,
                    status_code=response.status_code,
                    retryable=False,
                )

            try:
                result = response.json()
            except ValueError as exc:
                raise PrivateXBOSHTTPError("malformed_response") from exc
            if not isinstance(result, Mapping):
                raise PrivateXBOSHTTPError("malformed_response")
            return dict(result)

        raise AssertionError("unreachable")

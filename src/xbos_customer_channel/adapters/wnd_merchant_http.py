"""P0D R1 candidate WND private checkout transport.

Deliberately separate from catalog-read-only credentials and Gateway payment
credentials. Must be explicitly enabled by the caller after external owner,
merchant, service-identity, context and staging admission. Default OFF.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID

import httpx

from ..order import OrderSubmissionConflict, OrderTransportUnknown

_PREFIX = "/internal/customer-channel/v1/"
_ALLOWED_ACTION_PATHS = frozenset({
    "/internal/customer-channel/v1/order-confirmations",
    "/internal/customer-channel/v1/orders",
    "/internal/customer-channel/v1/order-drafts",
})
_TIMEOUT = httpx.Timeout(connect=2.0, read=4.0, write=4.0, pool=2.0)


class WNDMerchantAdmissionRequired(PermissionError):
    """Feature/merchant/service-context admission has not been proven."""


@dataclass(frozen=True, slots=True)
class WNDMerchantBinding:
    """Pre-qualified source-owned values, not a substitute for actual WND auth."""

    tenant_id: int
    merchant_public_id: str
    location_public_id: str
    location_id: int
    context_binding_public_id: str
    service_identity_public_id: str
    correlation_ref: str
    currency: str = "XAF"

    def __post_init__(self) -> None:
        if self.tenant_id <= 0 or self.location_id <= 0 or self.currency != "XAF":
            raise WNDMerchantAdmissionRequired("invalid_merchant_tenant_currency")
        for name in (
            "merchant_public_id", "location_public_id",
            "context_binding_public_id", "service_identity_public_id",
        ):
            try:
                parsed = UUID(str(getattr(self, name)))
            except (ValueError, AttributeError, TypeError) as exc:
                raise WNDMerchantAdmissionRequired("source_owned_uuid_required") from exc
            if parsed.int == 0:
                raise WNDMerchantAdmissionRequired("source_owned_nonzero_uuid_required")
        if not self.correlation_ref or len(self.correlation_ref) > 128:
            raise WNDMerchantAdmissionRequired("correlation_ref_invalid")


class WNDMerchantPrivateTransport:
    """Feature-gated narrow private HTTP client, no default network client.

    An injected httpx.Client is necessary. Staging may supply an appropriately
    configured secure client only after the caller independently verifies the
    existing service identity and credential permissions. No env reads here.
    """

    def __init__(
        self, *, enabled: bool = False,
        base_url: str,
        expected_host: str,
        bearer_token: str,
        credential_ref: str,
        binding: WNDMerchantBinding,
        client: httpx.Client | None = None,
    ) -> None:
        if not enabled:
            raise WNDMerchantAdmissionRequired("wnd_merchant_feature_disabled")
        if client is None:
            raise WNDMerchantAdmissionRequired("explicit_authenticated_client_required")
        url = urlsplit(base_url.rstrip("/"))
        if (
            url.scheme != "https" or not url.hostname
            or url.hostname != expected_host
            or url.username is not None or url.password is not None
            or url.query or url.fragment or url.path not in ("", "/")
        ):
            raise WNDMerchantAdmissionRequired("private_https_hostname_required")
        if url.port not in (None, 443):
            raise WNDMerchantAdmissionRequired("private_https_port_restricted")
        if not credential_ref.startswith("wnd-channel-service:"):
            raise WNDMerchantAdmissionRequired("distinct_wnd_service_credential_ref_required")
        if not bearer_token or not bearer_token.strip() or "\r" in bearer_token or "\n" in bearer_token:
            raise WNDMerchantAdmissionRequired("wnd_service_bearer_required")
        if len(bearer_token) > 4096:
            raise WNDMerchantAdmissionRequired("wnd_service_bearer_invalid")
        self._base_url = base_url.rstrip("/")
        self._bearer = bearer_token
        self._client = client
        self.binding = binding
        self.credential_ref = credential_ref  # symbol, NOT secret
        self.is_approved_private = True
        self.is_in_memory = False

    def request(
        self, *, method: str, path: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        # Exact private subtree; never allow absolute URLs, redirects or
        # caller-controlled payment endpoints or addresses.
        if (
            method not in ("POST", "GET") or not path.startswith(_PREFIX)
            or path.startswith("//") or "?" in path or "#" in path
            or "\\" in path or ".." in path
            or any(ord(ch) < 32 for ch in path)
        ):
            raise WNDMerchantAdmissionRequired("wnd_private_route_invalid")
        draft_prefix = _PREFIX + "order-drafts/"
        reconcile_prefix = _PREFIX + "orders/by-client-submit-ref/"
        is_draft_action = False
        is_reconcile = False
        if path.startswith(draft_prefix):
            segments = path[len(draft_prefix):].split("/")
            if len(segments) == 2 and segments[1] in ("commands", "confirmation", "submit"):
                try:
                    UUID(segments[0])
                except (ValueError, TypeError) as exc:
                    raise WNDMerchantAdmissionRequired("wnd_draft_uuid_required") from exc
                is_draft_action = method == "POST" and "%" not in path
        if path.startswith(reconcile_prefix):
            encoded = path[len(reconcile_prefix):]
            decoded = unquote(encoded)
            is_reconcile = (
                method == "GET" and bool(decoded)
                and "/" not in decoded and "\\" not in decoded
                and len(decoded) <= 256 and quote(decoded, safe="") == encoded
            )
        if not (
            (method == "POST" and path in _ALLOWED_ACTION_PATHS)
            or is_draft_action or is_reconcile
        ):
            raise WNDMerchantAdmissionRequired("wnd_private_route_not_admitted")
        if (
            headers.get("X-XBOS-Context-Binding-Ref") != self.binding.context_binding_public_id
            or headers.get("X-Correlation-Ref") != self.binding.correlation_ref
        ):
            raise WNDMerchantAdmissionRequired("wnd_context_correlation_binding_mismatch")
        # WND bearer always comes from this separate credential boundary.
        # Never take Authorization from Channel command, inbound phone, or
        # Gateway payment authorization.
        safe_headers = {
            "Authorization": "Bearer " + self._bearer,
            "X-XBOS-Context-Binding-Ref": self.binding.context_binding_public_id,
            "X-Correlation-Ref": self.binding.correlation_ref,
            "Content-Type": "application/json",
        }
        try:
            response = self._client.request(
                method, self._base_url + path,
                headers=safe_headers,
                json=dict(json_body) if json_body is not None else None,
                timeout=_TIMEOUT,
                follow_redirects=False,
            )
        except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            # POST may already have effected the order; never retry blindly.
            raise OrderTransportUnknown("wnd_private_outcome_unknown") from exc
        if response.status_code in (401, 403):
            raise WNDMerchantAdmissionRequired("wnd_service_authorization_rejected")
        if response.status_code in (408, 429, 500, 502, 503, 504):
            raise OrderTransportUnknown("wnd_private_result_unavailable")
        if response.status_code >= 400:
            raise OrderSubmissionConflict("wnd_private_command_rejected")
        try:
            result = response.json()
        except ValueError as exc:
            raise WNDMerchantAdmissionRequired("wnd_malformed_response") from exc
        if not isinstance(result, Mapping):
            raise WNDMerchantAdmissionRequired("wnd_untrusted_response")
        return result

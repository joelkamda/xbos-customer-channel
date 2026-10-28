"""P0C R1: *inert*, test-injected WND checkout authority order-port bridge.

NO live runtime import/registration. This file never creates an HTTP client,
reads environment secrets, opens a database or mints an order UUID. Only the
caller-supplied in-memory transport can dispatch a request. An actual joined
WND test must supply the WND-authenticating private transport, not a stub.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Protocol
from urllib.parse import quote as url_quote
from uuid import UUID

from ..catalog import AvailabilityState, CommercialQuoteSnapshot
from ..order import (
    AuthoritativeOrderConfirmationSnapshot,
    CanonicalOrderProjection,
    CanonicalOrderState,
    OrderSubmissionConflict,
    OrderSubmitRequest,
    OrderTransportUnknown,
    ServiceMode,
    ServiceModeProjection,
    ServiceModeUnavailable,
)


class WNDTrustBoundaryRejected(PermissionError):
    """The fake-only scope/authorization or WND source projection did not match."""


class WNDInMemoryRequestPort(Protocol):
    """A WND-owned, authenticated in-memory test client. No socket fallback."""

    is_in_memory: bool

    def request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]: ...


def _uuid(value: Any) -> str:
    try:
        result = str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise WNDTrustBoundaryRejected("wnd_canonical_uuid_required") from exc
    if result == str(UUID(int=0)):
        raise WNDTrustBoundaryRejected("wnd_nonzero_uuid_required")
    return result


def _epoch(value: Any) -> int:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError) as exc:
            raise WNDTrustBoundaryRejected("wnd_expiry_invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise WNDTrustBoundaryRejected("wnd_expiry_untrusted")
    return int(result.astimezone(timezone.utc).timestamp())


@dataclass(frozen=True)
class WNDTestScope:
    """Every value is fixture-provided, never inferred from a phone/message."""

    tenant_id: int
    merchant_public_id: str
    location_public_id: str
    location_id: int
    context_binding_public_id: str
    fake_bearer: str
    correlation_ref: str
    currency: str = "XAF"

    def __post_init__(self) -> None:
        if self.tenant_id != 910001 or self.location_id <= 0:
            raise WNDTrustBoundaryRejected("only_isolated_synthetic_tenant_allowed")
        for field in ("merchant_public_id", "location_public_id", "context_binding_public_id"):
            _uuid(getattr(self, field))
        if (
            not self.fake_bearer.startswith("offline-fixture-")
            or not self.correlation_ref.startswith("offline-")
            or self.currency != "XAF"
        ):
            raise WNDTrustBoundaryRejected("only_offline_fixture_authority_allowed")


class WNDR1InertOrderPort:
    """XBOSOrderPort-compatible fake-only WND private checkout adapter.

    WND must authenticate the request headers *itself* in the injected
    transport. This adapter cannot assert that WND's auth succeeded.
    """

    def __init__(self, *, scope: WNDTestScope, transport: WNDInMemoryRequestPort):
        if getattr(transport, "is_in_memory", False) is not True:
            raise WNDTrustBoundaryRejected("in_memory_wnd_transport_required")
        self._scope = scope
        self._transport = transport
        self._confirmations: dict[str, AuthoritativeOrderConfirmationSnapshot] = {}
        self._submitted_confirmations: dict[str, str] = {}

    def _call(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        if not path.startswith("/internal/customer-channel/v1/"):
            raise WNDTrustBoundaryRejected("unapproved_wnd_private_path")
        response = self._transport.request(
            method=method,
            path=path,
            headers={
                "Authorization": "Bearer " + self._scope.fake_bearer,
                "X-XBOS-Context-Binding-Ref": self._scope.context_binding_public_id,
                "X-Correlation-Ref": self._scope.correlation_ref,
            },
            json_body=payload,
        )
        if not isinstance(response, Mapping):
            raise WNDTrustBoundaryRejected("untrusted_wnd_response")
        if "error" in response:
            raise OrderSubmissionConflict("wnd_private_rejected")
        return response

    def resolve_service_context(
        self, *, merchant_ref: str, location_ref: str, mode: ServiceMode,
        table_ref: str | None, contact_ref: str | None,
        delivery_address_ref: str | None,
    ) -> ServiceModeProjection:
        if (
            _uuid(merchant_ref) != _uuid(self._scope.merchant_public_id)
            or _uuid(location_ref) != _uuid(self._scope.location_public_id)
        ):
            raise WNDTrustBoundaryRejected("wnd_merchant_location_mismatch")
        if mode not in (ServiceMode.DINE_IN, ServiceMode.TAKEAWAY, ServiceMode.DELIVERY):
            raise ServiceModeUnavailable("wnd_mode_unavailable")
        if mode is ServiceMode.DINE_IN and not table_ref:
            raise ServiceModeUnavailable("wnd_table_required")
        if mode is ServiceMode.TAKEAWAY and not contact_ref:
            raise ServiceModeUnavailable("wnd_contact_required")
        if mode is ServiceMode.DELIVERY and (not contact_ref or not delivery_address_ref):
            raise ServiceModeUnavailable("wnd_delivery_bindings_required")
        for value in (table_ref, contact_ref, delivery_address_ref):
            if value is not None:
                _uuid(value)
        return ServiceModeProjection(
            service_context_ref=_uuid(self._scope.context_binding_public_id),
            merchant_ref=_uuid(self._scope.merchant_public_id),
            location_ref=_uuid(self._scope.location_public_id),
            mode=mode,
            available=True,
            table_ref=table_ref,
            contact_ref=contact_ref,
            delivery_address_ref=delivery_address_ref,
            currency="XAF",
        )

    def prepare_order_confirmation(
        self, *, quote: CommercialQuoteSnapshot,
        service_context: ServiceModeProjection, now_epoch: int,
    ) -> AuthoritativeOrderConfirmationSnapshot:
        if (
            quote.currency != "XAF"
            or quote.merchant_ref != self._scope.merchant_public_id
            or quote.location_ref != self._scope.location_public_id
            or service_context.merchant_ref != self._scope.merchant_public_id
            or service_context.location_ref != self._scope.location_public_id
            or service_context.service_context_ref != self._scope.context_binding_public_id
            or not service_context.available
        ):
            raise WNDTrustBoundaryRejected("wnd_quote_context_mismatch")
        if quote.expires_at_epoch <= now_epoch:
            raise WNDTrustBoundaryRejected("wnd_quote_expired")
        if not quote.lines or any(
            line.availability is not AvailabilityState.AVAILABLE or line.quantity <= 0
            for line in quote.lines
        ):
            raise WNDTrustBoundaryRejected("wnd_quote_line_not_available")
        payload: dict[str, Any] = {
            "service_mode": service_context.mode.value,
            "cart_lines": [
                {
                    "catalog_entry_ref": _uuid(line.item_ref),
                    "quantity": str(line.quantity),
                    "option_refs": [_uuid(x) for x in line.option_refs],
                }
                for line in quote.lines
            ],
            "observed_quote_ref": quote.quote_ref,
            "observed_quote_version": quote.version,
        }
        for k in ("table_ref", "contact_ref", "delivery_address_ref"):
            value = getattr(service_context, k)
            if value is not None:
                payload[k] = _uuid(value)
        # NO caller amount, currency override, unit price or tenant in request.
        raw = self._call("POST", "/internal/customer-channel/v1/order-confirmations", payload)
        if (
            _uuid(raw.get("merchant_ref")) != _uuid(self._scope.merchant_public_id)
            or int(raw.get("location_ref", -1)) != self._scope.location_id
            or _uuid(raw.get("service_context_ref")) != _uuid(self._scope.context_binding_public_id)
            or raw.get("service_mode") != service_context.mode.value
            or raw.get("currency") != "XAF"
            or raw.get("observed_quote_ref") != quote.quote_ref
            or raw.get("observed_quote_version") != quote.version
        ):
            raise WNDTrustBoundaryRejected("wnd_confirmation_binding_mismatch")
        expiry = _epoch(raw.get("expires_at"))
        if expiry <= now_epoch:
            raise WNDTrustBoundaryRejected("wnd_confirmation_expired")
        lines = tuple(raw.get("authoritative_lines") or ())
        if len(lines) != len(quote.lines):
            raise WNDTrustBoundaryRejected("wnd_authoritative_line_count_mismatch")
        for expected, actual in zip(quote.lines, lines):
            if (
                _uuid(actual.get("catalog_entry_ref")) != _uuid(expected.item_ref)
                or Decimal(str(actual.get("quantity"))) != Decimal(expected.quantity)
                or Decimal(str(actual.get("unit_price"))) != expected.unit_price
                or str(actual.get("currency")) != "XAF"
            ):
                raise WNDTrustBoundaryRejected("wnd_authoritative_price_or_line_changed")
        canonical_total = Decimal(str(raw.get("canonical_total")))
        if canonical_total != quote.total:
            raise WNDTrustBoundaryRejected("wnd_canonical_total_changed_reconfirmation_required")
        confirmation = AuthoritativeOrderConfirmationSnapshot(
            confirmation_ref=_uuid(raw.get("confirmation_ref")),
            quote_ref=quote.quote_ref,
            quote_version=quote.version,
            merchant_ref=quote.merchant_ref,
            location_ref=quote.location_ref,
            service_context_ref=service_context.service_context_ref,
            service_mode=service_context.mode,
            lines=quote.lines,
            delivery_fee=None,
            taxes_charges=(),
            total=canonical_total,
            currency="XAF",
            expires_at_epoch=expiry,
        )
        self._confirmations[confirmation.confirmation_ref] = confirmation
        return confirmation

    def resolve_order_confirmation(
        self, confirmation_ref: str
    ) -> AuthoritativeOrderConfirmationSnapshot | None:
        # In-memory handle is an inert copy of a WND-issued confirmation.
        # WND revalidates it against its persisted snapshot at submit.
        return self._confirmations.get(confirmation_ref)

    def _order(self, raw: Mapping[str, Any], request: OrderSubmitRequest) -> CanonicalOrderProjection:
        if str(raw.get("order_state")).lower() != "submitted":
            raise WNDTrustBoundaryRejected("wnd_order_not_submitted")
        order_id = _uuid(raw.get("order_ref"))  # WND must RETURN this value.
        return CanonicalOrderProjection(
            order_ref=order_id,
            client_submit_ref=request.client_submit_ref,
            correlation_ref=request.correlation_ref,
            confirmation_ref=request.confirmation_ref,
            state=CanonicalOrderState.CONFIRMED,
            evidence_ref="wnd:fake:source-order:" + order_id,
        )

    def submit_order(self, request: OrderSubmitRequest) -> CanonicalOrderProjection:
        if request.correlation_ref != self._scope.correlation_ref:
            raise WNDTrustBoundaryRejected("wnd_submit_correlation_mismatch")
        if request.confirmation_ref not in self._confirmations:
            raise WNDTrustBoundaryRejected("wnd_confirmation_not_source_issued")
        existing_ref = self._submitted_confirmations.get(request.client_submit_ref)
        if existing_ref is not None and existing_ref != request.confirmation_ref:
            raise OrderSubmissionConflict("idempotency_payload_conflict")
        # Reserve correlation before transport: a lost reply MUST reconcile
        # by the same key, never be assumed successful or start a new order.
        self._submitted_confirmations[request.client_submit_ref] = request.confirmation_ref
        try:
            raw = self._call(
                "POST", "/internal/customer-channel/v1/orders",
                {"confirmation_ref": request.confirmation_ref, "client_submit_ref": request.client_submit_ref},
            )
        except OrderTransportUnknown:
            raise  # Existing Channel OrderService will reconcile.
        result = self._order(raw, request)
        self._submitted_confirmations[request.client_submit_ref] = request.confirmation_ref
        return result

    def get_order_by_client_ref(self, client_submit_ref: str) -> CanonicalOrderProjection | None:
        prior = self._submitted_confirmations.get(client_submit_ref)
        if prior is None:
            return None
        request = OrderSubmitRequest(
            client_submit_ref=client_submit_ref,
            correlation_ref=self._scope.correlation_ref,
            confirmation_ref=prior,
        )
        raw = self._call(
            "GET", "/internal/customer-channel/v1/orders/by-client-submit-ref/"
            + url_quote(client_submit_ref, safe=""),
        )
        result = self._order(raw, request)
        return result

    def prepare_wnd_draft_test_only(
        self, *, catalog_entry_ref: str, contact_ref: str,
        create_command_ref: str, add_command_ref: str,
        quantity: int = 2,
    ) -> tuple[str, str, str, Decimal]:
        """Actual WND private draft-create/add/confirm API, test-only injected.

        Does NOT invent a draft/order UUID or a price. The WND service must
        authenticate bearer and context, apply catalog pricing and return refs.
        """
        if quantity != 2 or not create_command_ref or not add_command_ref:
            raise WNDTrustBoundaryRejected("wnd_synthetic_draft_scope_required")
        entry_id, contact_id = _uuid(catalog_entry_ref), _uuid(contact_ref)
        created = self._call(
            "POST", "/internal/customer-channel/v1/order-drafts",
            {
                "client_command_ref": create_command_ref,
                "service_mode": "takeaway",
                "contact_ref": contact_id,
            },
        )
        draft_id = _uuid(created.get("draft_ref"))
        order_id = _uuid(created.get("order_ref"))
        if str(created.get("order_state")).upper() != "DRAFT":
            raise WNDTrustBoundaryRejected("wnd_draft_not_open")
        try:
            version = int(created["draft_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WNDTrustBoundaryRejected("wnd_draft_version_missing") from exc
        if version < 1:
            raise WNDTrustBoundaryRejected("wnd_draft_version_invalid")
        updated = self._call(
            "POST",
            "/internal/customer-channel/v1/order-drafts/" + draft_id + "/commands",
            {
                "operation": "ADD_LINE",
                "client_command_ref": add_command_ref,
                "expected_draft_version": version,
                "item_ref": entry_id,
                "quantity": "2",
                "modifier_refs": [],
            },
        )
        if _uuid(updated.get("order_ref")) != order_id:
            raise WNDTrustBoundaryRejected("wnd_draft_order_identity_changed")
        if updated.get("currency") != "XAF":
            raise WNDTrustBoundaryRejected("wnd_draft_currency_changed")
        lines = updated.get("lines")
        if not isinstance(lines, list) or len(lines) != 1:
            raise WNDTrustBoundaryRejected("wnd_draft_line_count_wrong")
        if (
            Decimal(str(lines[0].get("unit_price"))) != Decimal("1750")
            or Decimal(str(lines[0].get("quantity"))) != Decimal("2")
            or Decimal(str(lines[0].get("line_total"))) != Decimal("3500")
            or Decimal(str(updated.get("canonical_total"))) != Decimal("3500")
        ):
            raise WNDTrustBoundaryRejected("wnd_server_price_not_3500_xaf")
        confirmed = self._call(
            "POST",
            "/internal/customer-channel/v1/order-drafts/" + draft_id + "/confirmation",
            {},
        )
        if (
            _uuid(confirmed.get("merchant_ref")) != _uuid(self._scope.merchant_public_id)
            or int(confirmed.get("location_ref", -1)) != self._scope.location_id
            or _uuid(confirmed.get("service_context_ref")) != _uuid(self._scope.context_binding_public_id)
            or str(confirmed.get("currency")) != "XAF"
            or Decimal(str(confirmed.get("canonical_total"))) != Decimal("3500")
        ):
            raise WNDTrustBoundaryRejected("wnd_draft_confirmation_mismatch")
        confirm_id = _uuid(confirmed.get("confirmation_ref"))
        return draft_id, order_id, confirm_id, Decimal("3500")

    def submit_wnd_draft_test_only(
        self, *, draft_ref: str, expected_order_ref: str,
        confirmation_ref: str, client_submit_ref: str,
    ) -> CanonicalOrderProjection:
        """One WND draft submit and source-owned UUID replay, no payments."""
        draft_id, order_id, confirmation_id = (
            _uuid(draft_ref), _uuid(expected_order_ref), _uuid(confirmation_ref)
        )
        if not client_submit_ref:
            raise WNDTrustBoundaryRejected("wnd_submit_key_required")
        existing = self._submitted_confirmations.get(client_submit_ref)
        if existing is not None and existing != confirmation_id:
            raise OrderSubmissionConflict("idempotency_payload_conflict")
        self._submitted_confirmations[client_submit_ref] = confirmation_id
        raw = self._call(
            "POST",
            "/internal/customer-channel/v1/order-drafts/" + draft_id + "/submit",
            {"confirmation_ref": confirmation_id, "client_submit_ref": client_submit_ref},
        )
        request = OrderSubmitRequest(
            client_submit_ref=client_submit_ref,
            correlation_ref=self._scope.correlation_ref,
            confirmation_ref=confirmation_id,
        )
        result = self._order(raw, request)
        if result.order_ref != order_id:
            raise WNDTrustBoundaryRejected("wnd_submitted_uuid_does_not_match_draft")
        return result


class WNDMerchantOrderPort(WNDR1InertOrderPort):
    """Source-backed staging candidate using a separate authenticated WND client.

    No runtime wiring, secret lookup, provider call, identity registration,
    or network action occurs at construction. WND itself must independently
    verify service digest/rotation/tenant/merchant/context for every request.
    """

    def __init__(self, *, transport: object) -> None:
        from .wnd_merchant_http import WNDMerchantPrivateTransport

        if type(transport) is not WNDMerchantPrivateTransport:
            raise WNDTrustBoundaryRejected("authenticated_wnd_private_transport_required")
        if not transport.is_approved_private:
            raise WNDTrustBoundaryRejected("wnd_private_transport_not_admitted")
        # Reuse the existing type-safe mapping, source UUID and replay code,
        # but NEVER invoke the test scope constructor or its fake bearer.
        self._scope = transport.binding
        self._transport = transport
        self._confirmations = {}
        self._submitted_confirmations = {}

    def _call(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        response = self._transport.request(
            method=method,
            path=path,
            headers={
                "X-XBOS-Context-Binding-Ref": self._scope.context_binding_public_id,
                "X-Correlation-Ref": self._scope.correlation_ref,
            },
            json_body=payload,
        )
        if not isinstance(response, Mapping):
            raise WNDTrustBoundaryRejected("untrusted_wnd_server_response")
        if "error" in response:
            raise OrderSubmissionConflict("wnd_private_authoritative_rejection")
        return response

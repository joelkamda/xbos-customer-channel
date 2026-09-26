from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

from .xbos_private_http import PrivateXBOSHTTPClient, PrivateXBOSHTTPError
from ..catalog import (
    AvailabilityState,
    CatalogItemProjection,
    CatalogProjection,
    CatalogSectionProjection,
    CommercialQuoteSnapshot,
    InteractionCart,
)


XBOS_CATALOG_READ_OPERATION = "R2Authority.menu"
XBOS_CATALOG_READ_PERMISSION = "restaurant.menu.read"
XBOS_CATALOG_CONTRACT_STATE = (
    "xbos-g02-c3-h1b-customer-channel-cross-process-binding-accepted-20260925"
)


class RealXBOSCatalogUnavailable(RuntimeError):
    """Fail-closed real XBOS catalog read boundary."""


class RealXBOSQuoteUnavailable(RealXBOSCatalogUnavailable):
    pass


class XBOSMenuReadClientError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class XBOSMenuReadBinding:
    """Runtime-supplied binding from Channel context to the accepted XBOS menu read."""

    merchant_ref: str
    location_ref: str
    tenant_id: int
    catalog_public_id: UUID
    price_code: str
    currency: str
    scope_type: str
    scope_id: int | None = None
    binding_ref: str | None = None
    binding_version: int | None = None

    def __post_init__(self) -> None:
        if not self.merchant_ref or not self.location_ref:
            raise ValueError("xbos_catalog_binding_context_required")
        if self.tenant_id <= 0:
            raise ValueError("xbos_catalog_binding_tenant_required")
        if not self.price_code.strip():
            raise ValueError("xbos_catalog_binding_price_code_required")
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("xbos_catalog_binding_currency_invalid")
        if not self.scope_type.strip():
            raise ValueError("xbos_catalog_binding_scope_type_required")
        if (self.binding_ref is None) != (self.binding_version is None):
            raise ValueError("xbos_catalog_binding_identity_incomplete")
        if self.binding_ref is not None and not self.binding_ref.strip():
            raise ValueError("xbos_catalog_binding_ref_required")
        if self.binding_version is not None and self.binding_version <= 0:
            raise ValueError("xbos_catalog_binding_version_invalid")


@dataclass(frozen=True, slots=True)
class XBOSMenuReadRequest:
    tenant_id: int
    catalog_public_id: UUID
    effective_at: datetime
    price_code: str
    currency: str
    scope_type: str
    scope_id: int | None


class XBOSCatalogBindingResolver(Protocol):
    def resolve(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
    ) -> XBOSMenuReadBinding | None: ...

    def resolve_bound(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        effective_at: datetime,
        correlation_ref: str,
    ) -> XBOSMenuReadBinding | None: ...


class XBOSMenuReadClient(Protocol):
    """Client seam for the accepted internal read operation R2Authority.menu."""

    def menu(self, request: XBOSMenuReadRequest) -> Any: ...

    def menu_bound(
        self,
        request: XBOSMenuReadRequest,
        *,
        binding_ref: str,
        binding_version: int,
        correlation_ref: str,
    ) -> Any: ...


class PrivateXBOSCatalogBindingResolver:
    """Resolve the XBOS-owned merchant/location catalog binding over the private seam."""

    def __init__(self, client: PrivateXBOSHTTPClient) -> None:
        self._client = client

    def resolve(self, *, merchant_ref: str, location_ref: str) -> XBOSMenuReadBinding | None:
        del merchant_ref, location_ref
        raise RealXBOSCatalogUnavailable(
            "bound_effective_at_and_correlation_required"
        )

    def resolve_bound(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        effective_at: datetime,
        correlation_ref: str,
    ) -> XBOSMenuReadBinding | None:
        try:
            raw = self._client.resolve_catalog_binding(
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                effective_at=effective_at,
                correlation_ref=correlation_ref,
            )
        except PrivateXBOSHTTPError as exc:
            raise RealXBOSCatalogUnavailable(
                f"xbos_catalog_binding_failed:{exc.reason}"
            ) from exc
        try:
            binding_ref = raw["binding_ref"]
            binding_version = raw["binding_version"]
            tenant_id = raw["tenant_id"]
            if not isinstance(binding_ref, str) or not binding_ref.strip():
                raise ValueError("binding_ref_required")
            if isinstance(binding_version, bool) or int(binding_version) <= 0:
                raise ValueError("binding_version_invalid")
            if isinstance(tenant_id, bool) or int(tenant_id) <= 0:
                raise ValueError("tenant_id_invalid")
            return XBOSMenuReadBinding(
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                tenant_id=int(tenant_id),
                catalog_public_id=UUID(str(raw["catalog_public_id"])),
                price_code=str(raw["price_code"]),
                currency=str(raw["currency"]),
                scope_type=str(raw["scope_type"]),
                scope_id=(
                    None if raw.get("scope_id") is None else int(raw["scope_id"])
                ),
                binding_ref=binding_ref.strip(),
                binding_version=int(binding_version),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RealXBOSCatalogUnavailable(
                "xbos_catalog_binding_malformed"
            ) from exc


class PrivateXBOSMenuReadClient:
    """Forward the exact seven R2 menu inputs plus binding transport metadata."""

    def __init__(self, client: PrivateXBOSHTTPClient) -> None:
        self._client = client

    def menu(self, request: XBOSMenuReadRequest) -> Any:
        del request
        raise XBOSMenuReadClientError(
            "bound_catalog_read_required"
        )

    def menu_bound(
        self,
        request: XBOSMenuReadRequest,
        *,
        binding_ref: str,
        binding_version: int,
        correlation_ref: str,
    ) -> Any:
        try:
            return self._client.menu(
                request,
                binding_ref=binding_ref,
                binding_version=binding_version,
                correlation_ref=correlation_ref,
            )
        except PrivateXBOSHTTPError as exc:
            raise XBOSMenuReadClientError(exc.reason) from exc


class RealXBOSCatalogAdapter:
    """Map the accepted XBOS R2/SO1 menu read into Customer Channel catalog types.

    This source tranche does not invent an HTTP route or principal.  The client
    and binding resolver are injected by a separately authorized runtime gate.
    """

    accepted_operation = XBOS_CATALOG_READ_OPERATION
    required_permission = XBOS_CATALOG_READ_PERMISSION
    accepted_contract_state = XBOS_CATALOG_CONTRACT_STATE

    def __init__(
        self,
        *,
        client: XBOSMenuReadClient,
        binding_resolver: XBOSCatalogBindingResolver,
        effective_at_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._binding_resolver = binding_resolver
        self._effective_at_factory = effective_at_factory or (
            lambda: datetime.now(timezone.utc)
        )

    def get_catalog(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
    ) -> CatalogProjection:
        effective_at = self._effective_at_factory()
        binding = self._binding_resolver.resolve(
            merchant_ref=merchant_ref,
            location_ref=location_ref,
        )
        if binding is None:
            raise RealXBOSCatalogUnavailable("xbos_catalog_binding_unavailable")
        request = self._request_for_binding(
            binding,
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            effective_at=effective_at,
        )
        try:
            menu = self._client.menu(request)
        except XBOSMenuReadClientError as exc:
            raise RealXBOSCatalogUnavailable(
                f"xbos_catalog_read_failed:{exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise RealXBOSCatalogUnavailable(
                "xbos_catalog_read_failed:timeout"
            ) from exc
        return _map_menu_projection(
            menu,
            request=request,
            merchant_ref=merchant_ref,
            location_ref=location_ref,
        )

    def get_catalog_bound(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        effective_at: datetime,
        correlation_ref: str,
    ) -> CatalogProjection:
        resolver = getattr(self._binding_resolver, "resolve_bound", None)
        reader = getattr(self._client, "menu_bound", None)
        if not callable(resolver) or not callable(reader):
            raise RealXBOSCatalogUnavailable(
                "real_xbos_private_catalog_runtime_binding_unavailable"
            )
        binding = resolver(
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            effective_at=effective_at,
            correlation_ref=correlation_ref,
        )
        if binding is None:
            raise RealXBOSCatalogUnavailable("xbos_catalog_binding_unavailable")
        if binding.binding_ref is None or binding.binding_version is None:
            raise RealXBOSCatalogUnavailable("xbos_catalog_binding_identity_required")
        request = self._request_for_binding(
            binding,
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            effective_at=effective_at,
        )
        try:
            menu = reader(
                request,
                binding_ref=binding.binding_ref,
                binding_version=binding.binding_version,
                correlation_ref=correlation_ref,
            )
        except XBOSMenuReadClientError as exc:
            raise RealXBOSCatalogUnavailable(
                f"xbos_catalog_read_failed:{exc.reason}"
            ) from exc
        return _map_menu_projection(
            menu,
            request=request,
            merchant_ref=merchant_ref,
            location_ref=location_ref,
        )

    @staticmethod
    def _request_for_binding(
        binding: XBOSMenuReadBinding,
        *,
        merchant_ref: str,
        location_ref: str,
        effective_at: datetime,
    ) -> XBOSMenuReadRequest:
        if (
            binding.merchant_ref != merchant_ref
            or binding.location_ref != location_ref
        ):
            raise RealXBOSCatalogUnavailable("xbos_catalog_binding_context_mismatch")
        if effective_at.tzinfo is None or effective_at.utcoffset() is None:
            raise RealXBOSCatalogUnavailable("xbos_catalog_effective_at_must_be_aware")
        return XBOSMenuReadRequest(
            tenant_id=binding.tenant_id,
            catalog_public_id=binding.catalog_public_id,
            effective_at=effective_at.astimezone(timezone.utc),
            price_code=binding.price_code.strip().lower(),
            currency=binding.currency.strip().upper(),
            scope_type=binding.scope_type.strip().lower(),
            scope_id=binding.scope_id,
        )

    def resolve_quote(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        cart: InteractionCart,
        now_epoch: int,
    ) -> CommercialQuoteSnapshot:
        del merchant_ref, location_ref, cart, now_epoch
        raise RealXBOSQuoteUnavailable(
            "real_xbos_quote_contract_not_materialized_by_loop_b_r1"
        )


def _map_menu_projection(
    menu: Any,
    *,
    request: XBOSMenuReadRequest,
    merchant_ref: str,
    location_ref: str,
) -> CatalogProjection:
    if menu is None:
        raise RealXBOSCatalogUnavailable("xbos_catalog_response_missing")

    tenant_id = _int_field(menu, "tenant_id")
    if tenant_id != request.tenant_id:
        raise RealXBOSCatalogUnavailable("xbos_catalog_tenant_mismatch")

    if bool(_field(menu, "creates_financial_truth")):
        raise RealXBOSCatalogUnavailable(
            "xbos_catalog_read_claimed_financial_truth"
        )

    catalog = _field(menu, "catalog_reference")
    catalog_ref = _uuid_text(_field(catalog, "public_id"), "catalog_public_id")
    if catalog_ref != str(request.catalog_public_id):
        raise RealXBOSCatalogUnavailable("xbos_catalog_identity_mismatch")

    pricing = _field(menu, "pricing_context")
    price_code = _text_field(pricing, "price_code").lower()
    currency = _currency(_field(pricing, "currency"))
    scope_type = _text_field(pricing, "scope_type").lower()
    scope_id = _optional_int(_optional_field(pricing, "scope_id"))
    if price_code != request.price_code:
        raise RealXBOSCatalogUnavailable("xbos_catalog_price_code_mismatch")
    if currency != request.currency:
        raise RealXBOSCatalogUnavailable("xbos_catalog_currency_mismatch")
    if scope_type != request.scope_type or scope_id != request.scope_id:
        raise RealXBOSCatalogUnavailable("xbos_catalog_scope_mismatch")

    sections_out: list[CatalogSectionProjection] = []
    items_out: list[CatalogItemProjection] = []
    section_refs: set[str] = set()
    item_refs: set[str] = set()
    version_parts: list[dict[str, Any]] = []

    sections = _sequence_field(menu, "sections")
    for section in sections:
        section_ref = _uuid_text(
            _field(section, "section_public_id"),
            "section_public_id",
        )
        if section_ref in section_refs:
            raise RealXBOSCatalogUnavailable("xbos_catalog_duplicate_section")
        section_refs.add(section_ref)
        section_name = _text_field(section, "display_name")
        section_sort = _int_field(section, "sort_order")
        sections_out.append(
            CatalogSectionProjection(
                section_ref=section_ref,
                name=section_name,
                sort_order=section_sort,
            )
        )

        for entry in _sequence_field(section, "entries"):
            item_ref = _uuid_text(
                _field(entry, "target_public_id"),
                "target_public_id",
            )
            if item_ref in item_refs:
                raise RealXBOSCatalogUnavailable(
                    "xbos_catalog_duplicate_sellable_identity"
                )
            item_refs.add(item_ref)

            target_type = _enum_text(_field(entry, "target_type"))
            if target_type not in {"atomic_unit", "offer"}:
                raise RealXBOSCatalogUnavailable(
                    "xbos_catalog_target_type_invalid"
                )

            target = _field(entry, "target_presentation")
            if _optional_field(target, "active", True) is not True:
                raise RealXBOSCatalogUnavailable(
                    "xbos_catalog_inactive_target_returned"
                )
            name = _text_field(target, "name")
            description = _target_description(target)
            media_refs = _target_media_refs(target)

            resolved_price = _field(entry, "resolved_price")
            amount = _decimal_field(resolved_price, "amount")
            item_currency = _currency(_field(resolved_price, "currency"))
            if item_currency != currency:
                raise RealXBOSCatalogUnavailable(
                    "xbos_catalog_item_currency_mismatch"
                )
            resolved_price_code = _text_field(
                resolved_price,
                "price_code",
            ).lower()
            if resolved_price_code != price_code:
                raise RealXBOSCatalogUnavailable(
                    "xbos_catalog_item_price_code_mismatch"
                )

            modifier_refs = tuple(
                _uuid_text(
                    _field(group, "group_public_id"),
                    "group_public_id",
                )
                for group in _sequence_field(
                    entry,
                    "modifier_configuration",
                )
            )

            items_out.append(
                CatalogItemProjection(
                    item_ref=item_ref,
                    section_ref=section_ref,
                    name=name,
                    description=description,
                    media_refs=media_refs,
                    modifier_group_refs=modifier_refs,
                    availability=AvailabilityState.AVAILABLE,
                    display_price=amount,
                    currency=item_currency,
                )
            )
            version_parts.append(
                {
                    "catalog_entry_public_id": _uuid_text(
                        _field(entry, "catalog_entry_public_id"),
                        "catalog_entry_public_id",
                    ),
                    "target_type": target_type,
                    "target_public_id": item_ref,
                    "price_public_id": _uuid_text(
                        _field(resolved_price, "public_id"),
                        "price_public_id",
                    ),
                    "amount": str(amount),
                    "currency": item_currency,
                }
            )

    version = _projection_version(
        catalog_ref=catalog_ref,
        effective_at=request.effective_at,
        price_code=price_code,
        currency=currency,
        scope_type=scope_type,
        scope_id=scope_id,
        entries=version_parts,
    )

    return CatalogProjection(
        catalog_ref=catalog_ref,
        merchant_ref=merchant_ref,
        location_ref=location_ref,
        version=version,
        currency=currency,
        terminology=(),
        sections=tuple(sections_out),
        items=tuple(items_out),
    )


def _projection_version(
    *,
    catalog_ref: str,
    effective_at: datetime,
    price_code: str,
    currency: str,
    scope_type: str,
    scope_id: int | None,
    entries: list[dict[str, Any]],
) -> str:
    document = {
        "catalog_ref": catalog_ref,
        "effective_at": effective_at.astimezone(timezone.utc).isoformat(),
        "price_code": price_code,
        "currency": currency,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "entries": entries,
    }
    digest = hashlib.sha256(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"xbos-menu-read-v1:{digest}"


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        if name not in value:
            raise RealXBOSCatalogUnavailable(
                f"xbos_catalog_missing_required_field:{name}"
            )
        return value[name]
    if not hasattr(value, name):
        raise RealXBOSCatalogUnavailable(
            f"xbos_catalog_missing_required_field:{name}"
        )
    return getattr(value, name)


def _optional_field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text_field(value: Any, name: str) -> str:
    result = str(_field(value, name)).strip()
    if not result:
        raise RealXBOSCatalogUnavailable(
            f"xbos_catalog_empty_required_field:{name}"
        )
    return result


def _int_field(value: Any, name: str) -> int:
    raw = _field(value, name)
    if isinstance(raw, bool):
        raise RealXBOSCatalogUnavailable(
            f"xbos_catalog_invalid_integer:{name}"
        )
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise RealXBOSCatalogUnavailable(
            f"xbos_catalog_invalid_integer:{name}"
        ) from None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise RealXBOSCatalogUnavailable("xbos_catalog_invalid_scope_id")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise RealXBOSCatalogUnavailable(
            "xbos_catalog_invalid_scope_id"
        ) from None


def _sequence_field(value: Any, name: str) -> Sequence[Any]:
    raw = _field(value, name)
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise RealXBOSCatalogUnavailable(
            f"xbos_catalog_invalid_sequence:{name}"
        )
    return raw


def _decimal_field(value: Any, name: str) -> Decimal:
    raw = _field(value, name)
    try:
        amount = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise RealXBOSCatalogUnavailable(
            f"xbos_catalog_invalid_decimal:{name}"
        ) from None
    if not amount.is_finite():
        raise RealXBOSCatalogUnavailable(
            f"xbos_catalog_invalid_decimal:{name}"
        )
    return amount


def _uuid_text(value: Any, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise RealXBOSCatalogUnavailable(
            f"xbos_catalog_invalid_uuid:{field}"
        ) from None


def _currency(value: Any) -> str:
    currency = str(value).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise RealXBOSCatalogUnavailable("xbos_catalog_currency_invalid")
    return currency


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().lower()


def _target_description(target: Any) -> str:
    direct = _optional_field(target, "description")
    if direct is not None:
        return str(direct)
    metadata = _optional_field(target, "metadata", {})
    if isinstance(metadata, Mapping):
        description = metadata.get("description")
        if description is not None:
            return str(description)
    return ""


def _target_media_refs(target: Any) -> tuple[str, ...]:
    raw = _optional_field(target, "media_refs")
    if raw is None:
        metadata = _optional_field(target, "metadata", {})
        raw = metadata.get("media_refs", ()) if isinstance(metadata, Mapping) else ()
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        raise RealXBOSCatalogUnavailable("xbos_catalog_media_refs_invalid")
    return tuple(str(item) for item in raw)

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ..entry_context import EntryContextAttestation, EntryPurpose, MerchantContextProjection
from .xbos_private_http import PrivateXBOSHTTPClient, PrivateXBOSHTTPError


class RealXBOSContextUnavailable(PermissionError):
    """Fail-closed boundary for real XBOS context reattestation."""


class RealXBOSContextAdapter:
    """Reattest an existing server-issued XBOS/IA0 context binding."""

    requires_explicit_context_binding_ref = True

    def __init__(self, client: PrivateXBOSHTTPClient) -> None:
        self._client = client

    def resolve_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        purpose: EntryPurpose,
    ) -> MerchantContextProjection:
        del merchant_ref, location_ref, table_ref, purpose
        raise RealXBOSContextUnavailable("existing_context_binding_ref_required")

    def attest_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        purpose: EntryPurpose,
        dining_area_ref: str | None = None,
    ) -> EntryContextAttestation:
        del merchant_ref, location_ref, table_ref, purpose, dining_area_ref
        raise RealXBOSContextUnavailable("existing_context_binding_ref_required")

    def attest_bound_context(
        self,
        *,
        context_binding_ref: str,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        purpose: EntryPurpose,
        dining_area_ref: str | None,
        effective_at: datetime,
        correlation_ref: str,
    ) -> EntryContextAttestation:
        if not context_binding_ref.strip():
            raise RealXBOSContextUnavailable("existing_context_binding_ref_required")
        try:
            raw = self._client.attest_context(
                context_binding_ref=context_binding_ref,
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                table_ref=table_ref,
                dining_area_ref=dining_area_ref,
                purpose=purpose.value,
                effective_at=effective_at,
                correlation_ref=correlation_ref,
            )
        except PrivateXBOSHTTPError as exc:
            raise RealXBOSContextUnavailable(
                f"xbos_context_reattestation_failed:{exc.reason}"
            ) from exc

        projection_raw = _mapping(raw, "projection")
        projection = MerchantContextProjection(
            merchant_ref=_text(projection_raw, "merchant_ref"),
            location_ref=_text(projection_raw, "location_ref"),
            service_available=_bool(projection_raw, "service_available"),
            display_name=_text(projection_raw, "display_name"),
            terminology=_terminology(projection_raw.get("terminology", ())),
            currency=_currency(projection_raw.get("currency")),
            allowed_fulfillment_modes=_text_sequence(
                projection_raw.get("allowed_fulfillment_modes", ())
            ),
        )
        returned_purpose = _text(raw, "purpose")
        returned_table = _optional_text(raw.get("table_ref"))
        returned_area = _optional_text(raw.get("dining_area_ref"))
        returned_binding = _text(raw, "context_binding_ref")
        returned_merchant = _text(raw, "merchant_ref")
        returned_location = _text(raw, "location_ref")

        if returned_binding != context_binding_ref:
            raise RealXBOSContextUnavailable("context_binding_ref_mismatch")
        if returned_merchant != merchant_ref or returned_location != location_ref:
            raise RealXBOSContextUnavailable("context_chain_mismatch")
        if projection.merchant_ref != merchant_ref or projection.location_ref != location_ref:
            raise RealXBOSContextUnavailable("context_projection_mismatch")
        if returned_table != table_ref:
            raise RealXBOSContextUnavailable("table_context_mismatch")
        if dining_area_ref is not None and returned_area != dining_area_ref:
            raise RealXBOSContextUnavailable("dining_area_context_mismatch")
        if returned_purpose != purpose.value:
            raise RealXBOSContextUnavailable("purpose_mismatch")

        tenant_raw = raw.get("tenant_ref")
        tenant_ref = None if tenant_raw is None else str(tenant_raw)
        return EntryContextAttestation(
            tenant_ref=tenant_ref,
            merchant_ref=returned_merchant,
            location_ref=returned_location,
            table_ref=returned_table,
            dining_area_ref=returned_area,
            purpose=purpose,
            context_binding_ref=returned_binding,
            projection=projection,
        )


def _mapping(value: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    selected = value.get(name)
    if not isinstance(selected, Mapping):
        raise RealXBOSContextUnavailable(f"missing_or_invalid:{name}")
    return selected


def _text(value: Mapping[str, Any], name: str) -> str:
    selected = value.get(name)
    if not isinstance(selected, str) or not selected.strip():
        raise RealXBOSContextUnavailable(f"missing_or_invalid:{name}")
    return selected.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    selected = str(value).strip()
    if not selected:
        raise RealXBOSContextUnavailable("invalid_optional_context_reference")
    return selected


def _bool(value: Mapping[str, Any], name: str) -> bool:
    selected = value.get(name)
    if not isinstance(selected, bool):
        raise RealXBOSContextUnavailable(f"missing_or_invalid:{name}")
    return selected


def _currency(value: Any) -> str:
    selected = str(value or "").strip().upper()
    if len(selected) != 3 or not selected.isalpha():
        raise RealXBOSContextUnavailable("invalid_context_currency")
    return selected


def _terminology(value: Any) -> tuple[tuple[str, str], ...]:
    if isinstance(value, Mapping):
        items = value.items()
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = value
    else:
        raise RealXBOSContextUnavailable("invalid_context_terminology")
    result: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)) or len(item) != 2:
            raise RealXBOSContextUnavailable("invalid_context_terminology")
        key, display = str(item[0]).strip(), str(item[1]).strip()
        if not key or not display:
            raise RealXBOSContextUnavailable("invalid_context_terminology")
        result.append((key, display))
    return tuple(result)


def _text_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise RealXBOSContextUnavailable("invalid_fulfillment_modes")
    result = tuple(str(item).strip() for item in value)
    if any(not item for item in result):
        raise RealXBOSContextUnavailable("invalid_fulfillment_modes")
    return result

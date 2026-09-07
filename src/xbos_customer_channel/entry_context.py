from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EntryPurpose(StrEnum):
    DINE_IN = "dine_in"
    TAKEAWAY = "takeaway"
    DELIVERY = "delivery"
    MERCHANT_DISCOVERY = "merchant_discovery"


class ReplayPolicy(StrEnum):
    REUSABLE = "reusable"
    SINGLE_USE = "single_use"


class EntryTarget(StrEnum):
    WHATSAPP = "whatsapp"
    CUSTOMER_WEB = "customer_web"


@dataclass(frozen=True, slots=True)
class MerchantContextProjection:
    merchant_ref: str
    location_ref: str
    service_available: bool
    display_name: str
    terminology: tuple[tuple[str, str], ...]
    currency: str
    allowed_fulfillment_modes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntryContextAttestation:
    """Server-side XBOS context attestation; transport callers never author this object."""

    tenant_ref: str | None
    merchant_ref: str
    location_ref: str
    table_ref: str | None
    dining_area_ref: str | None
    purpose: EntryPurpose
    context_binding_ref: str
    projection: MerchantContextProjection

    @property
    def service_available(self) -> bool:
        return self.projection.service_available

    @property
    def display_name(self) -> str:
        return self.projection.display_name

    @property
    def terminology(self) -> tuple[tuple[str, str], ...]:
        return self.projection.terminology

    @property
    def currency(self) -> str:
        return self.projection.currency

    @property
    def allowed_fulfillment_modes(self) -> tuple[str, ...]:
        return self.projection.allowed_fulfillment_modes


@dataclass(frozen=True, slots=True)
class EntryTokenRecord:
    token_ref: str
    tenant_ref: str | None
    merchant_ref: str
    location_ref: str
    table_ref: str | None
    dining_area_ref: str | None
    purpose: EntryPurpose
    context_binding_ref: str
    expires_at_epoch: int
    version: int
    replay_policy: ReplayPolicy
    consumed_at_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class ResolvedEntryContext:
    token_ref: str
    merchant_ref: str
    location_ref: str
    table_ref: str | None
    dining_area_ref: str | None
    purpose: EntryPurpose
    projection: MerchantContextProjection
    tenant_ref: str | None = None
    context_binding_ref: str = "legacy_unattested"

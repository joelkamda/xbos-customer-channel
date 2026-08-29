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
class EntryTokenRecord:
    token_ref: str
    merchant_ref: str
    location_ref: str
    table_ref: str | None
    dining_area_ref: str | None
    purpose: EntryPurpose
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

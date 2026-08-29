from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class AvailabilityState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CatalogSectionProjection:
    section_ref: str
    name: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class CatalogItemProjection:
    item_ref: str
    section_ref: str
    name: str
    description: str
    media_refs: tuple[str, ...]
    modifier_group_refs: tuple[str, ...]
    availability: AvailabilityState
    display_price: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class CatalogProjection:
    catalog_ref: str
    merchant_ref: str
    location_ref: str
    version: str
    currency: str
    terminology: tuple[tuple[str, str], ...]
    sections: tuple[CatalogSectionProjection, ...]
    items: tuple[CatalogItemProjection, ...]

    def item(self, item_ref: str) -> CatalogItemProjection:
        for item in self.items:
            if item.item_ref == item_ref:
                return item
        raise KeyError(item_ref)


@dataclass(frozen=True, slots=True)
class CartLine:
    """Interaction state only: refs/options/quantity, never commercial amounts."""

    item_ref: str
    quantity: int
    option_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity_must_be_positive")


@dataclass(frozen=True, slots=True)
class InteractionCart:
    lines: tuple[CartLine, ...] = ()

    def add(self, item_ref: str, *, quantity: int = 1, option_refs: tuple[str, ...] = ()) -> "InteractionCart":
        if quantity <= 0:
            raise ValueError("quantity_must_be_positive")
        return InteractionCart(self.lines + (CartLine(item_ref, quantity, option_refs),))


@dataclass(frozen=True, slots=True)
class QuoteLineSnapshot:
    item_ref: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    availability: AvailabilityState
    option_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommercialQuoteSnapshot:
    quote_ref: str
    merchant_ref: str
    location_ref: str
    currency: str
    total: Decimal
    version: str
    expires_at_epoch: int
    lines: tuple[QuoteLineSnapshot, ...]


class QuoteChangeKind(StrEnum):
    PRICE_CHANGED = "price_changed"
    AVAILABILITY_CHANGED = "availability_changed"
    ITEM_MISSING = "item_missing"


@dataclass(frozen=True, slots=True)
class QuoteChange:
    kind: QuoteChangeKind
    item_ref: str
    displayed_value: str
    quoted_value: str


@dataclass(frozen=True, slots=True)
class QuoteReview:
    quote: CommercialQuoteSnapshot
    changes: tuple[QuoteChange, ...]
    requires_reconfirmation: bool
    can_continue: bool

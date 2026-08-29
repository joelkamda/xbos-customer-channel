from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from ..catalog import (
    AvailabilityState,
    CatalogItemProjection,
    CatalogProjection,
    CatalogSectionProjection,
    CommercialQuoteSnapshot,
    InteractionCart,
    QuoteLineSnapshot,
)


class FakeXBOSCatalogClient:
    """Deterministic XBOS SO1/R2-style contract fixture; never channel catalog/pricing authority."""

    def __init__(self) -> None:
        self._merchant_ref = "merchant:fixture:alpha"
        self._location_ref = "location:fixture:one"
        self._version = 1
        self._items: dict[str, CatalogItemProjection] = {
            "item:fixture:one": CatalogItemProjection(
                item_ref="item:fixture:one",
                section_ref="section:fixture:daily",
                name="Fixture Daily Item",
                description="Deterministic catalog fixture",
                media_refs=("media:fixture:item-one",),
                modifier_group_refs=("modifier-group:fixture:sides",),
                availability=AvailabilityState.AVAILABLE,
                display_price=Decimal("3000"),
                currency="XAF",
            ),
            "item:fixture:two": CatalogItemProjection(
                item_ref="item:fixture:two",
                section_ref="section:fixture:main",
                name="Fixture Main Item",
                description="Second deterministic catalog fixture",
                media_refs=(),
                modifier_group_refs=(),
                availability=AvailabilityState.AVAILABLE,
                display_price=Decimal("4500"),
                currency="XAF",
            ),
        }

    def _assert_context(self, merchant_ref: str, location_ref: str) -> None:
        if (merchant_ref, location_ref) != (self._merchant_ref, self._location_ref):
            raise KeyError("catalog_context_not_found")

    def get_catalog(self, *, merchant_ref: str, location_ref: str) -> CatalogProjection:
        self._assert_context(merchant_ref, location_ref)
        return CatalogProjection(
            catalog_ref="catalog:fixture:alpha",
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            version=f"fixture-v{self._version}",
            currency="XAF",
            terminology=(("menu", "Menu"), ("item", "Item")),
            sections=(
                CatalogSectionProjection("section:fixture:daily", "Daily Selection", 1),
                CatalogSectionProjection("section:fixture:main", "Main Selection", 2),
            ),
            items=tuple(self._items.values()),
        )

    def resolve_quote(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        cart: InteractionCart,
        now_epoch: int,
    ) -> CommercialQuoteSnapshot:
        self._assert_context(merchant_ref, location_ref)
        lines: list[QuoteLineSnapshot] = []
        for line in cart.lines:
            item = self._items.get(line.item_ref)
            if item is None:
                continue
            lines.append(
                QuoteLineSnapshot(
                    item_ref=item.item_ref,
                    quantity=line.quantity,
                    unit_price=item.display_price,
                    line_total=item.display_price * line.quantity,
                    availability=item.availability,
                    option_refs=line.option_refs,
                )
            )
        total = sum(
            (line.line_total for line in lines if line.availability is AvailabilityState.AVAILABLE),
            Decimal("0"),
        )
        return CommercialQuoteSnapshot(
            quote_ref=f"quote:fixture:v{self._version}",
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            currency="XAF",
            total=total,
            version=f"fixture-v{self._version}",
            expires_at_epoch=now_epoch + 300,
            lines=tuple(lines),
        )

    # Fixture controls simulate authoritative XBOS changes between browse and confirmation.
    def set_price(self, item_ref: str, amount: Decimal) -> None:
        self._items[item_ref] = replace(self._items[item_ref], display_price=amount)
        self._version += 1

    def set_availability(self, item_ref: str, state: AvailabilityState) -> None:
        self._items[item_ref] = replace(self._items[item_ref], availability=state)
        self._version += 1

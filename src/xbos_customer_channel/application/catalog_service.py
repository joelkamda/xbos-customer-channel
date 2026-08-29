from __future__ import annotations

from dataclasses import dataclass

from ..catalog import (
    AvailabilityState,
    CatalogProjection,
    CommercialQuoteSnapshot,
    InteractionCart,
    QuoteChange,
    QuoteChangeKind,
    QuoteReview,
)
from ..entry_context import ResolvedEntryContext
from ..ports import XBOSCatalogPort


class ReconfirmationRequired(RuntimeError):
    pass


class QuoteUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogSession:
    entry: ResolvedEntryContext
    projection: CatalogProjection
    cart: InteractionCart


class CatalogQuoteService:
    """Projects XBOS commerce truth and keeps only non-authoritative interaction cart state."""

    def __init__(self, catalog: XBOSCatalogPort) -> None:
        self._catalog = catalog

    def browse(self, entry: ResolvedEntryContext) -> CatalogSession:
        projection = self._catalog.get_catalog(
            merchant_ref=entry.merchant_ref,
            location_ref=entry.location_ref,
        )
        if projection.merchant_ref != entry.merchant_ref or projection.location_ref != entry.location_ref:
            raise RuntimeError("catalog_context_mismatch")
        return CatalogSession(entry=entry, projection=projection, cart=InteractionCart())

    @staticmethod
    def add_to_cart(
        session: CatalogSession,
        *,
        item_ref: str,
        quantity: int = 1,
        option_refs: tuple[str, ...] = (),
    ) -> CatalogSession:
        # Lookup is presentation validation only; cart deliberately stores no price/availability authority.
        session.projection.item(item_ref)
        return CatalogSession(
            entry=session.entry,
            projection=session.projection,
            cart=session.cart.add(item_ref, quantity=quantity, option_refs=option_refs),
        )

    def review_for_confirmation(self, session: CatalogSession, *, now_epoch: int) -> QuoteReview:
        quote = self._catalog.resolve_quote(
            merchant_ref=session.entry.merchant_ref,
            location_ref=session.entry.location_ref,
            cart=session.cart,
            now_epoch=now_epoch,
        )
        if quote.merchant_ref != session.entry.merchant_ref or quote.location_ref != session.entry.location_ref:
            raise RuntimeError("quote_context_mismatch")

        quoted_by_ref = {line.item_ref: line for line in quote.lines}
        changes: list[QuoteChange] = []
        can_continue = True

        for cart_line in session.cart.lines:
            displayed = session.projection.item(cart_line.item_ref)
            quoted = quoted_by_ref.get(cart_line.item_ref)
            if quoted is None:
                changes.append(
                    QuoteChange(
                        QuoteChangeKind.ITEM_MISSING,
                        cart_line.item_ref,
                        "present",
                        "missing",
                    )
                )
                can_continue = False
                continue
            if displayed.display_price != quoted.unit_price:
                changes.append(
                    QuoteChange(
                        QuoteChangeKind.PRICE_CHANGED,
                        cart_line.item_ref,
                        str(displayed.display_price),
                        str(quoted.unit_price),
                    )
                )
            if displayed.availability != quoted.availability:
                changes.append(
                    QuoteChange(
                        QuoteChangeKind.AVAILABILITY_CHANGED,
                        cart_line.item_ref,
                        displayed.availability.value,
                        quoted.availability.value,
                    )
                )
            if quoted.availability is not AvailabilityState.AVAILABLE:
                can_continue = False

        return QuoteReview(
            quote=quote,
            changes=tuple(changes),
            requires_reconfirmation=bool(changes),
            can_continue=can_continue,
        )

    @staticmethod
    def accept_review(review: QuoteReview, *, acknowledged_quote_ref: str) -> CommercialQuoteSnapshot:
        if not review.can_continue:
            raise QuoteUnavailable("authoritative_quote_not_available")
        if review.requires_reconfirmation and acknowledged_quote_ref != review.quote.quote_ref:
            raise ReconfirmationRequired("latest_quote_must_be_explicitly_acknowledged")
        return review.quote

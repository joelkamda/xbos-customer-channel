from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from ..models import EntryContext, MenuItem, OrderRef, Quote, Receipt


class FakeXBOSCommerceClient:
    """Contract fixture. Simulates XBOS responses; it is not channel-owned business truth."""

    def __init__(self) -> None:
        self.context = EntryContext("merchant:wnd", "location:logpom", "dine_in", "table:2")
        self.menu = (
            MenuItem("item:eru", "Eru", Decimal("3000"), "XAF"),
            MenuItem("item:ndole", "Ndolè", Decimal("3000"), "XAF"),
        )
        self._orders_by_key: dict[str, OrderRef] = {}

    def resolve_entry_context(self, entry_ref: str) -> EntryContext:
        if entry_ref != "qr:wnd:logpom:table2":
            raise KeyError(entry_ref)
        return self.context

    def get_menu(self, context: EntryContext) -> Sequence[MenuItem]:
        return self.menu

    def resolve_quote(self, context: EntryContext, item_refs: Sequence[str]) -> Quote:
        selected = [item for item in self.menu if item.item_ref in set(item_refs)]
        if not selected:
            raise KeyError("no_items")
        return Quote("quote:fixture:1", context.merchant_ref, sum((i.display_price for i in selected), Decimal("0")), "XAF", tuple(i.item_ref for i in selected))

    def open_order(self, quote: Quote, idempotency_key: str) -> OrderRef:
        if idempotency_key not in self._orders_by_key:
            self._orders_by_key[idempotency_key] = OrderRef("order:fixture:1", "draft", quote.quote_ref)
        return self._orders_by_key[idempotency_key]

    def update_order(self, order_ref: str, item_refs: Sequence[str], idempotency_key: str) -> OrderRef:
        return OrderRef(order_ref, "draft", "quote:fixture:updated")

    def confirm_order(self, order_ref: str, idempotency_key: str) -> OrderRef:
        return OrderRef(order_ref, "confirmed_pending_payment", "quote:fixture:1")

    def get_order_status(self, order_ref: str) -> OrderRef:
        return OrderRef(order_ref, "confirmed_paid", "quote:fixture:1")

    def get_receipt(self, order_ref: str) -> Receipt:
        return Receipt("receipt:fixture:1", order_ref, Decimal("3000"), "XAF")

    def request_cancel_or_correction(self, order_ref: str, reason: str, idempotency_key: str) -> OrderRef:
        return OrderRef(order_ref, "correction_requested", "quote:fixture:1")

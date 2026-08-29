from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import OrderRef, PaymentState, Receipt
from ..ports import PaymentPort, TransportPort, XBOSCommercePort


@dataclass(frozen=True, slots=True)
class JourneyOutcome:
    order: OrderRef
    payment_state: PaymentState
    receipt: Receipt


class CustomerJourneyService:
    """Orchestrates a customer journey without owning business or monetary truth."""

    def __init__(self, commerce: XBOSCommercePort, payments: PaymentPort, transport: TransportPort) -> None:
        self._commerce = commerce
        self._payments = payments
        self._transport = transport

    def run_confirm_and_pay(
        self,
        *,
        customer_ref: str,
        entry_ref: str,
        item_refs: Sequence[str],
        correlation_ref: str,
    ) -> JourneyOutcome:
        context = self._commerce.resolve_entry_context(entry_ref)
        self._commerce.get_menu(context)
        quote = self._commerce.resolve_quote(context, item_refs)
        order = self._commerce.open_order(quote, f"{correlation_ref}:order:open")
        order = self._commerce.confirm_order(order.order_ref, f"{correlation_ref}:order:confirm")

        payment = self._payments.create_payment_request(quote, f"{correlation_ref}:payment:create")
        state = self._payments.get_payment_status(payment.payment_ref)
        if state is not PaymentState.SUCCEEDED:
            self._transport.send_message(
                customer_ref,
                "Payment is still pending. Your order has not been marked paid. You can safely check again.",
                f"{correlation_ref}:msg:pending",
            )
            raise RuntimeError("payment_not_succeeded")

        authoritative_order = self._commerce.get_order_status(order.order_ref)
        receipt = self._commerce.get_receipt(authoritative_order.order_ref)
        self._transport.send_message(
            customer_ref,
            f"Payment confirmed. Receipt {receipt.receipt_ref}.",
            f"{correlation_ref}:msg:receipt",
        )
        return JourneyOutcome(authoritative_order, state, receipt)

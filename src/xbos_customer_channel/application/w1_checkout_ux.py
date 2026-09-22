"""Customer-facing WhatsApp checkout presentation over injected domain boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from ..application.catalog_service import CatalogSession
from ..order import (
    AuthoritativeOrderConfirmationSnapshot,
    CanonicalOrderProjection,
    ServiceMode,
)
from ..payment_experience import PaymentMethodCode
from ..session_state import CustomerSessionSnapshot


class W1CheckoutViewKind(StrEnum):
    TEXT = "text"
    BUTTONS = "buttons"
    LIST = "list"


class W1CheckoutStage(StrEnum):
    CART_REVIEW = "cart_review"
    SERVICE_MODE = "service_mode"
    ORDER_REVIEW = "order_review"
    SUBMITTED_ORDER = "submitted_order"
    PAYMENT_OPTIONS = "payment_options"
    METHOD_SELECTED = "method_selected"
    ERROR = "error"


class W1CheckoutFailureCode(StrEnum):
    ORDER_NOT_READY = "order_not_ready"
    ORDER_CONFIRMATION_EXPIRED_OR_CHANGED = "order_confirmation_expired_or_changed"
    PAYMENT_OPTIONS_UNAVAILABLE = "payment_options_unavailable"
    CONTEXT_MISMATCH = "context_mismatch"
    UNKNOWN_ORDER_RESULT = "unknown_order_result"
    UNKNOWN_PAYMENT_REQUEST_RESULT = "unknown_payment_request_result"
    UNSUPPORTED_METHOD = "unsupported_method"


class W1CheckoutDomainError(RuntimeError):
    def __init__(self, code: W1CheckoutFailureCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class W1CheckoutButton:
    reply_id: str
    title: str


@dataclass(frozen=True, slots=True)
class W1CheckoutListRow:
    reply_id: str
    title: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class W1CheckoutView:
    kind: W1CheckoutViewKind
    body: str
    buttons: tuple[W1CheckoutButton, ...] = ()
    list_button_label: str | None = None
    list_rows: tuple[W1CheckoutListRow, ...] = ()


@dataclass(frozen=True, slots=True)
class W1CustomerSafePaymentOptions:
    """C3-safe presentation shape only; this is not payment execution truth."""

    payment_request_ref: str
    order_ref: str
    canonical_amount: Decimal
    currency: str
    permitted_methods: tuple[PaymentMethodCode, ...]
    payment_request_state: str = "open"
    safe_next_action: str = "present_permitted_methods_only"

    def __post_init__(self) -> None:
        allowed = {
            PaymentMethodCode.PAY_AT_COUNTER,
            PaymentMethodCode.MTN_MOBILE_MONEY,
            PaymentMethodCode.ORANGE_MONEY,
        }
        if not self.payment_request_ref or not self.order_ref:
            raise ValueError("payment_options_reference_required")
        if not self.currency:
            raise ValueError("payment_options_currency_required")
        if self.payment_request_state != "open":
            raise ValueError("unsupported_payment_request_state")
        if self.safe_next_action != "present_permitted_methods_only":
            raise ValueError("unsupported_safe_next_action")
        if not self.permitted_methods or any(method not in allowed for method in self.permitted_methods):
            raise ValueError("unsupported_payment_method_projection")
        if len(set(self.permitted_methods)) != len(self.permitted_methods):
            raise ValueError("duplicate_payment_method_projection")


@dataclass(frozen=True, slots=True)
class W1CheckoutState:
    stage: W1CheckoutStage
    service_mode: ServiceMode | None = None
    confirmation: AuthoritativeOrderConfirmationSnapshot | None = None
    order: CanonicalOrderProjection | None = None
    payment_options: W1CustomerSafePaymentOptions | None = None
    selected_method: PaymentMethodCode | None = None


@dataclass(frozen=True, slots=True)
class W1CheckoutResult:
    view: W1CheckoutView
    state: W1CheckoutState


class W1CheckoutDomainBoundary(Protocol):
    """Injected authority boundary. U1 owns presentation, not order or payment truth."""

    def prepare_order_review(
        self,
        *,
        session: CustomerSessionSnapshot,
        catalog_session: CatalogSession,
        service_mode: ServiceMode,
    ) -> AuthoritativeOrderConfirmationSnapshot: ...

    def submit_order(
        self,
        *,
        session: CustomerSessionSnapshot,
        confirmation: AuthoritativeOrderConfirmationSnapshot,
    ) -> CanonicalOrderProjection: ...

    def payment_options(
        self,
        *,
        session: CustomerSessionSnapshot,
        order: CanonicalOrderProjection,
    ) -> W1CustomerSafePaymentOptions: ...


_METHOD_LABELS = {
    PaymentMethodCode.PAY_AT_COUNTER: "Pay at counter",
    PaymentMethodCode.MTN_MOBILE_MONEY: "MTN Mobile Money",
    PaymentMethodCode.ORANGE_MONEY: "Orange Money",
}


class W1CheckoutUX:
    """WhatsApp checkout UX that stops after customer payment-method selection."""

    def __init__(self, boundary: W1CheckoutDomainBoundary) -> None:
        self._boundary = boundary

    def cart_review(self, catalog_session: CatalogSession) -> W1CheckoutResult:
        if not catalog_session.cart.lines:
            return self.failure(W1CheckoutFailureCode.ORDER_NOT_READY)

        line_text: list[str] = []
        for line in catalog_session.cart.lines:
            try:
                item = catalog_session.projection.item(line.item_ref)
            except KeyError:
                return self.failure(W1CheckoutFailureCode.ORDER_NOT_READY)
            line_text.append(f"{line.quantity} Ã— {item.name}")

        body = "Your cart\n" + "\n".join(line_text) + "\n\nContinue to review service and the current authoritative total."
        return W1CheckoutResult(
            W1CheckoutView(
                W1CheckoutViewKind.BUTTONS,
                body,
                (
                    W1CheckoutButton("checkout", "Checkout"),
                    W1CheckoutButton("menu", "Menu"),
                ),
            ),
            W1CheckoutState(W1CheckoutStage.CART_REVIEW),
        )

    def service_mode(self, catalog_session: CatalogSession) -> W1CheckoutResult:
        allowed = set(catalog_session.entry.projection.allowed_fulfillment_modes)
        buttons: list[W1CheckoutButton] = []
        for mode, title in (
            (ServiceMode.DINE_IN, "Dine in"),
            (ServiceMode.TAKEAWAY, "Takeaway"),
            (ServiceMode.DELIVERY, "Delivery"),
        ):
            if mode.value in allowed:
                buttons.append(W1CheckoutButton(f"service:{mode.value}", title))
        if not buttons:
            return self.failure(W1CheckoutFailureCode.CONTEXT_MISMATCH)
        return W1CheckoutResult(
            W1CheckoutView(
                W1CheckoutViewKind.BUTTONS,
                "How would you like to receive your order?",
                tuple(buttons),
            ),
            W1CheckoutState(W1CheckoutStage.SERVICE_MODE),
        )

    def select_service_mode(
        self,
        *,
        session: CustomerSessionSnapshot,
        catalog_session: CatalogSession,
        service_mode: ServiceMode,
    ) -> W1CheckoutResult:
        if service_mode is ServiceMode.DINE_IN and not session.table_ref:
            return self.failure(W1CheckoutFailureCode.CONTEXT_MISMATCH)
        if service_mode.value not in catalog_session.entry.projection.allowed_fulfillment_modes:
            return self.failure(W1CheckoutFailureCode.CONTEXT_MISMATCH)
        try:
            confirmation = self._boundary.prepare_order_review(
                session=session,
                catalog_session=catalog_session,
                service_mode=service_mode,
            )
        except W1CheckoutDomainError as exc:
            return self.failure(exc.code)

        body_lines = [
            "Review your order",
            f"Service: {self._service_label(service_mode)}",
        ]
        for line in confirmation.lines:
            body_lines.append(f"{line.quantity} Ã— {line.item_ref}: {line.line_total} {confirmation.currency}")
        if confirmation.delivery_fee is not None:
            body_lines.append(f"Delivery fee: {confirmation.delivery_fee} {confirmation.currency}")
        for charge in confirmation.taxes_charges:
            body_lines.append(f"{charge.label}: {charge.amount} {confirmation.currency}")
        body_lines.append(f"Total: {confirmation.total} {confirmation.currency}")
        body_lines.append("Confirming the order does not mean it is paid.")
        return W1CheckoutResult(
            W1CheckoutView(
                W1CheckoutViewKind.BUTTONS,
                "\n".join(body_lines),
                (
                    W1CheckoutButton("order:confirm", "Confirm order"),
                    W1CheckoutButton("order:change", "Change"),
                ),
            ),
            W1CheckoutState(
                W1CheckoutStage.ORDER_REVIEW,
                service_mode=service_mode,
                confirmation=confirmation,
            ),
        )

    def confirm_order(
        self,
        *,
        session: CustomerSessionSnapshot,
        state: W1CheckoutState | None,
    ) -> W1CheckoutResult:
        if state is None or state.confirmation is None:
            return self.failure(W1CheckoutFailureCode.ORDER_NOT_READY)
        try:
            order = self._boundary.submit_order(session=session, confirmation=state.confirmation)
        except W1CheckoutDomainError as exc:
            return self.failure(exc.code)

        try:
            options = self._boundary.payment_options(session=session, order=order)
        except W1CheckoutDomainError as exc:
            return self.failure(exc.code)

        if options.order_ref != order.order_ref:
            return self.failure(W1CheckoutFailureCode.CONTEXT_MISMATCH)

        rows = tuple(
            W1CheckoutListRow(f"pay:{method.value}", _METHOD_LABELS[method])
            for method in options.permitted_methods
        )
        return W1CheckoutResult(
            W1CheckoutView(
                W1CheckoutViewKind.LIST,
                (
                    f"Order submitted: {order.order_ref}\n"
                    f"Amount to pay: {options.canonical_amount} {options.currency}\n"
                    "Payment options are available. Choose a method."
                ),
                list_button_label="Payment methods",
                list_rows=rows,
            ),
            W1CheckoutState(
                W1CheckoutStage.PAYMENT_OPTIONS,
                service_mode=state.service_mode,
                confirmation=state.confirmation,
                order=order,
                payment_options=options,
            ),
        )

    def select_payment_method(
        self,
        *,
        state: W1CheckoutState | None,
        method: PaymentMethodCode,
    ) -> W1CheckoutResult:
        if state is None or state.payment_options is None:
            return self.failure(W1CheckoutFailureCode.PAYMENT_OPTIONS_UNAVAILABLE)
        if method not in state.payment_options.permitted_methods:
            return self.failure(W1CheckoutFailureCode.UNSUPPORTED_METHOD)

        label = _METHOD_LABELS.get(method)
        if label is None:
            return self.failure(W1CheckoutFailureCode.UNSUPPORTED_METHOD)

        if method is PaymentMethodCode.PAY_AT_COUNTER:
            body = (
                "Pay at counter selected. Pay at the merchant counter when instructed. "
                "This selection does not mean the order is paid."
            )
        else:
            body = (
                f"{label} selected. Your payment method is selected. "
                "The secure payment execution step has not started yet."
            )
        return W1CheckoutResult(
            W1CheckoutView(W1CheckoutViewKind.TEXT, body),
            W1CheckoutState(
                W1CheckoutStage.METHOD_SELECTED,
                service_mode=state.service_mode,
                confirmation=state.confirmation,
                order=state.order,
                payment_options=state.payment_options,
                selected_method=method,
            ),
        )

    @staticmethod
    def failure(code: W1CheckoutFailureCode) -> W1CheckoutResult:
        messages = {
            W1CheckoutFailureCode.ORDER_NOT_READY: "Your order is not ready for checkout yet. Review your cart and try again.",
            W1CheckoutFailureCode.ORDER_CONFIRMATION_EXPIRED_OR_CHANGED: "Your order details changed or expired. Please review the latest order before confirming.",
            W1CheckoutFailureCode.PAYMENT_OPTIONS_UNAVAILABLE: "Payment options are temporarily unavailable. You have not been charged.",
            W1CheckoutFailureCode.CONTEXT_MISMATCH: "We could not safely match this checkout to your current order. Please return to the menu or ask for help.",
            W1CheckoutFailureCode.UNKNOWN_ORDER_RESULT: "We are checking your order. Do not submit a second order.",
            W1CheckoutFailureCode.UNKNOWN_PAYMENT_REQUEST_RESULT: "We are checking your payment options. Do not start a second payment request.",
            W1CheckoutFailureCode.UNSUPPORTED_METHOD: "That payment method is not available for this order. Choose one of the offered methods.",
        }
        return W1CheckoutResult(
            W1CheckoutView(W1CheckoutViewKind.TEXT, messages[code]),
            W1CheckoutState(W1CheckoutStage.ERROR),
        )

    @staticmethod
    def _service_label(mode: ServiceMode) -> str:
        return {
            ServiceMode.DINE_IN: "Dine in",
            ServiceMode.TAKEAWAY: "Takeaway",
            ServiceMode.DELIVERY: "Delivery",
        }[mode]

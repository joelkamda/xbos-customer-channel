"""Bounded W1 customer conversation rendering over existing channel primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..catalog import CatalogItemProjection
from ..order import ServiceMode
from ..payment_experience import PaymentMethodCode
from ..session_state import ChannelState, CustomerSessionSnapshot
from ..transports.meta_whatsapp import InteractionKind, NormalizedInboundMessage
from .catalog_service import CatalogQuoteService, CatalogSession
from .w1_checkout_ux import (
    W1CheckoutResult,
    W1CheckoutState,
    W1CheckoutUX,
    W1CheckoutViewKind,
)


class W1RenderKind(StrEnum):
    TEXT = "text"
    BUTTONS = "buttons"
    LIST = "list"


@dataclass(frozen=True, slots=True)
class W1Button:
    reply_id: str
    title: str


@dataclass(frozen=True, slots=True)
class W1ListRow:
    reply_id: str
    title: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class W1ListSection:
    title: str
    rows: tuple[W1ListRow, ...]


@dataclass(frozen=True, slots=True)
class W1RenderedMessage:
    kind: W1RenderKind
    body: str
    buttons: tuple[W1Button, ...] = ()
    list_button_label: str | None = None
    list_sections: tuple[W1ListSection, ...] = ()


@dataclass(frozen=True, slots=True)
class W1NavigationCursor:
    """Ephemeral render cursor only; identity, session and cart remain existing primitives."""

    active_section_ref: str | None = None
    active_item_ref: str | None = None
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class W1ConversationResult:
    rendered: W1RenderedMessage
    catalog_session: CatalogSession
    navigation: W1NavigationCursor
    checkout: W1CheckoutState | None = None


class W1ConversationRouter:
    """W1 customer navigation and checkout presentation; never order/payment authority."""

    def __init__(self, catalog: CatalogQuoteService, checkout: W1CheckoutUX | None = None) -> None:
        self._catalog = catalog
        self._checkout = checkout

    def route(
        self,
        *,
        session: CustomerSessionSnapshot,
        catalog_session: CatalogSession,
        navigation: W1NavigationCursor,
        inbound: NormalizedInboundMessage,
        checkout_state: W1CheckoutState | None = None,
    ) -> W1ConversationResult:
        self._assert_server_session(session)
        action = self._action(inbound)

        if action in {"start", "menu", "restart"}:
            return self._root(catalog_session)
        if action == "help":
            return W1ConversationResult(
                W1RenderedMessage(
                    W1RenderKind.BUTTONS,
                    "Choose Menu to browse, or Restart to return here.",
                    (W1Button("menu", "Menu"), W1Button("restart", "Restart")),
                ),
                catalog_session,
                navigation,
                checkout_state,
            )
        if action == "back":
            return self._back(catalog_session, navigation)
        if action.startswith("section:"):
            return self._section(catalog_session, action.removeprefix("section:"))
        if action.startswith("item:"):
            return self._item(catalog_session, action.removeprefix("item:"))
        if action.startswith("qty:"):
            return self._quantity(catalog_session, navigation, action.removeprefix("qty:"))
        if action == "add":
            return self._add(catalog_session, navigation)

        if action == "cart":
            checkout = self._checkout_or_none()
            if checkout is None:
                return self._unsupported(catalog_session)
            return self._from_checkout(checkout.cart_review(catalog_session), catalog_session, navigation)

        if action == "checkout":
            checkout = self._checkout_or_none()
            if checkout is None:
                return self._unsupported(catalog_session)
            return self._from_checkout(checkout.service_mode(catalog_session), catalog_session, navigation)

        if action.startswith("service:"):
            checkout = self._checkout_or_none()
            if checkout is None:
                return self._unsupported(catalog_session)
            try:
                mode = ServiceMode(action.removeprefix("service:"))
            except ValueError:
                return self._unsupported(catalog_session)
            result = checkout.select_service_mode(
                session=session,
                catalog_session=catalog_session,
                service_mode=mode,
            )
            return self._from_checkout(result, catalog_session, navigation)

        if action == "order:confirm":
            checkout = self._checkout_or_none()
            if checkout is None:
                return self._unsupported(catalog_session)
            return self._from_checkout(
                checkout.confirm_order(session=session, state=checkout_state),
                catalog_session,
                navigation,
            )

        if action == "order:change":
            checkout = self._checkout_or_none()
            if checkout is None:
                return self._unsupported(catalog_session)
            return self._from_checkout(checkout.cart_review(catalog_session), catalog_session, navigation)

        if action.startswith("pay:"):
            checkout = self._checkout_or_none()
            if checkout is None:
                return self._unsupported(catalog_session)
            try:
                method = PaymentMethodCode(action.removeprefix("pay:"))
            except ValueError:
                return self._unsupported(catalog_session)
            return self._from_checkout(
                checkout.select_payment_method(state=checkout_state, method=method),
                catalog_session,
                navigation,
            )

        return W1ConversationResult(
            W1RenderedMessage(
                W1RenderKind.TEXT,
                "I did not understand that. Reply Menu, Help, or Restart.",
            ),
            catalog_session,
            navigation,
            checkout_state,
        )

    @staticmethod
    def _assert_server_session(session: CustomerSessionSnapshot) -> None:
        permitted_states = {
            ChannelState.BROWSING,
            ChannelState.CART,
            ChannelState.SERVICE_MODE,
            ChannelState.REVIEW,
            ChannelState.ORDER_SUBMITTING,
            ChannelState.ORDER_CREATED,
            ChannelState.PAYMENT_METHOD,
        }
        if (
            not session.security_binding_complete
            or not session.session_ref
            or session.state not in permitted_states
        ):
            raise PermissionError("w1_server_session_binding_required")

    @staticmethod
    def _action(inbound: NormalizedInboundMessage) -> str:
        if inbound.interactive_reply is not None:
            if inbound.interactive_reply.kind not in {
                InteractionKind.BUTTON_REPLY,
                InteractionKind.LIST_REPLY,
            }:
                return "unsupported"
            return inbound.interactive_reply.reply_id
        if inbound.text is None:
            return "unsupported"
        return inbound.text.strip().casefold()

    def _root(self, catalog_session: CatalogSession) -> W1ConversationResult:
        categories = tuple(
            W1ListRow(f"section:{section.section_ref}", section.name)
            for section in sorted(
                catalog_session.projection.sections,
                key=lambda section: section.sort_order,
            )
        )
        return W1ConversationResult(
            W1RenderedMessage(
                W1RenderKind.LIST,
                "Welcome. Choose a menu category.",
                list_button_label="Menu",
                list_sections=(W1ListSection("Categories", categories),),
            ),
            catalog_session,
            W1NavigationCursor(),
        )

    def _section(
        self,
        catalog_session: CatalogSession,
        section_ref: str,
    ) -> W1ConversationResult:
        section = next(
            (
                section
                for section in catalog_session.projection.sections
                if section.section_ref == section_ref
            ),
            None,
        )
        if section is None:
            return self._unsupported(catalog_session)
        rows = tuple(
            W1ListRow(f"item:{item.item_ref}", item.name, item.description)
            for item in catalog_session.projection.items
            if item.section_ref == section_ref
        )
        if not rows:
            return self._unsupported(catalog_session)
        return W1ConversationResult(
            W1RenderedMessage(
                W1RenderKind.LIST,
                f"{section.name}: choose an item.",
                list_button_label="Items",
                list_sections=(W1ListSection(section.name, rows),),
            ),
            catalog_session,
            W1NavigationCursor(active_section_ref=section_ref),
        )

    def _item(
        self,
        catalog_session: CatalogSession,
        item_ref: str,
    ) -> W1ConversationResult:
        try:
            item = catalog_session.projection.item(item_ref)
        except KeyError:
            return self._unsupported(catalog_session)
        return self._product(
            catalog_session,
            item,
            W1NavigationCursor(
                active_section_ref=item.section_ref,
                active_item_ref=item_ref,
            ),
        )

    def _quantity(
        self,
        catalog_session: CatalogSession,
        navigation: W1NavigationCursor,
        value: str,
    ) -> W1ConversationResult:
        if navigation.active_item_ref is None or value not in {"1", "2", "3"}:
            return self._unsupported(catalog_session)
        try:
            item = catalog_session.projection.item(navigation.active_item_ref)
        except KeyError:
            return self._unsupported(catalog_session)
        return self._product(
            catalog_session,
            item,
            W1NavigationCursor(
                navigation.active_section_ref,
                item.item_ref,
                int(value),
            ),
        )

    def _add(
        self,
        catalog_session: CatalogSession,
        navigation: W1NavigationCursor,
    ) -> W1ConversationResult:
        if navigation.active_item_ref is None:
            return self._unsupported(catalog_session)
        try:
            updated = self._catalog.add_to_cart(
                catalog_session,
                item_ref=navigation.active_item_ref,
                quantity=navigation.quantity,
            )
            item = updated.projection.item(navigation.active_item_ref)
        except (KeyError, ValueError):
            return self._unsupported(catalog_session)
        return W1ConversationResult(
            W1RenderedMessage(
                W1RenderKind.BUTTONS,
                f"Added {navigation.quantity} Ã— {item.name}. What next?",
                (
                    W1Button("cart", "View cart"),
                    W1Button("menu", "Menu"),
                    W1Button("back", "Back"),
                ),
            ),
            updated,
            navigation,
        )

    def _back(
        self,
        catalog_session: CatalogSession,
        navigation: W1NavigationCursor,
    ) -> W1ConversationResult:
        if navigation.active_item_ref is not None and navigation.active_section_ref is not None:
            return self._section(catalog_session, navigation.active_section_ref)
        return self._root(catalog_session)

    def _product(
        self,
        catalog_session: CatalogSession,
        item: CatalogItemProjection,
        navigation: W1NavigationCursor,
    ) -> W1ConversationResult:
        return W1ConversationResult(
            W1RenderedMessage(
                W1RenderKind.BUTTONS,
                (
                    f"{item.name}: {item.description}\n"
                    f"Displayed price: {item.display_price} {item.currency}\n"
                    f"Quantity: {navigation.quantity}"
                ),
                (
                    W1Button("qty:1", "1"),
                    W1Button("qty:2", "2"),
                    W1Button("add", "Add to cart"),
                ),
            ),
            catalog_session,
            navigation,
        )

    def _unsupported(self, catalog_session: CatalogSession) -> W1ConversationResult:
        return W1ConversationResult(
            W1RenderedMessage(
                W1RenderKind.TEXT,
                "That selection is no longer available. Reply Menu to browse safely.",
            ),
            catalog_session,
            W1NavigationCursor(),
        )

    def _checkout_or_none(self) -> W1CheckoutUX | None:
        return self._checkout

    @staticmethod
    def _from_checkout(
        checkout_result: W1CheckoutResult,
        catalog_session: CatalogSession,
        navigation: W1NavigationCursor,
    ) -> W1ConversationResult:
        view = checkout_result.view
        if view.kind is W1CheckoutViewKind.TEXT:
            rendered = W1RenderedMessage(W1RenderKind.TEXT, view.body)
        elif view.kind is W1CheckoutViewKind.BUTTONS:
            rendered = W1RenderedMessage(
                W1RenderKind.BUTTONS,
                view.body,
                tuple(W1Button(button.reply_id, button.title) for button in view.buttons),
            )
        else:
            rendered = W1RenderedMessage(
                W1RenderKind.LIST,
                view.body,
                list_button_label=view.list_button_label or "Options",
                list_sections=(
                    W1ListSection(
                        "Options",
                        tuple(
                            W1ListRow(row.reply_id, row.title, row.description)
                            for row in view.list_rows
                        ),
                    ),
                ),
            )
        return W1ConversationResult(
            rendered,
            catalog_session,
            navigation,
            checkout_result.state,
        )

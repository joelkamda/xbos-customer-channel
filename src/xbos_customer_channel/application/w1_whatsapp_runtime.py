"""Application-level WhatsApp runtime composition for W1 customer conversations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..session_state import CustomerSessionSnapshot
from ..transports.meta_whatsapp import (
    InteractiveButton,
    InteractiveListRow,
    InteractiveListSection,
    MetaWhatsAppInboundAdapter,
    MetaWhatsAppOutboundAdapter,
    NormalizedInboundMessage,
    ProviderOutboundMessage,
)
from .catalog_service import CatalogSession
from .w1_checkout_ux import W1CheckoutState
from .w1_conversation import (
    W1ConversationRouter,
    W1NavigationCursor,
    W1RenderKind,
)


@dataclass(frozen=True, slots=True)
class W1RuntimeConversationState:
    session: CustomerSessionSnapshot
    catalog_session: CatalogSession
    navigation: W1NavigationCursor = W1NavigationCursor()
    checkout: W1CheckoutState | None = None


class W1RuntimeStatePort(Protocol):
    """Server-side state resolution; raw WhatsApp sender remains only a transport locator."""

    def load(self, inbound: NormalizedInboundMessage) -> W1RuntimeConversationState: ...

    def save(
        self,
        inbound: NormalizedInboundMessage,
        state: W1RuntimeConversationState,
    ) -> None: ...


class W1WhatsAppRuntime:
    """Verified Meta inbound -> Channel router -> Meta outbound composition."""

    def __init__(
        self,
        *,
        inbound: MetaWhatsAppInboundAdapter,
        outbound: MetaWhatsAppOutboundAdapter,
        router: W1ConversationRouter,
        state_port: W1RuntimeStatePort,
    ) -> None:
        self._inbound = inbound
        self._outbound = outbound
        self._router = router
        self._state_port = state_port

    def handle_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str | None,
    ) -> tuple[ProviderOutboundMessage, ...]:
        callback = self._inbound.receive(raw_body=raw_body, signature=signature)
        sent: list[ProviderOutboundMessage] = []
        for message in callback.messages:
            try:
                state = self._state_port.load(message)
                result = self._router.route(
                    session=state.session,
                    catalog_session=state.catalog_session,
                    navigation=state.navigation,
                    inbound=message,
                    checkout_state=state.checkout,
                )
                next_state = W1RuntimeConversationState(
                    session=state.session,
                    catalog_session=result.catalog_session,
                    navigation=result.navigation,
                    checkout=result.checkout,
                )
                self._state_port.save(message, next_state)
                sent.append(self._send(message, result.rendered))
            except Exception:
                sent.append(
                    self._outbound.send_text(
                        recipient=message.sender,
                        body=(
                            "We could not continue this request safely. "
                            "Please reply Menu or Help."
                        ),
                    )
                )
        return tuple(sent)

    def _send(self, inbound: NormalizedInboundMessage, rendered):
        if rendered.kind is W1RenderKind.TEXT:
            return self._outbound.send_text(
                recipient=inbound.sender,
                body=rendered.body,
            )
        if rendered.kind is W1RenderKind.BUTTONS:
            return self._outbound.send_buttons(
                recipient=inbound.sender,
                body=rendered.body,
                buttons=tuple(
                    InteractiveButton(button.reply_id, button.title)
                    for button in rendered.buttons
                ),
            )
        sections = tuple(
            InteractiveListSection(
                section.title,
                tuple(
                    InteractiveListRow(row.reply_id, row.title, row.description)
                    for row in section.rows
                ),
            )
            for section in rendered.list_sections
        )
        return self._outbound.send_list(
            recipient=inbound.sender,
            body=rendered.body,
            button_label=rendered.list_button_label or "Options",
            sections=sections,
        )

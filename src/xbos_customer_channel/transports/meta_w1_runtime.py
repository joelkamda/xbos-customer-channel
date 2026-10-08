"""Meta WhatsApp adapter bridge for the provider-neutral W1 domain."""

from __future__ import annotations

from ..application.catalog_service import CatalogQuoteService
from ..application.outbound_intent import build_w1_outbound_intent
from ..application.w1_checkout_ux import W1CheckoutUX
from ..application.w1_conversation import (
    W1ConversationRouter,
    W1RenderKind,
)
from ..application.w1_whatsapp_runtime import (
    W1RuntimeConversationState,
    W1RuntimeStatePort,
)
from .meta_whatsapp import (
    InteractiveButton,
    InteractiveListRow,
    InteractiveListSection,
    MetaWhatsAppInboundAdapter,
    MetaWhatsAppOutboundAdapter,
    ProviderOutboundMessage,
    to_channel_inbound_event,
)


class W1WhatsAppRuntime:
    """Meta transport edge -> provider-neutral Channel event -> W1 domain."""

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
            reserve_outbound = getattr(self._state_port, "reserve_outbound", None)
            record_outcome = getattr(self._state_port, "record_outbound_outcome", None)
            durable_delivery = callable(reserve_outbound)
            reservation = None
            try:
                state = self._state_port.load(message)
                event = to_channel_inbound_event(
                    message,
                    conversation_ref=state.session.conversation_ref,
                    session_ref=state.session.session_ref,
                    merchant_ref=state.session.merchant_ref or "",
                    subject_ref=state.session.owner_identity_ref or "",
                    correlation_ref=state.session.correlation_ref,
                )
                result = self._router.route(
                    session=state.session,
                    catalog_session=state.catalog_session,
                    navigation=state.navigation,
                    inbound=event,
                    checkout_state=state.checkout,
                )
                next_state = W1RuntimeConversationState(
                    session=state.session,
                    catalog_session=result.catalog_session,
                    navigation=result.navigation,
                    checkout=result.checkout,
                )
                if durable_delivery:
                    intent = build_w1_outbound_intent(
                        event=event,
                        rendered=result.rendered,
                    )
                    reservation = reserve_outbound(
                        message,
                        intent,
                        render_ordinal=0,
                    )
                self._state_port.save(message, next_state)
                try:
                    provider_message = self._send(message.sender, result.rendered)
                except Exception as exc:
                    if reservation is not None and callable(record_outcome):
                        record_outcome(
                            reservation,
                            delivery_state="failed",
                            provider_message_ref=None,
                            provider_error_code=type(exc).__name__,
                        )
                    raise
                if reservation is not None and callable(record_outcome):
                    record_outcome(
                        reservation,
                        delivery_state="sent",
                        provider_message_ref=provider_message.provider_message_ref,
                        provider_error_code=None,
                    )
                sent.append(provider_message)
            except Exception:
                if durable_delivery:
                    # A durable runtime must never emit an unreserved fallback.
                    # Pending/failed delivery state is the reconciliation surface.
                    continue
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

    def _send(self, recipient, rendered):
        if rendered.kind is W1RenderKind.TEXT:
            return self._outbound.send_text(
                recipient=recipient,
                body=rendered.body,
            )
        if rendered.kind is W1RenderKind.BUTTONS:
            return self._outbound.send_buttons(
                recipient=recipient,
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
            recipient=recipient,
            body=rendered.body,
            button_label=rendered.list_button_label or "Options",
            sections=sections,
        )


def compose_w1_whatsapp_runtime(
    *,
    inbound: MetaWhatsAppInboundAdapter,
    outbound: MetaWhatsAppOutboundAdapter,
    catalog: CatalogQuoteService,
    checkout: W1CheckoutUX,
    state_port: W1RuntimeStatePort,
) -> W1WhatsAppRuntime:
    """Compose the Meta adapter around provider-neutral W1 domain behavior."""
    router = W1ConversationRouter(catalog, checkout=checkout)
    return W1WhatsAppRuntime(
        inbound=inbound,
        outbound=outbound,
        router=router,
        state_port=state_port,
    )

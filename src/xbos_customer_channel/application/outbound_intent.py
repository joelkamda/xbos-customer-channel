from __future__ import annotations

from ..channel_contract import ChannelInboundEvent, ChannelOutboundIntent
from .w1_conversation import W1RenderedMessage, W1RenderKind


def build_w1_outbound_intent(
    *,
    event: ChannelInboundEvent,
    rendered: W1RenderedMessage,
    purpose: str = "commerce_response",
) -> ChannelOutboundIntent:
    projection: dict[str, object] = {
        "kind": rendered.kind.value,
        "body": rendered.body,
    }
    if rendered.kind is W1RenderKind.BUTTONS:
        projection["buttons"] = tuple(
            {"reply_id": button.reply_id, "title": button.title}
            for button in rendered.buttons
        )
    elif rendered.kind is W1RenderKind.LIST:
        projection["list_button_label"] = rendered.list_button_label or "Options"
        projection["list_sections"] = tuple(
            {
                "title": section.title,
                "rows": tuple(
                    {
                        "reply_id": row.reply_id,
                        "title": row.title,
                        "description": row.description,
                    }
                    for row in section.rows
                ),
            }
            for section in rendered.list_sections
        )

    return ChannelOutboundIntent(
        recipient=event.sender_identity,
        channel=event.channel,
        merchant_ref=event.merchant_ref,
        conversation_ref=event.conversation_ref,
        session_ref=event.session_ref,
        purpose=purpose,
        event_type="w1_rendered_message",
        projection=projection,
        template_variables={},
        correlation_id=event.correlation_ref,
    )

from __future__ import annotations

from collections.abc import Mapping

from ..channel_contract import (
    ChannelCommand,
    ChannelCommandType,
    ChannelInboundEvent,
    build_channel_command,
)


def propose_w1_command(
    event: ChannelInboundEvent,
    *,
    interaction_context: Mapping[str, object] | None = None,
) -> ChannelCommand | None:
    """Deterministically map current W1 actions into proposed typed commands.

    This function interprets only the existing explicit W1 action vocabulary.
    It performs no authorization; CommandValidator remains the execution gate.
    """

    action = _action(event)
    context = dict(interaction_context or {})

    if action in {"start", "menu", "restart"}:
        command_type = ChannelCommandType.SHOW_CATALOG
        payload: dict[str, object] = {}
    elif action.startswith("item:"):
        command_type = ChannelCommandType.SHOW_ITEM
        payload = {"item_ref": action.removeprefix("item:")}
    elif action == "add":
        command_type = ChannelCommandType.ADD_LINE
        payload = {
            key: context[key]
            for key in ("item_ref", "quantity", "option_refs")
            if key in context
        }
    elif action == "cart":
        command_type = ChannelCommandType.SHOW_CART
        payload = {}
    elif action.startswith("service:"):
        command_type = ChannelCommandType.SET_FULFILLMENT
        payload = {"service_mode": action.removeprefix("service:")}
    elif action == "order:confirm":
        command_type = ChannelCommandType.SUBMIT_ORDER
        payload = {
            key: context[key]
            for key in ("confirmation_ref", "client_submit_ref")
            if key in context
        }
    elif action == "order:change":
        command_type = ChannelCommandType.SHOW_CART
        payload = {}
    else:
        return None

    return build_channel_command(
        event=event,
        command_type=command_type,
        payload=payload,
    )


def _action(event: ChannelInboundEvent) -> str:
    if event.message_type == "interactive":
        reply_id = event.payload.get("reply_id")
        return reply_id if isinstance(reply_id, str) else ""
    if event.message_type == "text":
        text = event.payload.get("text")
        return text.strip().casefold() if isinstance(text, str) else ""
    return ""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping


class ChannelCommandType(StrEnum):
    SHOW_CATALOG = "show_catalog"
    SHOW_ITEM = "show_item"
    ADD_LINE = "add_line"
    REMOVE_LINE = "remove_line"
    SET_QUANTITY = "set_quantity"
    SET_FULFILLMENT = "set_fulfillment"
    SHOW_CART = "show_cart"
    SUBMIT_ORDER = "submit_order"
    GET_ORDER_STATUS = "get_order_status"
    CANCEL_DRAFT = "cancel_draft"
    REQUEST_HUMAN = "request_human"


class ControlOwner(StrEnum):
    AUTOMATION = "automation"
    HUMAN = "human"


class HandoffPhase(StrEnum):
    HANDOFF_REQUESTED = "handoff_requested"
    HUMAN_ACTIVE = "human_active"
    AUTOMATED_RESUME = "automated_resume"


@dataclass(frozen=True, slots=True)
class HandoffControlState:
    phase: HandoffPhase
    control_owner: ControlOwner
    handoff_ref: str

    def __post_init__(self) -> None:
        if not self.handoff_ref.strip():
            raise ValueError("handoff_ref_required")


MUTATING_COMMANDS = frozenset(
    {
        ChannelCommandType.ADD_LINE,
        ChannelCommandType.REMOVE_LINE,
        ChannelCommandType.SET_QUANTITY,
        ChannelCommandType.SET_FULFILLMENT,
        ChannelCommandType.SUBMIT_ORDER,
        ChannelCommandType.CANCEL_DRAFT,
    }
)

REAL_XBOS_WRITE_COMMANDS = frozenset(MUTATING_COMMANDS)


@dataclass(frozen=True, slots=True)
class ChannelInboundEvent:
    channel_event_id: str
    provider_message_id: str
    channel: str
    sender_identity: str
    recipient_identity: str
    conversation_ref: str
    session_ref: str
    merchant_ref: str
    subject_ref: str
    occurred_at: datetime
    message_type: str
    payload: Mapping[str, object]
    correlation_ref: str
    safe_provider_metadata: Mapping[str, str]

    def __post_init__(self) -> None:
        required = (
            self.channel_event_id,
            self.provider_message_id,
            self.channel,
            self.sender_identity,
            self.recipient_identity,
            self.conversation_ref,
            self.session_ref,
            self.merchant_ref,
            self.subject_ref,
            self.message_type,
            self.correlation_ref,
        )
        if any(not value.strip() for value in required):
            raise ValueError("channel_inbound_event_required_field_missing")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("channel_inbound_event_occurred_at_must_be_aware")


@dataclass(frozen=True, slots=True)
class ChannelCommand:
    command_id: str
    channel_event_id: str
    conversation_id: str
    session_ref: str
    merchant_ref: str
    subject_ref: str
    command_type: ChannelCommandType
    payload: Mapping[str, object]
    idempotency_key: str
    occurred_at: datetime
    correlation_ref: str

    def __post_init__(self) -> None:
        required = (
            self.command_id,
            self.channel_event_id,
            self.conversation_id,
            self.session_ref,
            self.merchant_ref,
            self.subject_ref,
            self.idempotency_key,
            self.correlation_ref,
        )
        if any(not value.strip() for value in required):
            raise ValueError("channel_command_required_field_missing")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("channel_command_occurred_at_must_be_aware")

    @property
    def mutating(self) -> bool:
        return self.command_type in MUTATING_COMMANDS

    @property
    def requires_real_xbos_write(self) -> bool:
        return self.command_type in REAL_XBOS_WRITE_COMMANDS


@dataclass(frozen=True, slots=True)
class CommandValidationResult:
    command: ChannelCommand
    valid: bool
    dispatchable: bool
    reason: str
    control_owner: ControlOwner

    @property
    def may_dispatch(self) -> bool:
        return self.valid and self.dispatchable


@dataclass(frozen=True, slots=True)
class ChannelOutboundIntent:
    recipient: str
    channel: str
    merchant_ref: str
    conversation_ref: str
    session_ref: str
    purpose: str
    event_type: str
    projection: Mapping[str, object]
    template_variables: Mapping[str, str]
    correlation_id: str

    def __post_init__(self) -> None:
        required = (
            self.recipient,
            self.channel,
            self.merchant_ref,
            self.conversation_ref,
            self.session_ref,
            self.purpose,
            self.event_type,
            self.correlation_id,
        )
        if any(not value.strip() for value in required):
            raise ValueError("channel_outbound_intent_required_field_missing")


def stable_channel_event_id(
    *,
    channel: str,
    recipient_identity: str,
    provider_message_id: str,
) -> str:
    raw = f"{channel.strip().lower()}\n{recipient_identity.strip()}\n{provider_message_id.strip()}".encode("utf-8")
    return "cevt_" + hashlib.sha256(raw).hexdigest()


def build_channel_command(
    *,
    event: ChannelInboundEvent,
    command_type: ChannelCommandType,
    payload: Mapping[str, object],
) -> ChannelCommand:
    canonical_payload = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(
        (
            event.channel_event_id
            + "\n"
            + command_type.value
            + "\n"
            + canonical_payload
        ).encode("utf-8")
    ).hexdigest()
    return ChannelCommand(
        command_id="ccmd_" + fingerprint,
        channel_event_id=event.channel_event_id,
        conversation_id=event.conversation_ref,
        session_ref=event.session_ref,
        merchant_ref=event.merchant_ref,
        subject_ref=event.subject_ref,
        command_type=command_type,
        payload=dict(payload),
        idempotency_key=f"channel-command:{event.channel_event_id}",
        occurred_at=event.occurred_at.astimezone(timezone.utc),
        correlation_ref=event.correlation_ref,
    )


def command_fingerprint(command: ChannelCommand) -> str:
    document = {
        "channel_event_id": command.channel_event_id,
        "command_type": command.command_type.value,
        "conversation_id": command.conversation_id,
        "merchant_ref": command.merchant_ref,
        "payload": dict(command.payload),
        "session_ref": command.session_ref,
        "subject_ref": command.subject_ref,
    }
    return hashlib.sha256(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def outbound_payload_digest(intent: ChannelOutboundIntent) -> str:
    document = {
        "channel": intent.channel,
        "conversation_ref": intent.conversation_ref,
        "correlation_id": intent.correlation_id,
        "event_type": intent.event_type,
        "merchant_ref": intent.merchant_ref,
        "projection": dict(intent.projection),
        "purpose": intent.purpose,
        "recipient": intent.recipient,
        "session_ref": intent.session_ref,
        "template_variables": dict(intent.template_variables),
    }
    return hashlib.sha256(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChannelSessionRecord:
    session_ref: str
    channel: str
    external_user_ref_hash: str
    correlation_ref: str
    presentation_locale: str | None = None
    human_handoff_state: str | None = None


@dataclass(frozen=True, slots=True)
class TransportDeliveryRecord:
    external_message_ref: str
    session_ref: str
    attempt: int
    delivery_state: str

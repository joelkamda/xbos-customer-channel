from __future__ import annotations

from dataclasses import dataclass

from ..identity import ChannelIdentityState, ConsentPurpose


@dataclass(frozen=True, slots=True)
class ChannelIdentityRecord:
    """Durable channel metadata only. Raw phone/address is intentionally absent."""

    identity_ref: str
    channel: str
    channel_user_ref_hash: str
    conversation_ref: str
    state: ChannelIdentityState
    linked_party_ref: str | None = None
    verification_ref: str | None = None
    restriction_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ConsentAuditRecord:
    consent_ref: str
    identity_ref: str
    purpose: ConsentPurpose
    granted: bool
    evidence_ref: str

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChannelIdentityState(StrEnum):
    ANONYMOUS = "anonymous"
    RECOGNIZED = "recognized"
    VERIFIED = "verified"
    LINKED = "linked"
    BLOCKED_OR_RESTRICTED = "blocked_or_restricted"


class ConsentPurpose(StrEnum):
    TRANSACTIONAL = "transactional"
    MARKETING = "marketing"
    SUPPORT = "support"
    ORDER_UPDATES = "order_updates"
    RECEIPT_DELIVERY = "receipt_delivery"


@dataclass(frozen=True, slots=True)
class PartyCandidate:
    """Opaque XBOS reference returned by a resolver; never proof of identity."""

    party_ref: str
    match_ref: str


@dataclass(frozen=True, slots=True)
class ChannelIdentity:
    identity_ref: str
    channel: str
    channel_user_ref: str
    conversation_ref: str
    state: ChannelIdentityState
    candidates: tuple[PartyCandidate, ...] = ()
    verification_ref: str | None = None
    linked_party_ref: str | None = None
    restriction_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    consent_ref: str
    identity_ref: str
    purpose: ConsentPurpose
    granted: bool
    evidence_ref: str

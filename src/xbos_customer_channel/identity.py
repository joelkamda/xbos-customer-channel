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
class ChannelSubjectResolution:
    """Server-resolved channel subject. Caller locators are never copied in as authority."""

    channel: str
    canonical_channel_subject_ref: str
    subject_attestation_ref: str
    expires_at_epoch: int
    candidate_party_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChannelIdentity:
    identity_ref: str
    channel: str
    canonical_channel_subject_ref: str
    subject_attestation_ref: str
    conversation_ref: str
    state: ChannelIdentityState
    candidates: tuple[PartyCandidate, ...] = ()
    verification_ref: str | None = None
    linked_party_ref: str | None = None
    linked_tenant_ref: str | None = None
    linked_merchant_ref: str | None = None
    linked_context_binding_ref: str | None = None
    restriction_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    consent_ref: str
    identity_ref: str
    purpose: ConsentPurpose
    granted: bool
    consent_version: str
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class IdentityBindingRecord:
    """Server-issued Channel session identity binding; never Party/customer truth."""

    identity_binding_ref: str
    identity_ref: str
    canonical_channel_subject_ref: str
    subject_attestation_ref: str
    conversation_ref: str
    tenant_ref: str | None
    merchant_ref: str
    location_ref: str
    table_ref: str | None
    dining_area_ref: str | None
    context_binding_ref: str
    issued_at_epoch: int
    expires_at_epoch: int

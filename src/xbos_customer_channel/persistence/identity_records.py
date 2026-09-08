from __future__ import annotations

import secrets
from dataclasses import dataclass
from threading import RLock

from ..identity import ChannelIdentityState, ConsentPurpose, IdentityBindingRecord


@dataclass(frozen=True, slots=True)
class ChannelIdentityRecord:
    """Durable Channel metadata only. Raw phone/address is intentionally absent."""

    identity_ref: str
    channel: str
    channel_user_ref_hash: str
    subject_attestation_ref: str
    last_conversation_ref: str
    state: ChannelIdentityState
    linked_party_ref: str | None = None
    linked_tenant_ref: str | None = None
    linked_merchant_ref: str | None = None
    linked_context_binding_ref: str | None = None
    verification_ref: str | None = None
    restriction_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ConsentAuditRecord:
    consent_ref: str
    identity_ref: str
    purpose: ConsentPurpose
    granted: bool
    evidence_ref: str
    consent_version: str = "legacy-unversioned"


class InMemoryIdentityBindingStore:
    """Single-process CR3 binding contract; not a production durable identity store."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, IdentityBindingRecord] = {}

    def issue_binding(
        self,
        *,
        identity_ref: str,
        canonical_channel_subject_ref: str,
        subject_attestation_ref: str,
        conversation_ref: str,
        tenant_ref: str | None,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        dining_area_ref: str | None,
        context_binding_ref: str,
        issued_at_epoch: int,
        expires_at_epoch: int,
    ) -> IdentityBindingRecord:
        if not identity_ref or not canonical_channel_subject_ref or not subject_attestation_ref:
            raise ValueError("identity_binding_subject_required")
        if not conversation_ref:
            raise ValueError("identity_binding_conversation_required")
        if not merchant_ref or not location_ref or not context_binding_ref:
            raise ValueError("identity_binding_context_required")
        if expires_at_epoch <= issued_at_epoch:
            raise ValueError("identity_binding_expiry_must_be_future")

        with self._lock:
            ref = "identity_binding_" + secrets.token_urlsafe(32)
            while ref in self._records:
                ref = "identity_binding_" + secrets.token_urlsafe(32)
            record = IdentityBindingRecord(
                identity_binding_ref=ref,
                identity_ref=identity_ref,
                canonical_channel_subject_ref=canonical_channel_subject_ref,
                subject_attestation_ref=subject_attestation_ref,
                conversation_ref=conversation_ref,
                tenant_ref=tenant_ref,
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                table_ref=table_ref,
                dining_area_ref=dining_area_ref,
                context_binding_ref=context_binding_ref,
                issued_at_epoch=issued_at_epoch,
                expires_at_epoch=expires_at_epoch,
            )
            self._records[ref] = record
            return record

    def resolve_binding(self, identity_binding_ref: str) -> IdentityBindingRecord | None:
        with self._lock:
            return self._records.get(identity_binding_ref)

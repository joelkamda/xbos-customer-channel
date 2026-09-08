from __future__ import annotations

from dataclasses import dataclass

from ..entry_context import ResolvedEntryContext
from ..identity import ChannelIdentity, ChannelIdentityState, ChannelSubjectResolution, ConsentPurpose, ConsentRecord, IdentityBindingRecord
from ..ports import ConsentPolicyPort, CustomerIdentityPort, IdentityBindingStorePort
from ..security.identity_refs import hash_channel_user_ref


@dataclass(frozen=True, slots=True)
class IdentityResolutionOutcome:
    identity: ChannelIdentity
    may_link: bool


class ChannelIdentityService:
    """Channel-owned identity orchestration; never XBOS Party/customer authority."""

    def __init__(
        self,
        identity_port: CustomerIdentityPort,
        *,
        consent_policy: ConsentPolicyPort,
        binding_store: IdentityBindingStorePort,
        identity_secret: str,
    ) -> None:
        if not identity_secret:
            raise ValueError("identity_secret_required")
        self._identity_port = identity_port
        self._consent_policy = consent_policy
        self._binding_store = binding_store
        self._identity_secret = identity_secret

    def _resolve_subject_and_identity(
        self,
        *,
        channel: str,
        channel_user_ref: str,
        subject_evidence_ref: str,
        conversation_ref: str,
        now_epoch: int,
    ) -> tuple[ChannelSubjectResolution, ChannelIdentity]:
        resolution = self._identity_port.resolve_channel_subject(
            channel=channel,
            channel_user_ref=channel_user_ref,
            subject_evidence_ref=subject_evidence_ref,
            now_epoch=now_epoch,
        )
        if resolution.channel != channel:
            raise PermissionError("subject_resolution_channel_mismatch")
        if not resolution.canonical_channel_subject_ref or not resolution.subject_attestation_ref:
            raise PermissionError("subject_resolution_incomplete")

        stable = hash_channel_user_ref(
            channel=channel,
            channel_user_ref=resolution.canonical_channel_subject_ref,
            secret=self._identity_secret,
        )
        identity_ref = f"identity:channel:{stable}"
        identity = self._identity_port.resolve_identity(
            channel=channel,
            identity_ref=identity_ref,
            canonical_channel_subject_ref=resolution.canonical_channel_subject_ref,
            subject_attestation_ref=resolution.subject_attestation_ref,
            conversation_ref=conversation_ref,
            candidate_party_refs=resolution.candidate_party_refs,
        )
        if identity.identity_ref != identity_ref:
            raise PermissionError("identity_ref_not_server_derived")
        if identity.canonical_channel_subject_ref != resolution.canonical_channel_subject_ref:
            raise PermissionError("identity_subject_mismatch")
        return resolution, identity

    def resolve(
        self,
        *,
        channel: str,
        channel_user_ref: str,
        subject_evidence_ref: str,
        conversation_ref: str,
        now_epoch: int,
    ) -> IdentityResolutionOutcome:
        _, identity = self._resolve_subject_and_identity(
            channel=channel,
            channel_user_ref=channel_user_ref,
            subject_evidence_ref=subject_evidence_ref,
            conversation_ref=conversation_ref,
            now_epoch=now_epoch,
        )
        return IdentityResolutionOutcome(
            identity=identity,
            may_link=identity.state is ChannelIdentityState.VERIFIED,
        )

    def verify(
        self,
        *,
        identity_ref: str,
        verification_evidence_ref: str,
        now_epoch: int,
    ) -> ChannelIdentity:
        return self._identity_port.verify_identity(
            identity_ref=identity_ref,
            verification_evidence_ref=verification_evidence_ref,
            now_epoch=now_epoch,
        )

    def link(
        self,
        *,
        identity_ref: str,
        party_ref: str,
        verification_ref: str,
        entry_context: ResolvedEntryContext,
    ) -> ChannelIdentity:
        return self._identity_port.link_verified_party(
            identity_ref=identity_ref,
            party_ref=party_ref,
            verification_ref=verification_ref,
            tenant_ref=entry_context.tenant_ref,
            merchant_ref=entry_context.merchant_ref,
            context_binding_ref=entry_context.context_binding_ref,
        )

    def issue_session_identity_binding(
        self,
        *,
        channel: str,
        channel_user_ref: str,
        subject_evidence_ref: str,
        conversation_ref: str,
        entry_context: ResolvedEntryContext,
        now_epoch: int,
        expires_at_epoch: int,
    ) -> IdentityBindingRecord:
        resolution, identity = self._resolve_subject_and_identity(
            channel=channel,
            channel_user_ref=channel_user_ref,
            subject_evidence_ref=subject_evidence_ref,
            conversation_ref=conversation_ref,
            now_epoch=now_epoch,
        )
        if expires_at_epoch <= now_epoch:
            raise ValueError("identity_binding_expiry_must_be_future")
        if expires_at_epoch > resolution.expires_at_epoch:
            raise PermissionError("identity_binding_outlives_subject_attestation")
        return self._binding_store.issue_binding(
            identity_ref=identity.identity_ref,
            canonical_channel_subject_ref=resolution.canonical_channel_subject_ref,
            subject_attestation_ref=resolution.subject_attestation_ref,
            conversation_ref=conversation_ref,
            tenant_ref=entry_context.tenant_ref,
            merchant_ref=entry_context.merchant_ref,
            location_ref=entry_context.location_ref,
            table_ref=entry_context.table_ref,
            dining_area_ref=entry_context.dining_area_ref,
            context_binding_ref=entry_context.context_binding_ref,
            issued_at_epoch=now_epoch,
            expires_at_epoch=expires_at_epoch,
        )

    def set_consent(
        self,
        *,
        identity_ref: str,
        purpose: ConsentPurpose,
        granted: bool,
        evidence_ref: str,
        idempotency_key: str,
    ) -> ConsentRecord:
        consent_version = self._consent_policy.current_version(purpose)
        return self._identity_port.record_consent(
            identity_ref=identity_ref,
            purpose=purpose,
            granted=granted,
            consent_version=consent_version,
            evidence_ref=evidence_ref,
            idempotency_key=idempotency_key,
        )

    def purpose_allowed(self, *, identity_ref: str, purpose: ConsentPurpose) -> bool:
        current_version = self._consent_policy.current_version(purpose)
        record = self._identity_port.consent_for(identity_ref=identity_ref, purpose=purpose)
        return bool(record and record.granted and record.consent_version == current_version)

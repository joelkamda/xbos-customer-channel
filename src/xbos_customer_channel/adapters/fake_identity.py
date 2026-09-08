from __future__ import annotations

from dataclasses import replace
from threading import RLock

from ..identity import (
    ChannelIdentity,
    ChannelIdentityState,
    ChannelSubjectResolution,
    ConsentPurpose,
    ConsentRecord,
    PartyCandidate,
)


class IdentityCollisionError(PermissionError):
    pass


class SubjectEvidenceError(PermissionError):
    pass


class ConsentIdempotencyConflict(ValueError):
    pass


class FakeCustomerIdentityService:
    """Single-process security fixture; never a Party/customer master or production identity provider."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._subject_evidence: dict[str, tuple[str, str, ChannelSubjectResolution]] = {}
        self._verification_evidence: dict[str, tuple[str, str, int]] = {}
        self._identities: dict[str, ChannelIdentity] = {}
        self._subject_bindings: dict[tuple[str, str], str] = {}
        self._consents: dict[tuple[str, ConsentPurpose], ConsentRecord] = {}
        self._idempotency: dict[str, tuple[tuple[object, ...], ConsentRecord]] = {}
        self._policy_versions: dict[ConsentPurpose, str] = {purpose: "v1" for purpose in ConsentPurpose}

    # Fixture setup only. Public request paths never create trusted subject evidence.
    def register_subject_evidence(
        self,
        *,
        channel: str,
        channel_user_ref: str,
        subject_evidence_ref: str,
        canonical_channel_subject_ref: str,
        expires_at_epoch: int,
        candidate_party_refs: tuple[str, ...] = (),
    ) -> None:
        if not channel or not channel_user_ref or not subject_evidence_ref or not canonical_channel_subject_ref:
            raise ValueError("subject_evidence_fields_required")
        resolution = ChannelSubjectResolution(
            channel=channel,
            canonical_channel_subject_ref=canonical_channel_subject_ref,
            subject_attestation_ref=f"subject_attestation:fixture:{subject_evidence_ref}",
            expires_at_epoch=expires_at_epoch,
            candidate_party_refs=candidate_party_refs,
        )
        with self._lock:
            self._subject_evidence[subject_evidence_ref] = (channel, channel_user_ref, resolution)

    def resolve_channel_subject(
        self,
        *,
        channel: str,
        channel_user_ref: str,
        subject_evidence_ref: str,
        now_epoch: int,
    ) -> ChannelSubjectResolution:
        with self._lock:
            fixture = self._subject_evidence.get(subject_evidence_ref)
            if fixture is None:
                raise SubjectEvidenceError("unknown_subject_evidence")
            expected_channel, expected_locator, resolution = fixture
            if channel != expected_channel:
                raise SubjectEvidenceError("cross_channel_subject_evidence")
            if channel_user_ref != expected_locator:
                raise SubjectEvidenceError("locator_attestation_mismatch")
            if now_epoch >= resolution.expires_at_epoch:
                raise SubjectEvidenceError("stale_or_expired_subject_evidence")
            return resolution

    def resolve_identity(
        self,
        *,
        channel: str,
        identity_ref: str,
        canonical_channel_subject_ref: str,
        subject_attestation_ref: str,
        conversation_ref: str,
        candidate_party_refs: tuple[str, ...],
    ) -> ChannelIdentity:
        key = (channel, canonical_channel_subject_ref)
        with self._lock:
            bound_ref = self._subject_bindings.get(key)
            if bound_ref is not None and bound_ref != identity_ref:
                raise IdentityCollisionError("canonical_subject_already_bound")

            current = self._identities.get(identity_ref)
            if current is not None:
                if current.channel != channel or current.canonical_channel_subject_ref != canonical_channel_subject_ref:
                    raise IdentityCollisionError("identity_ref_subject_collision")
                candidates = tuple(
                    PartyCandidate(party_ref=party_ref, match_ref=f"match:fixture:{index}")
                    for index, party_ref in enumerate(candidate_party_refs, start=1)
                )
                updated = replace(
                    current,
                    subject_attestation_ref=subject_attestation_ref,
                    conversation_ref=conversation_ref,
                    candidates=candidates or current.candidates,
                )
                self._identities[identity_ref] = updated
                self._subject_bindings[key] = identity_ref
                return updated

            candidates = tuple(
                PartyCandidate(party_ref=party_ref, match_ref=f"match:fixture:{index}")
                for index, party_ref in enumerate(candidate_party_refs, start=1)
            )
            state = ChannelIdentityState.RECOGNIZED if candidates else ChannelIdentityState.ANONYMOUS
            identity = ChannelIdentity(
                identity_ref=identity_ref,
                channel=channel,
                canonical_channel_subject_ref=canonical_channel_subject_ref,
                subject_attestation_ref=subject_attestation_ref,
                conversation_ref=conversation_ref,
                state=state,
                candidates=candidates,
            )
            self._identities[identity_ref] = identity
            self._subject_bindings[key] = identity_ref
            return identity

    # Fixture setup only. Verification evidence must be pre-registered to exact identity + subject.
    def register_verification_evidence(
        self,
        *,
        identity_ref: str,
        verification_evidence_ref: str,
        expires_at_epoch: int,
    ) -> None:
        with self._lock:
            current = self._require(identity_ref)
            self._verification_evidence[verification_evidence_ref] = (
                identity_ref,
                current.canonical_channel_subject_ref,
                expires_at_epoch,
            )

    def verify_identity(
        self,
        *,
        identity_ref: str,
        verification_evidence_ref: str,
        now_epoch: int,
    ) -> ChannelIdentity:
        with self._lock:
            current = self._require(identity_ref)
            if current.state is ChannelIdentityState.BLOCKED_OR_RESTRICTED:
                raise PermissionError("identity_restricted")
            fixture = self._verification_evidence.get(verification_evidence_ref)
            if fixture is None:
                raise PermissionError("verification_evidence_not_server_resolved")
            expected_identity_ref, expected_subject_ref, expires_at_epoch = fixture
            if expected_identity_ref != identity_ref or expected_subject_ref != current.canonical_channel_subject_ref:
                raise PermissionError("verification_evidence_identity_mismatch")
            if now_epoch >= expires_at_epoch:
                raise PermissionError("verification_evidence_expired")
            updated = replace(
                current,
                state=ChannelIdentityState.VERIFIED,
                verification_ref=f"verification:fixture:{verification_evidence_ref}",
            )
            self._identities[identity_ref] = updated
            return updated

    def link_verified_party(
        self,
        *,
        identity_ref: str,
        party_ref: str,
        verification_ref: str,
        tenant_ref: str | None,
        merchant_ref: str,
        context_binding_ref: str,
    ) -> ChannelIdentity:
        with self._lock:
            current = self._require(identity_ref)
            if current.state is not ChannelIdentityState.VERIFIED:
                raise PermissionError("verification_required_before_link")
            if current.verification_ref != verification_ref:
                raise PermissionError("verification_reference_mismatch")
            candidate_refs = {candidate.party_ref for candidate in current.candidates}
            if party_ref not in candidate_refs:
                raise PermissionError("party_not_in_resolved_candidates")
            if not merchant_ref or not context_binding_ref:
                raise PermissionError("party_link_context_required")
            updated = replace(
                current,
                state=ChannelIdentityState.LINKED,
                linked_party_ref=party_ref,
                linked_tenant_ref=tenant_ref,
                linked_merchant_ref=merchant_ref,
                linked_context_binding_ref=context_binding_ref,
            )
            self._identities[identity_ref] = updated
            return updated

    def restrict_identity(self, *, identity_ref: str, restriction_ref: str) -> ChannelIdentity:
        with self._lock:
            current = self._require(identity_ref)
            updated = replace(
                current,
                state=ChannelIdentityState.BLOCKED_OR_RESTRICTED,
                restriction_ref=restriction_ref,
            )
            self._identities[identity_ref] = updated
            return updated

    def record_consent(
        self,
        *,
        identity_ref: str,
        purpose: ConsentPurpose,
        granted: bool,
        consent_version: str,
        evidence_ref: str,
        idempotency_key: str,
    ) -> ConsentRecord:
        with self._lock:
            self._require(identity_ref)
            if not consent_version:
                raise ValueError("consent_version_required")
            fingerprint: tuple[object, ...] = (
                identity_ref,
                purpose,
                granted,
                consent_version,
                evidence_ref,
            )
            prior = self._idempotency.get(idempotency_key)
            if prior is not None:
                prior_fingerprint, prior_record = prior
                if prior_fingerprint != fingerprint:
                    raise ConsentIdempotencyConflict("consent_idempotency_payload_conflict")
                return prior_record

            record = ConsentRecord(
                consent_ref=f"consent:fixture:{len(self._idempotency) + 1}",
                identity_ref=identity_ref,
                purpose=purpose,
                granted=granted,
                consent_version=consent_version,
                evidence_ref=evidence_ref,
            )
            self._consents[(identity_ref, purpose)] = record
            self._idempotency[idempotency_key] = (fingerprint, record)
            return record

    def consent_for(self, *, identity_ref: str, purpose: ConsentPurpose) -> ConsentRecord | None:
        with self._lock:
            return self._consents.get((identity_ref, purpose))

    # Fixture policy control; public Channel requests never call this operation.
    def set_policy_version(self, purpose: ConsentPurpose, version: str) -> None:
        if not version:
            raise ValueError("consent_policy_version_required")
        with self._lock:
            self._policy_versions[purpose] = version

    def current_version(self, purpose: ConsentPurpose) -> str:
        with self._lock:
            try:
                return self._policy_versions[purpose]
            except KeyError as exc:
                raise KeyError("consent_policy_version_unknown") from exc

    def _require(self, identity_ref: str) -> ChannelIdentity:
        try:
            return self._identities[identity_ref]
        except KeyError as exc:
            raise KeyError("unknown_identity_ref") from exc

from __future__ import annotations

from dataclasses import replace

from ..identity import (
    ChannelIdentity,
    ChannelIdentityState,
    ConsentPurpose,
    ConsentRecord,
    PartyCandidate,
)


class FakeCustomerIdentityService:
    """Contract fixture, not a Party/customer master and not a production identity provider."""

    def __init__(self, recognized_refs: dict[str, tuple[str, ...]] | None = None) -> None:
        self._recognized_refs = recognized_refs or {}
        self._identities: dict[str, ChannelIdentity] = {}
        self._consents: dict[tuple[str, ConsentPurpose], ConsentRecord] = {}
        self._idempotency: dict[str, ConsentRecord] = {}

    def resolve_identity(
        self,
        *,
        channel: str,
        channel_user_ref: str,
        conversation_ref: str,
    ) -> ChannelIdentity:
        identity_ref = f"identity:fixture:{channel}:{conversation_ref}"
        candidate_refs = self._recognized_refs.get(channel_user_ref, ())
        candidates = tuple(
            PartyCandidate(party_ref=party_ref, match_ref=f"match:fixture:{index}")
            for index, party_ref in enumerate(candidate_refs, start=1)
        )
        state = ChannelIdentityState.RECOGNIZED if candidates else ChannelIdentityState.ANONYMOUS
        identity = ChannelIdentity(
            identity_ref=identity_ref,
            channel=channel,
            channel_user_ref=channel_user_ref,
            conversation_ref=conversation_ref,
            state=state,
            candidates=candidates,
        )
        self._identities[identity_ref] = identity
        return identity

    def verify_identity(
        self,
        *,
        identity_ref: str,
        verification_evidence_ref: str,
    ) -> ChannelIdentity:
        current = self._require(identity_ref)
        if current.state is ChannelIdentityState.BLOCKED_OR_RESTRICTED:
            raise PermissionError("identity_restricted")
        if not verification_evidence_ref:
            raise ValueError("verification_evidence_required")
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
    ) -> ChannelIdentity:
        current = self._require(identity_ref)
        if current.state is not ChannelIdentityState.VERIFIED:
            raise PermissionError("verification_required_before_link")
        if current.verification_ref != verification_ref:
            raise PermissionError("verification_reference_mismatch")
        candidate_refs = {candidate.party_ref for candidate in current.candidates}
        if party_ref not in candidate_refs:
            raise PermissionError("party_not_in_resolved_candidates")
        updated = replace(
            current,
            state=ChannelIdentityState.LINKED,
            linked_party_ref=party_ref,
        )
        self._identities[identity_ref] = updated
        return updated

    def restrict_identity(self, *, identity_ref: str, restriction_ref: str) -> ChannelIdentity:
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
        evidence_ref: str,
        idempotency_key: str,
    ) -> ConsentRecord:
        self._require(identity_ref)
        if idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]
        record = ConsentRecord(
            consent_ref=f"consent:fixture:{len(self._consents) + 1}",
            identity_ref=identity_ref,
            purpose=purpose,
            granted=granted,
            evidence_ref=evidence_ref,
        )
        self._consents[(identity_ref, purpose)] = record
        self._idempotency[idempotency_key] = record
        return record

    def consent_for(self, *, identity_ref: str, purpose: ConsentPurpose) -> ConsentRecord | None:
        return self._consents.get((identity_ref, purpose))

    def _require(self, identity_ref: str) -> ChannelIdentity:
        try:
            return self._identities[identity_ref]
        except KeyError as exc:
            raise KeyError("unknown_identity_ref") from exc

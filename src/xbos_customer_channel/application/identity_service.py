from __future__ import annotations

from dataclasses import dataclass

from ..identity import ChannelIdentity, ChannelIdentityState, ConsentPurpose, ConsentRecord
from ..ports import CustomerIdentityPort


@dataclass(frozen=True, slots=True)
class IdentityResolutionOutcome:
    identity: ChannelIdentity
    may_link: bool


class ChannelIdentityService:
    """Orchestrates channel identity without claiming XBOS Party authority."""

    def __init__(self, identity_port: CustomerIdentityPort) -> None:
        self._identity_port = identity_port

    def resolve(
        self,
        *,
        channel: str,
        channel_user_ref: str,
        conversation_ref: str,
    ) -> IdentityResolutionOutcome:
        identity = self._identity_port.resolve_identity(
            channel=channel,
            channel_user_ref=channel_user_ref,
            conversation_ref=conversation_ref,
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
    ) -> ChannelIdentity:
        return self._identity_port.verify_identity(
            identity_ref=identity_ref,
            verification_evidence_ref=verification_evidence_ref,
        )

    def link(
        self,
        *,
        identity_ref: str,
        party_ref: str,
        verification_ref: str,
    ) -> ChannelIdentity:
        return self._identity_port.link_verified_party(
            identity_ref=identity_ref,
            party_ref=party_ref,
            verification_ref=verification_ref,
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
        return self._identity_port.record_consent(
            identity_ref=identity_ref,
            purpose=purpose,
            granted=granted,
            evidence_ref=evidence_ref,
            idempotency_key=idempotency_key,
        )

    def purpose_allowed(self, *, identity_ref: str, purpose: ConsentPurpose) -> bool:
        record = self._identity_port.consent_for(identity_ref=identity_ref, purpose=purpose)
        return bool(record and record.granted)

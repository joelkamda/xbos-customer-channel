"""Dormant M1 offline-only Customer Channel / WND security evidence prototype.

This module does not authenticate a user, verify a hosted ACL, query a database,
inspect environment secrets, open a network connection, or register a route.
Every input must later be supplied by a separately authorized owner-authenticated
auditor and a protected nonsecret WND projection. Fixture booleans are NOT
evidence of actual hosted permission. No module-level instantiation/activation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

SYNTHETIC_TENANT = 910001
CHECKOUT_PURPOSE = "restaurant.customer_channel.checkout"
AUDITOR_CAPABILITY = "customer_channel.checkout_readiness.attest"
STAGING_WND_AUDIENCE = "https://wnd-xafpay-demo-austin-backend.onrender.com"
CURRENCY = "XAF"
TTL_SECONDS = 300
SCHEMA = "xafpay.cc-wnd.security-readiness.v1"

# Strictly typed output, not a general-purpose serialiser of service records.
_OUTPUT_KEYS = frozenset((
    "schema", "tenant_class", "reader_authorized", "checkout_effective",
    "binding_active", "merchant_location_branch_bound", "credential_ref_bound",
    "slot_status", "service_active", "release_pinned",
    "attested_at", "expires_at", "audit_receipt",
))


class ReadinessDenied(PermissionError):
    """Safe generic denial; never includes an input value or credential."""


@dataclass(frozen=True, slots=True)
class SyntheticSelector:
    """Expected/observed IDs must come from independent server-owned sources."""

    tenant_id: int
    merchant_id: UUID
    location_id: int
    branch_id: int
    context_id: UUID
    service_id: UUID

    def valid(self) -> bool:
        return (
            type(self.tenant_id) is int
            and self.tenant_id == SYNTHETIC_TENANT
            and type(self.location_id) is int and self.location_id > 0
            and type(self.branch_id) is int and self.branch_id > 0
            and all(
                isinstance(v, UUID) and v.int != 0
                for v in (self.merchant_id, self.context_id, self.service_id)
            )
        )


@dataclass(frozen=True, slots=True)
class OwnerReaderProof:
    """Nonsecret *hypothetical* attestation; not a hosting login mechanism."""

    owner_authenticated: bool
    approved_capability: str
    effective_reader_acl: bool
    deployed_schema_attested: bool
    release_pinned: bool
    release_sha: str
    checkout_effective: bool
    service_active: bool
    context_active: bool
    purpose: str
    audience: str
    currency: str
    provider_egress_off: bool
    r5c_off: bool
    r5h_off: bool
    context_expires_at: datetime


@dataclass(frozen=True, slots=True)
class CredentialReferenceProof:
    """Reference/status only; no tokens, digest bytes, hash prefix or DSN."""

    reference: str
    reference_bound: bool
    current_present: bool
    current_matched: bool
    next_present: bool
    next_matched: bool
    revoked: bool
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ReadinessReceipt:
    schema: str
    tenant_class: Literal["isolated_synthetic"]
    reader_authorized: bool
    checkout_effective: bool
    binding_active: bool
    merchant_location_branch_bound: bool
    credential_ref_bound: bool
    slot_status: Literal["current", "next"]
    service_active: bool
    release_pinned: bool
    attested_at: str
    expires_at: str
    audit_receipt: str

    def as_nonsecret_dict(self) -> dict[str, object]:
        # Explicit whitelist: no introspection, __dict__, arbitrary metadata.
        projected: dict[str, object] = {
            "schema": self.schema,
            "tenant_class": self.tenant_class,
            "reader_authorized": self.reader_authorized,
            "checkout_effective": self.checkout_effective,
            "binding_active": self.binding_active,
            "merchant_location_branch_bound": self.merchant_location_branch_bound,
            "credential_ref_bound": self.credential_ref_bound,
            "slot_status": self.slot_status,
            "service_active": self.service_active,
            "release_pinned": self.release_pinned,
            "attested_at": self.attested_at,
            "expires_at": self.expires_at,
            "audit_receipt": self.audit_receipt,
        }
        if frozenset(projected) != _OUTPUT_KEYS:
            raise ReadinessDenied("projection_not_allowed")
        return projected


def _utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ReadinessDenied("invalid_evidence_time")
    return value.astimezone(timezone.utc)


class DormantSyntheticReadiness:
    """Local fake-only builder; NEVER independently proves hosted authority.

    Future protected implementation must replace fixture booleans with actual
    authenticated principal, applied-schema, reader-ACL and reference attestations.
    Replay state here is process-local; it is not a durable admission ledger.
    """

    def __init__(
        self, *,
        enabled: bool = False,
        server_owned_expected: SyntheticSelector,
        expected_release_sha: str,
        expected_credential_reference: str,
    ):
        self._enabled = enabled
        if (
            not server_owned_expected.valid()
            or not re.fullmatch(r"[0-9a-f]{40}", expected_release_sha)
            or not re.fullmatch(r"wnd-channel-service:[A-Za-z0-9_.:-]{1,100}",
                                expected_credential_reference)
        ):
            raise ReadinessDenied("unqualified_server_owned_selector")
        self._expected = server_owned_expected
        self._release = expected_release_sha
        self._credential_ref = expected_credential_reference
        self._seen: set[str] = set()
        self._guard = Lock()

    def attest(
        self,
        *,
        actual: SyntheticSelector,
        owner: OwnerReaderProof,
        credential: CredentialReferenceProof,
        at: datetime,
        request_nonce: str,
    ) -> ReadinessReceipt:
        if not self._enabled:
            raise ReadinessDenied("readiness_builder_disabled")
        now = _utc(at)
        if not actual.valid() or actual != self._expected:
            raise ReadinessDenied("synthetic_selector_not_admitted")
        if (
            not owner.owner_authenticated
            or owner.approved_capability != AUDITOR_CAPABILITY
            or not owner.effective_reader_acl
            or not owner.deployed_schema_attested
            or not owner.release_pinned
            or owner.release_sha != self._release
        ):
            raise ReadinessDenied("owner_reader_or_release_not_admitted")
        if (
            not owner.checkout_effective
            or not owner.service_active
            or not owner.context_active
            or owner.purpose != CHECKOUT_PURPOSE
            or owner.audience != STAGING_WND_AUDIENCE
            or owner.currency != CURRENCY
            or not owner.provider_egress_off
            or not owner.r5c_off
            or not owner.r5h_off
        ):
            raise ReadinessDenied("checkout_scope_or_safety_not_admitted")
        if (
            credential.reference != self._credential_ref
            or not credential.reference_bound
            or credential.revoked
            or not (
                (credential.current_present and credential.current_matched)
                or (credential.next_present and credential.next_matched)
            )
        ):
            raise ReadinessDenied("credential_reference_or_slot_not_admitted")
        expiry = min(
            _utc(owner.context_expires_at),
            _utc(credential.expires_at),
            now + timedelta(seconds=TTL_SECONDS),
        )
        if expiry <= now:
            raise ReadinessDenied("evidence_expired")
        # Non-secret opaque nonce is caller-controlled *only within an already
        # approved owner audit session*. No nonce/selector is written to output.
        if not re.fullmatch(r"[0-9a-f]{32}", request_nonce):
            raise ReadinessDenied("invalid_audit_nonce")
        with self._guard:
            if request_nonce in self._seen:
                raise ReadinessDenied("replayed_audit_nonce")
            if len(self._seen) >= 128:
                raise ReadinessDenied("evidence_capacity_unavailable")
            self._seen.add(request_nonce)
        slot: Literal["current", "next"] = (
            "current"
            if credential.current_present and credential.current_matched
            else "next"
        )
        return ReadinessReceipt(
            schema=SCHEMA, tenant_class="isolated_synthetic",
            reader_authorized=True, checkout_effective=True,
            binding_active=True, merchant_location_branch_bound=True,
            credential_ref_bound=True, slot_status=slot,
            service_active=True, release_pinned=True,
            attested_at=now.isoformat(),
            expires_at=expiry.isoformat(),
            audit_receipt=str(uuid4()),
        )

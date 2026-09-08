from __future__ import annotations

import secrets
from dataclasses import replace
from threading import Lock

from ..order import AuthoritativeOrderConfirmationSnapshot
from ..provenance import (
    ConfirmationProvenanceRecord,
    EvidenceProvenanceRecord,
    ProvenanceConflict,
    ServerIssuedConfirmation,
    binding_from_session,
    confirmation_commercial_fingerprint,
    presentation_from_record,
)
from ..session_state import CustomerSessionSnapshot, UpstreamStateProjection


class InMemoryChannelProvenanceStore:
    """Single-process CR2 provenance contract store.

    This is intentionally not production durability proof. Process loss makes
    all handles unknown and therefore fails closed.
    """

    def __init__(self) -> None:
        self._evidence: dict[str, EvidenceProvenanceRecord] = {}
        self._confirmations: dict[str, ConfirmationProvenanceRecord] = {}
        self._lock = Lock()

    @staticmethod
    def _new_ref(prefix: str) -> str:
        return prefix + secrets.token_urlsafe(32)

    def issue_evidence(
        self,
        *,
        session: CustomerSessionSnapshot,
        projection: UpstreamStateProjection,
    ) -> str:
        if projection.correlation_ref != session.correlation_ref:
            raise ValueError("upstream_correlation_mismatch")
        handle = self._new_ref("evidence_handle_")
        record = EvidenceProvenanceRecord(
            evidence_handle_ref=handle,
            binding=binding_from_session(session),
            projection=projection,
        )
        with self._lock:
            while handle in self._evidence:
                handle = self._new_ref("evidence_handle_")
                record = replace(record, evidence_handle_ref=handle)
            self._evidence[handle] = record
        return handle

    def resolve_evidence(self, evidence_handle_ref: str) -> EvidenceProvenanceRecord | None:
        with self._lock:
            return self._evidence.get(evidence_handle_ref)

    def issue_confirmation(
        self,
        *,
        session: CustomerSessionSnapshot,
        confirmation: AuthoritativeOrderConfirmationSnapshot,
    ) -> ServerIssuedConfirmation:
        if not session.security_binding_complete:
            raise PermissionError("secure_session_binding_required_for_confirmation")
        handle = self._new_ref("confirmation_handle_")
        record = ConfirmationProvenanceRecord(
            confirmation_handle_ref=handle,
            authoritative_xbos_confirmation_ref=confirmation.confirmation_ref,
            binding=binding_from_session(session),
            quote_ref=confirmation.quote_ref,
            quote_version=confirmation.quote_version,
            service_context_ref=confirmation.service_context_ref,
            service_mode=confirmation.service_mode,
            commercial_fingerprint=confirmation_commercial_fingerprint(confirmation),
            authoritative_snapshot=confirmation,
        )
        with self._lock:
            while handle in self._confirmations:
                handle = self._new_ref("confirmation_handle_")
                record = replace(record, confirmation_handle_ref=handle)
            self._confirmations[handle] = record
        return presentation_from_record(record)

    def resolve_confirmation(self, confirmation_handle_ref: str) -> ConfirmationProvenanceRecord | None:
        with self._lock:
            return self._confirmations.get(confirmation_handle_ref)

    def claim_confirmation(
        self,
        *,
        confirmation_handle_ref: str,
        client_submit_ref: str,
    ) -> ConfirmationProvenanceRecord:
        """Single-process atomic same-handle claim/idempotency contract."""
        with self._lock:
            current = self._confirmations.get(confirmation_handle_ref)
            if current is None:
                raise KeyError(confirmation_handle_ref)
            if current.client_submit_ref_claim is None:
                current = replace(current, client_submit_ref_claim=client_submit_ref)
                self._confirmations[confirmation_handle_ref] = current
                return current
            if current.client_submit_ref_claim != client_submit_ref:
                raise ProvenanceConflict("confirmation_handle_client_submit_conflict")
            return current

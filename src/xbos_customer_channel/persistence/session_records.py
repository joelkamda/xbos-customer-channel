from __future__ import annotations

from dataclasses import replace
from threading import Lock

from ..session_state import CustomerSessionSnapshot


class InMemoryCustomerSessionStore:
    """Single-process session fixture. No order/payment authority is stored here."""

    def __init__(self) -> None:
        self._sessions: dict[str, CustomerSessionSnapshot] = {}
        self._aliases: dict[str, str] = {}
        self._idempotent_results: dict[tuple[str, str], CustomerSessionSnapshot] = {}
        self._lock = Lock()

    def put(self, session: CustomerSessionSnapshot) -> CustomerSessionSnapshot:
        with self._lock:
            self._sessions[session.session_ref] = session
        return session

    def put_with_legacy_alias(self, session: CustomerSessionSnapshot, alias: str | None) -> CustomerSessionSnapshot:
        with self._lock:
            self._sessions[session.session_ref] = session
            if alias is not None:
                if alias in self._sessions or alias in self._aliases:
                    raise ValueError("session_alias_already_exists")
                self._aliases[alias] = session.session_ref
        return session

    def _canonical_ref_unlocked(self, session_ref: str) -> str:
        return self._aliases.get(session_ref, session_ref)

    def canonical_ref(self, session_ref: str) -> str:
        with self._lock:
            return self._canonical_ref_unlocked(session_ref)

    def get(self, session_ref: str) -> CustomerSessionSnapshot | None:
        with self._lock:
            return self._sessions.get(self._canonical_ref_unlocked(session_ref))

    def idempotent_result(self, session_ref: str, idempotency_key: str) -> CustomerSessionSnapshot | None:
        with self._lock:
            canonical = self._canonical_ref_unlocked(session_ref)
            return self._idempotent_results.get((canonical, idempotency_key))

    def record_idempotent_result(
        self,
        session_ref: str,
        idempotency_key: str,
        snapshot: CustomerSessionSnapshot,
    ) -> CustomerSessionSnapshot:
        with self._lock:
            canonical = self._canonical_ref_unlocked(session_ref)
            self._idempotent_results[(canonical, idempotency_key)] = snapshot
        return snapshot

    def replace(self, session_ref: str, **changes: object) -> CustomerSessionSnapshot:
        with self._lock:
            canonical = self._canonical_ref_unlocked(session_ref)
            current = self._sessions[canonical]
            updated = replace(current, **changes)
            if updated.session_ref != canonical:
                raise ValueError("session_ref_replace_forbidden")
            self._sessions[canonical] = updated
            return updated

    def rotate_if_active(
        self,
        *,
        session_ref: str,
        expected_owner_identity_ref: str,
        now_epoch: int,
        replacement: CustomerSessionSnapshot,
    ) -> CustomerSessionSnapshot | None:
        """Single-process CAS: exactly one caller may rotate an active predecessor."""
        with self._lock:
            canonical = self._canonical_ref_unlocked(session_ref)
            current = self._sessions.get(canonical)
            if current is None:
                return None
            if current.owner_identity_ref != expected_owner_identity_ref:
                raise PermissionError("session_owner_mismatch")
            if current.invalidated_at_epoch is not None or current.rotated_to_session_ref is not None:
                return None
            if current.expires_at_epoch is None or now_epoch >= current.expires_at_epoch:
                raise PermissionError("session_expired")
            if replacement.predecessor_session_ref != current.session_ref:
                raise ValueError("session_rotation_predecessor_mismatch")
            invalidated = replace(
                current,
                invalidated_at_epoch=now_epoch,
                rotated_to_session_ref=replacement.session_ref,
            )
            self._sessions[canonical] = invalidated
            self._sessions[replacement.session_ref] = replacement
            return replacement

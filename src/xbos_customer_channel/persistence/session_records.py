from __future__ import annotations

from dataclasses import replace

from ..session_state import CustomerSessionSnapshot


class InMemoryCustomerSessionStore:
    """Development fixture for Channel session state only. It is not an order/payment state store."""

    def __init__(self) -> None:
        self._sessions: dict[str, CustomerSessionSnapshot] = {}
        self._idempotent_results: dict[tuple[str, str], CustomerSessionSnapshot] = {}

    def put(self, session: CustomerSessionSnapshot) -> CustomerSessionSnapshot:
        self._sessions[session.session_ref] = session
        return session

    def get(self, session_ref: str) -> CustomerSessionSnapshot | None:
        return self._sessions.get(session_ref)

    def idempotent_result(self, session_ref: str, idempotency_key: str) -> CustomerSessionSnapshot | None:
        return self._idempotent_results.get((session_ref, idempotency_key))

    def record_idempotent_result(
        self,
        session_ref: str,
        idempotency_key: str,
        snapshot: CustomerSessionSnapshot,
    ) -> CustomerSessionSnapshot:
        self._idempotent_results[(session_ref, idempotency_key)] = snapshot
        return snapshot

    def replace(self, session_ref: str, **changes: object) -> CustomerSessionSnapshot:
        current = self._sessions[session_ref]
        updated = replace(current, **changes)
        self._sessions[session_ref] = updated
        return updated

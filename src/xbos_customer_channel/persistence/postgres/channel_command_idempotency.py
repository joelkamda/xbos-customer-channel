from __future__ import annotations

from datetime import datetime

from ...channel_contract import ChannelCommand, command_fingerprint
from .transport_state_stores import IdempotencyResult, PostgresIdempotencyResultStore


CHANNEL_COMMAND_SCOPE = "channel_command"


class PostgresChannelCommandIdempotencyBinder:
    """Bind one provider event identity to at most one effective Channel command."""

    def __init__(self, store: PostgresIdempotencyResultStore) -> None:
        self._store = store

    def reserve(
        self,
        *,
        command: ChannelCommand,
        now_utc: datetime,
        purge_after_utc: datetime,
    ) -> IdempotencyResult:
        return self._store.reserve(
            scope=CHANNEL_COMMAND_SCOPE,
            idempotency_key=command.idempotency_key,
            request_fingerprint=command_fingerprint(command),
            conversation_ref=command.conversation_id,
            session_ref=command.session_ref,
            now_utc=now_utc,
            purge_after_utc=purge_after_utc,
        )

    def complete(
        self,
        *,
        reservation: IdempotencyResult,
        command: ChannelCommand,
        result_ref: str,
        safe_result_json: dict[str, object] | None,
        completed_at_utc: datetime,
    ) -> IdempotencyResult:
        if reservation.idempotency_key != command.idempotency_key:
            raise ValueError("channel_command_reservation_identity_mismatch")
        if reservation.request_fingerprint != command_fingerprint(command):
            raise ValueError("channel_command_reservation_fingerprint_mismatch")
        return self._store.complete(
            idempotency_result_ref=reservation.idempotency_result_ref,
            expected_row_version=reservation.row_version,
            result_ref=result_ref,
            safe_result_json=safe_result_json,
            completed_at_utc=completed_at_utc,
        )

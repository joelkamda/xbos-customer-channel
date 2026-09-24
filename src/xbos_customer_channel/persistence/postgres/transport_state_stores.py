from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import ConnectionFactory, transaction


class IdempotencyConflict(RuntimeError):
    pass


class DeliveryConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderMessageReceipt:
    provider_message_receipt_ref: UUID
    provider_code: str
    provider_endpoint_ref: str
    provider_message_ref: str
    event_kind: str
    processing_state: str
    processing_attempt: int
    row_version: int


@dataclass(frozen=True, slots=True)
class IdempotencyResult:
    idempotency_result_ref: UUID
    scope: str
    idempotency_key: str
    request_fingerprint: str
    status: str
    result_ref: str | None
    safe_result_json: dict[str, Any] | None
    row_version: int


@dataclass(frozen=True, slots=True)
class TransportDelivery:
    delivery_ref: UUID
    receipt_ref: UUID
    render_ordinal: int
    provider_code: str
    provider_message_ref: str | None
    delivery_state: str
    row_version: int


class PostgresProviderMessageReceiptStore:
    """Replay-safe provider event receipt registration."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def get_or_create(
        self,
        *,
        provider_code: str,
        provider_endpoint_ref: str,
        provider_message_ref: str,
        event_kind: str,
        conversation_ref: str | None,
        sender_lookup_hash: str | None,
        occurred_at_utc: datetime,
        payload_digest: str,
        normalized_action_code: str | None,
        now_utc: datetime,
        purge_after_utc: datetime,
    ) -> tuple[ProviderMessageReceipt, bool]:
        """Create exactly one receipt or return the pre-existing receipt."""

        new_ref = uuid4()
        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    INSERT INTO provider_message_receipt (
                        provider_message_receipt_ref, provider_code,
                        provider_endpoint_ref, provider_message_ref, event_kind,
                        conversation_ref, sender_lookup_hash, occurred_at_utc,
                        payload_digest, normalized_action_code,
                        processing_state, processing_started_at_utc,
                        processing_attempt, idempotency_result_ref,
                        transport_delivery_ref, row_version,
                        created_at_utc, updated_at_utc, purge_after_utc
                    )
                    VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        'received',NULL,0,NULL,NULL,1,%s,%s,%s
                    )
                    ON CONFLICT (
                        provider_code, provider_endpoint_ref,
                        provider_message_ref, event_kind
                    )
                    DO NOTHING
                    RETURNING *
                    """,
                    (
                        new_ref,
                        provider_code,
                        provider_endpoint_ref,
                        provider_message_ref,
                        event_kind,
                        conversation_ref,
                        sender_lookup_hash,
                        _utc(occurred_at_utc),
                        payload_digest,
                        normalized_action_code,
                        now,
                        now,
                        _utc(purge_after_utc),
                    ),
                )
                row = cursor.fetchone()
                if row is not None:
                    return _receipt_from_row(row), True
                cursor.execute(
                    """
                    SELECT *
                    FROM provider_message_receipt
                    WHERE provider_code = %s
                      AND provider_endpoint_ref = %s
                      AND provider_message_ref = %s
                      AND event_kind = %s
                    """,
                    (
                        provider_code,
                        provider_endpoint_ref,
                        provider_message_ref,
                        event_kind,
                    ),
                )
                existing = cursor.fetchone()
        if existing is None:
            raise RuntimeError("provider_message_receipt_conflict_without_row")
        return _receipt_from_row(existing), False

    def begin_processing(
        self,
        *,
        receipt_ref: UUID,
        expected_row_version: int,
        now_utc: datetime,
    ) -> ProviderMessageReceipt:
        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    UPDATE provider_message_receipt
                    SET processing_state = 'processing',
                        processing_started_at_utc = %s,
                        processing_attempt = processing_attempt + 1,
                        updated_at_utc = %s,
                        row_version = row_version + 1
                    WHERE provider_message_receipt_ref = %s
                      AND row_version = %s
                      AND processing_state IN ('received','failed_retryable')
                    RETURNING *
                    """,
                    (now, now, receipt_ref, expected_row_version),
                )
                row = cursor.fetchone()
        if row is None:
            raise DeliveryConflict("provider_receipt_processing_conflict")
        return _receipt_from_row(row)

    def finish_processing(
        self,
        *,
        receipt_ref: UUID,
        expected_row_version: int,
        processing_state: str,
        idempotency_result_ref: UUID | None,
        transport_delivery_ref: UUID | None,
        now_utc: datetime,
    ) -> ProviderMessageReceipt:
        if processing_state not in {
            "processed",
            "failed_retryable",
            "failed_permanent",
        }:
            raise ValueError("invalid_provider_receipt_terminal_state")
        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    UPDATE provider_message_receipt
                    SET processing_state = %s,
                        idempotency_result_ref = %s,
                        transport_delivery_ref = %s,
                        updated_at_utc = %s,
                        row_version = row_version + 1
                    WHERE provider_message_receipt_ref = %s
                      AND row_version = %s
                      AND processing_state = 'processing'
                    RETURNING *
                    """,
                    (
                        processing_state,
                        idempotency_result_ref,
                        transport_delivery_ref,
                        now,
                        receipt_ref,
                        expected_row_version,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise DeliveryConflict("provider_receipt_finish_conflict")
        return _receipt_from_row(row)


class PostgresIdempotencyResultStore:
    """Durable scoped idempotency with fingerprint conflict protection."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def reserve(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_fingerprint: str,
        conversation_ref: str | None,
        session_ref: str | None,
        now_utc: datetime,
        purge_after_utc: datetime,
    ) -> tuple[IdempotencyResult, bool]:
        new_ref = uuid4()
        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    INSERT INTO idempotency_result (
                        idempotency_result_ref, scope, idempotency_key,
                        request_fingerprint, status, conversation_ref,
                        session_ref, result_ref, safe_result_json,
                        completed_at_utc, row_version,
                        created_at_utc, updated_at_utc, purge_after_utc
                    )
                    VALUES (
                        %s,%s,%s,%s,'in_progress',%s,%s,NULL,NULL,NULL,
                        1,%s,%s,%s
                    )
                    ON CONFLICT (scope, idempotency_key)
                    DO NOTHING
                    RETURNING *
                    """,
                    (
                        new_ref,
                        scope,
                        idempotency_key,
                        request_fingerprint,
                        conversation_ref,
                        session_ref,
                        now,
                        now,
                        _utc(purge_after_utc),
                    ),
                )
                row = cursor.fetchone()
                if row is not None:
                    return _idempotency_from_row(row), True
                cursor.execute(
                    """
                    SELECT *
                    FROM idempotency_result
                    WHERE scope = %s AND idempotency_key = %s
                    """,
                    (scope, idempotency_key),
                )
                existing = cursor.fetchone()
        if existing is None:
            raise RuntimeError("idempotency_conflict_without_row")
        if existing["request_fingerprint"] != request_fingerprint:
            raise IdempotencyConflict(
                "idempotency_key_reused_with_different_fingerprint"
            )
        return _idempotency_from_row(existing), False

    def complete(
        self,
        *,
        idempotency_result_ref: UUID,
        expected_row_version: int,
        result_ref: str | None,
        safe_result_json: dict[str, Any] | None,
        completed_at_utc: datetime,
    ) -> IdempotencyResult:
        completed = _utc(completed_at_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    UPDATE idempotency_result
                    SET status = 'completed',
                        result_ref = %s,
                        safe_result_json = %s,
                        completed_at_utc = %s,
                        updated_at_utc = %s,
                        row_version = row_version + 1
                    WHERE idempotency_result_ref = %s
                      AND row_version = %s
                      AND status IN ('in_progress','unknown')
                    RETURNING *
                    """,
                    (
                        result_ref,
                        None if safe_result_json is None else Jsonb(safe_result_json),
                        completed,
                        completed,
                        idempotency_result_ref,
                        expected_row_version,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise IdempotencyConflict("idempotency_complete_compare_and_swap_conflict")
        return _idempotency_from_row(row)

    def mark_unknown(
        self,
        *,
        idempotency_result_ref: UUID,
        expected_row_version: int,
        now_utc: datetime,
    ) -> IdempotencyResult:
        """Persist unknown outcome so a second upstream request is forbidden."""

        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    UPDATE idempotency_result
                    SET status = 'unknown',
                        updated_at_utc = %s,
                        row_version = row_version + 1
                    WHERE idempotency_result_ref = %s
                      AND row_version = %s
                      AND status = 'in_progress'
                    RETURNING *
                    """,
                    (now, idempotency_result_ref, expected_row_version),
                )
                row = cursor.fetchone()
        if row is None:
            raise IdempotencyConflict("idempotency_unknown_compare_and_swap_conflict")
        return _idempotency_from_row(row)


class PostgresTransportDeliveryStore:
    """Outbound delivery metadata only; never payment-success evidence."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def create_pending(
        self,
        *,
        receipt_ref: UUID,
        conversation_ref: str | None,
        render_ordinal: int,
        recipient_lookup_hash: str,
        message_kind: str,
        payload_digest: str,
        provider_code: str,
        now_utc: datetime,
        purge_after_utc: datetime,
    ) -> TransportDelivery:
        delivery_ref = uuid4()
        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    INSERT INTO transport_delivery (
                        delivery_ref, receipt_ref, conversation_ref,
                        render_ordinal, recipient_lookup_hash,
                        message_kind, payload_digest, delivery_state,
                        provider_code, provider_message_ref,
                        send_attempt_count, last_attempt_at_utc,
                        provider_occurred_at_utc, provider_error_code,
                        row_version, created_at_utc, updated_at_utc,
                        purge_after_utc
                    )
                    VALUES (
                        %s,%s,%s,%s,%s,%s,%s,'pending_send',
                        %s,NULL,0,NULL,NULL,NULL,1,%s,%s,%s
                    )
                    RETURNING *
                    """,
                    (
                        delivery_ref,
                        receipt_ref,
                        conversation_ref,
                        render_ordinal,
                        recipient_lookup_hash,
                        message_kind,
                        payload_digest,
                        provider_code,
                        now,
                        now,
                        _utc(purge_after_utc),
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("transport_delivery_insert_missing_return")
        return _delivery_from_row(row)

    def record_send_outcome(
        self,
        *,
        delivery_ref: UUID,
        expected_row_version: int,
        delivery_state: str,
        provider_message_ref: str | None,
        provider_error_code: str | None,
        now_utc: datetime,
    ) -> TransportDelivery:
        if delivery_state not in {"sent", "failed", "unknown"}:
            raise ValueError("invalid_transport_send_outcome")
        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    UPDATE transport_delivery
                    SET delivery_state = %s,
                        provider_message_ref = %s,
                        provider_error_code = %s,
                        send_attempt_count = send_attempt_count + 1,
                        last_attempt_at_utc = %s,
                        updated_at_utc = %s,
                        row_version = row_version + 1
                    WHERE delivery_ref = %s
                      AND row_version = %s
                      AND delivery_state = 'pending_send'
                    RETURNING *
                    """,
                    (
                        delivery_state,
                        provider_message_ref,
                        provider_error_code,
                        now,
                        now,
                        delivery_ref,
                        expected_row_version,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise DeliveryConflict("transport_delivery_send_outcome_conflict")
        return _delivery_from_row(row)

    def apply_provider_status(
        self,
        *,
        provider_code: str,
        provider_message_ref: str,
        delivery_state: str,
        provider_occurred_at_utc: datetime,
        provider_error_code: str | None,
        now_utc: datetime,
    ) -> TransportDelivery | None:
        if delivery_state not in {"sent", "delivered", "read", "failed"}:
            raise ValueError("invalid_provider_delivery_state")
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM transport_delivery
                    WHERE provider_code = %s
                      AND provider_message_ref = %s
                    FOR UPDATE
                    """,
                    (provider_code, provider_message_ref),
                )
                current = cursor.fetchone()
                if current is None:
                    return None
                if (
                    current["delivery_state"] == delivery_state
                    and current["provider_occurred_at_utc"]
                    == _utc(provider_occurred_at_utc)
                    and current["provider_error_code"] == provider_error_code
                ):
                    return _delivery_from_row(current)
                cursor.execute(
                    """
                    UPDATE transport_delivery
                    SET delivery_state = %s,
                        provider_occurred_at_utc = %s,
                        provider_error_code = %s,
                        updated_at_utc = %s,
                        row_version = row_version + 1
                    WHERE delivery_ref = %s
                      AND row_version = %s
                    RETURNING *
                    """,
                    (
                        delivery_state,
                        _utc(provider_occurred_at_utc),
                        provider_error_code,
                        _utc(now_utc),
                        current["delivery_ref"],
                        current["row_version"],
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise DeliveryConflict(
                "provider_delivery_status_compare_and_swap_conflict"
            )
        return _delivery_from_row(row)


def _receipt_from_row(row: dict[str, Any]) -> ProviderMessageReceipt:
    return ProviderMessageReceipt(
        provider_message_receipt_ref=row["provider_message_receipt_ref"],
        provider_code=row["provider_code"],
        provider_endpoint_ref=row["provider_endpoint_ref"],
        provider_message_ref=row["provider_message_ref"],
        event_kind=row["event_kind"],
        processing_state=row["processing_state"],
        processing_attempt=int(row["processing_attempt"]),
        row_version=int(row["row_version"]),
    )


def _idempotency_from_row(row: dict[str, Any]) -> IdempotencyResult:
    safe = row["safe_result_json"]
    return IdempotencyResult(
        idempotency_result_ref=row["idempotency_result_ref"],
        scope=row["scope"],
        idempotency_key=row["idempotency_key"],
        request_fingerprint=row["request_fingerprint"],
        status=row["status"],
        result_ref=row["result_ref"],
        safe_result_json=None if safe is None else dict(safe),
        row_version=int(row["row_version"]),
    )


def _delivery_from_row(row: dict[str, Any]) -> TransportDelivery:
    return TransportDelivery(
        delivery_ref=row["delivery_ref"],
        receipt_ref=row["receipt_ref"],
        render_ordinal=int(row["render_ordinal"]),
        provider_code=row["provider_code"],
        provider_message_ref=row["provider_message_ref"],
        delivery_state=row["delivery_state"],
        row_version=int(row["row_version"]),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timezone_aware_datetime_required")
    return value.astimezone(timezone.utc)

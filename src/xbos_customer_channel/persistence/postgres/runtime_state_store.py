from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .database import ConnectionFactory, transaction
from .serialization import LocatorKeyRing


class RuntimeStateMissing(KeyError):
    pass


class StaleRuntimeStateVersion(RuntimeError):
    pass


class SessionGenerationConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PersistedRuntimeState:
    session_ref: str
    row_version: int
    state_schema_version: int
    interaction_state_json: Mapping[str, Any]
    last_provider_message_ref: str | None
    last_transition_ref: str | None


@dataclass(frozen=True, slots=True)
class ConversationResolution:
    conversation_ref: str
    row_version: int
    locator_hash_key_version: str
    lazy_rehashed: bool


class PostgresRuntimeStateStore:
    """Direct-psycopg durable state substrate.

    H1S3 materializes this behind the port only. It is not composed into the
    live runtime. Raw provider locators are transient method input only.
    """

    LOAD_SQL = """
        SELECT session_ref, row_version, state_schema_version,
               interaction_state_json, last_provider_message_ref,
               last_transition_ref
        FROM w1_runtime_state
        WHERE session_ref = %s
    """

    CAS_UPDATE_SQL = """
        UPDATE w1_runtime_state
        SET interaction_state_json = %s,
            state_schema_version = %s,
            last_provider_message_ref = %s,
            last_transition_ref = %s,
            updated_at_utc = %s,
            purge_after_utc = %s,
            row_version = row_version + 1
        WHERE session_ref = %s
          AND row_version = %s
        RETURNING row_version
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def load(self, session_ref: str) -> PersistedRuntimeState:
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(self.LOAD_SQL, (session_ref,))
                row = cursor.fetchone()
        if row is None:
            raise RuntimeStateMissing(session_ref)
        return PersistedRuntimeState(
            session_ref=row["session_ref"],
            row_version=int(row["row_version"]),
            state_schema_version=int(row["state_schema_version"]),
            interaction_state_json=row["interaction_state_json"],
            last_provider_message_ref=row["last_provider_message_ref"],
            last_transition_ref=row["last_transition_ref"],
        )

    def insert_initial(
        self,
        *,
        session_ref: str,
        state_schema_version: int,
        interaction_state_json: Mapping[str, Any],
        created_at_utc: datetime,
        purge_after_utc: datetime,
        last_provider_message_ref: str | None = None,
        last_transition_ref: str | None = None,
    ) -> int:
        now = _utc(created_at_utc)
        sql = """
            INSERT INTO w1_runtime_state (
                session_ref, row_version, state_schema_version,
                interaction_state_json, last_provider_message_ref,
                last_transition_ref, created_at_utc, updated_at_utc,
                purge_after_utc
            )
            VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s)
            RETURNING row_version
        """
        with transaction(self._connection_factory) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    (
                        session_ref,
                        state_schema_version,
                        Jsonb(dict(interaction_state_json)),
                        last_provider_message_ref,
                        last_transition_ref,
                        now,
                        now,
                        _utc(purge_after_utc),
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("runtime_state_insert_missing_return")
        return int(row[0])

    def compare_and_swap(
        self,
        *,
        session_ref: str,
        expected_row_version: int,
        state_schema_version: int,
        interaction_state_json: Mapping[str, Any],
        updated_at_utc: datetime,
        purge_after_utc: datetime,
        last_provider_message_ref: str | None,
        last_transition_ref: str | None,
    ) -> int:
        with transaction(self._connection_factory) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.CAS_UPDATE_SQL,
                    (
                        Jsonb(dict(interaction_state_json)),
                        state_schema_version,
                        last_provider_message_ref,
                        last_transition_ref,
                        _utc(updated_at_utc),
                        _utc(purge_after_utc),
                        session_ref,
                        expected_row_version,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise StaleRuntimeStateVersion(
                "runtime_state_compare_and_swap_conflict"
            )
        return int(row[0])

    def resolve_active_conversation(
        self,
        *,
        channel_code: str,
        provider_endpoint_ref: str,
        raw_sender: str,
        key_ring: LocatorKeyRing,
        now_utc: datetime,
    ) -> ConversationResolution | None:
        """Look up current/previous keyed locators and lazily rehash old hits."""

        now = _utc(now_utc)
        candidates = key_ring.candidates(
            channel_code=channel_code,
            provider_endpoint_ref=provider_endpoint_ref,
            raw_sender=raw_sender,
        )
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                for candidate in candidates:
                    cursor.execute(
                        """
                        SELECT conversation_ref, row_version,
                               locator_hash_key_version
                        FROM channel_conversation
                        WHERE channel_code = %s
                          AND provider_endpoint_ref = %s
                          AND external_user_lookup_hash = %s
                          AND locator_hash_key_version = %s
                          AND closed_at_utc IS NULL
                        FOR UPDATE
                        """,
                        (
                            channel_code,
                            provider_endpoint_ref,
                            candidate.digest,
                            candidate.key_version,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        continue
                    current_version = int(row["row_version"])
                    if not candidate.needs_rehash:
                        return ConversationResolution(
                            conversation_ref=row["conversation_ref"],
                            row_version=current_version,
                            locator_hash_key_version=candidate.key_version,
                            lazy_rehashed=False,
                        )
                    current = key_ring.current(
                        channel_code=channel_code,
                        provider_endpoint_ref=provider_endpoint_ref,
                        raw_sender=raw_sender,
                    )
                    cursor.execute(
                        """
                        UPDATE channel_conversation
                        SET external_user_lookup_hash = %s,
                            locator_hash_key_version = %s,
                            updated_at_utc = %s,
                            row_version = row_version + 1
                        WHERE conversation_ref = %s
                          AND row_version = %s
                        RETURNING row_version
                        """,
                        (
                            current.digest,
                            current.key_version,
                            now,
                            row["conversation_ref"],
                            current_version,
                        ),
                    )
                    updated = cursor.fetchone()
                    if updated is None:
                        raise StaleRuntimeStateVersion(
                            "conversation_locator_lazy_rehash_conflict"
                        )
                    return ConversationResolution(
                        conversation_ref=row["conversation_ref"],
                        row_version=int(updated[0]),
                        locator_hash_key_version=current.key_version,
                        lazy_rehashed=True,
                    )
        return None

    def create_conversation(
        self,
        *,
        conversation_ref: str,
        channel_code: str,
        provider_endpoint_ref: str,
        raw_sender: str,
        key_ring: LocatorKeyRing,
        identity_ref: str | None,
        now_utc: datetime,
        purge_after_utc: datetime,
    ) -> ConversationResolution:
        current = key_ring.current(
            channel_code=channel_code,
            provider_endpoint_ref=provider_endpoint_ref,
            raw_sender=raw_sender,
        )
        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO channel_conversation (
                        conversation_ref, channel_code, provider_endpoint_ref,
                        external_user_lookup_hash, locator_hash_key_version,
                        identity_ref, last_activity_at_utc, closed_at_utc,
                        row_version, created_at_utc, updated_at_utc,
                        purge_after_utc
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, 1, %s, %s, %s)
                    RETURNING row_version
                    """,
                    (
                        conversation_ref,
                        channel_code,
                        provider_endpoint_ref,
                        current.digest,
                        current.key_version,
                        identity_ref,
                        now,
                        now,
                        now,
                        _utc(purge_after_utc),
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("conversation_insert_missing_return")
        return ConversationResolution(
            conversation_ref=conversation_ref,
            row_version=int(row[0]),
            locator_hash_key_version=current.key_version,
            lazy_rehashed=False,
        )

    def assert_session_generation_available(
        self,
        *,
        conversation_ref: str,
        generation: int,
    ) -> None:
        with transaction(self._connection_factory) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 1
                    FROM channel_session
                    WHERE conversation_ref = %s
                      AND generation = %s
                    """,
                    (conversation_ref, generation),
                )
                if cursor.fetchone() is not None:
                    raise SessionGenerationConflict(
                        "session_generation_already_exists"
                    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timezone_aware_datetime_required")
    return value.astimezone(timezone.utc)

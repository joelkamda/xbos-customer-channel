from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from ...entry_context import EntryPurpose, EntryTokenRecord, ReplayPolicy
from ...identity import IdentityBindingRecord
from ...session_state import ChannelState, CustomerSessionSnapshot
from .database import ConnectionFactory, transaction


class SecurityStateConflict(RuntimeError):
    pass


class SessionRotationConflict(SecurityStateConflict):
    pass


class ProvenanceHandleConflict(SecurityStateConflict):
    pass


class ActiveSessionAmbiguous(SecurityStateConflict):
    pass


class ActiveIdentityBindingAmbiguous(SecurityStateConflict):
    pass


@dataclass(frozen=True, slots=True)
class DurableProvenanceHandle:
    provenance_handle_ref: str
    handle_kind: str
    session_ref: str
    session_generation: int
    correlation_ref: str
    upstream_ref: str | None
    binding_json: dict[str, Any]
    customer_safe_projection_json: dict[str, Any] | None
    commercial_fingerprint: str | None
    client_submit_ref_claim: str | None
    row_version: int


class PostgresCustomerSessionStore:
    """Durable session security/lifecycle store with atomic rotation."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def insert(
        self,
        *,
        session: CustomerSessionSnapshot,
        legacy_alias_ref: str | None,
        created_at_utc: datetime,
        updated_at_utc: datetime,
    ) -> int:
        if session.expires_at_epoch is None:
            raise ValueError("session_expiry_required")
        purge_after_utc = _from_epoch(session.expires_at_epoch) + timedelta(days=7)
        sql = """
            INSERT INTO channel_session (
                session_ref, conversation_ref, correlation_ref, state,
                entry_token_ref, owner_identity_ref, tenant_ref, merchant_ref,
                location_ref, table_ref, dining_area_ref, entry_purpose,
                context_binding_ref, expires_at_utc, generation,
                legacy_alias_ref, predecessor_session_ref,
                rotated_to_session_ref, invalidated_at_utc,
                cart_ref, quote_ref, order_ref, payment_ref,
                last_upstream_evidence_ref, human_handoff_ref,
                transition_count, row_version, created_at_utc, updated_at_utc,
                purge_after_utc
            )
            VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s
            )
            RETURNING row_version
        """
        with transaction(self._connection_factory) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    (
                        session.session_ref,
                        session.conversation_ref,
                        session.correlation_ref,
                        session.state.value,
                        session.entry_token_ref,
                        session.owner_identity_ref,
                        session.tenant_ref,
                        session.merchant_ref,
                        session.location_ref,
                        session.table_ref,
                        session.dining_area_ref,
                        None if session.entry_purpose is None else session.entry_purpose.value,
                        session.context_binding_ref,
                        _from_epoch(session.expires_at_epoch),
                        session.generation,
                        legacy_alias_ref,
                        session.predecessor_session_ref,
                        session.rotated_to_session_ref,
                        _optional_from_epoch(session.invalidated_at_epoch),
                        session.cart_ref,
                        session.quote_ref,
                        session.order_ref,
                        session.payment_ref,
                        session.last_upstream_evidence_ref,
                        session.human_handoff_ref,
                        session.transition_count,
                        _utc(created_at_utc),
                        _utc(updated_at_utc),
                        purge_after_utc,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("session_insert_missing_return")
        return int(row[0])

    def get(
        self,
        session_ref: str,
        *,
        security_binding_complete: bool,
    ) -> CustomerSessionSnapshot | None:
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM channel_session
                    WHERE session_ref = %s OR legacy_alias_ref = %s
                    ORDER BY CASE WHEN session_ref = %s THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (session_ref, session_ref, session_ref),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return _session_from_row(
            row,
            security_binding_complete=security_binding_complete,
        )

    def resolve_active_for_conversation(
        self,
        conversation_ref: str,
        *,
        now_utc: datetime,
    ) -> CustomerSessionSnapshot | None:
        """Return exactly one active session or fail closed on ambiguity."""

        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM channel_session
                    WHERE conversation_ref = %s
                      AND invalidated_at_utc IS NULL
                      AND rotated_to_session_ref IS NULL
                      AND expires_at_utc > %s
                    ORDER BY generation DESC, created_at_utc DESC
                    LIMIT 2
                    """,
                    (conversation_ref, now),
                )
                first = cursor.fetchone()
                second = cursor.fetchone()
        if second is not None:
            raise ActiveSessionAmbiguous("active_session_state_ambiguous")
        if first is None:
            return None
        return _session_from_row(first, security_binding_complete=False)

    def rotate_if_active(
        self,
        *,
        session_ref: str,
        expected_owner_identity_ref: str,
        now_epoch: int,
        replacement: CustomerSessionSnapshot,
        replacement_legacy_alias_ref: str | None = None,
    ) -> CustomerSessionSnapshot | None:
        """Lock predecessor, invalidate it, and insert successor atomically."""

        if replacement.predecessor_session_ref != session_ref:
            raise ValueError("session_rotation_predecessor_mismatch")
        if replacement.expires_at_epoch is None:
            raise ValueError("replacement_session_expiry_required")
        now = _from_epoch(now_epoch)
        replacement_purge_after_utc = (
            _from_epoch(replacement.expires_at_epoch) + timedelta(days=7)
        )

        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM channel_session
                    WHERE session_ref = %s
                    FOR UPDATE
                    """,
                    (session_ref,),
                )
                current = cursor.fetchone()
                if current is None:
                    return None
                if current["owner_identity_ref"] != expected_owner_identity_ref:
                    raise PermissionError("session_owner_mismatch")
                if (
                    current["invalidated_at_utc"] is not None
                    or current["rotated_to_session_ref"] is not None
                ):
                    return None
                if current["expires_at_utc"] <= now:
                    raise PermissionError("session_expired")
                if int(current["generation"]) + 1 != replacement.generation:
                    raise SessionRotationConflict(
                        "replacement_session_generation_invalid"
                    )

                cursor.execute(
                    """
                    UPDATE channel_session
                    SET invalidated_at_utc = %s,
                        rotated_to_session_ref = %s,
                        updated_at_utc = %s,
                        row_version = row_version + 1
                    WHERE session_ref = %s
                      AND row_version = %s
                    RETURNING row_version
                    """,
                    (
                        now,
                        replacement.session_ref,
                        now,
                        session_ref,
                        current["row_version"],
                    ),
                )
                if cursor.fetchone() is None:
                    raise SessionRotationConflict(
                        "predecessor_session_compare_and_swap_conflict"
                    )

                cursor.execute(
                    """
                    INSERT INTO channel_session (
                        session_ref, conversation_ref, correlation_ref, state,
                        entry_token_ref, owner_identity_ref, tenant_ref, merchant_ref,
                        location_ref, table_ref, dining_area_ref, entry_purpose,
                        context_binding_ref, expires_at_utc, generation,
                        legacy_alias_ref, predecessor_session_ref,
                        rotated_to_session_ref, invalidated_at_utc,
                        cart_ref, quote_ref, order_ref, payment_ref,
                        last_upstream_evidence_ref, human_handoff_ref,
                        transition_count, row_version, created_at_utc, updated_at_utc,
                        purge_after_utc
                    )
                    VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,NULL,NULL,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s
                    )
                    """,
                    (
                        replacement.session_ref,
                        replacement.conversation_ref,
                        replacement.correlation_ref,
                        replacement.state.value,
                        replacement.entry_token_ref,
                        replacement.owner_identity_ref,
                        replacement.tenant_ref,
                        replacement.merchant_ref,
                        replacement.location_ref,
                        replacement.table_ref,
                        replacement.dining_area_ref,
                        None
                        if replacement.entry_purpose is None
                        else replacement.entry_purpose.value,
                        replacement.context_binding_ref,
                        _from_epoch(replacement.expires_at_epoch),
                        replacement.generation,
                        replacement_legacy_alias_ref,
                        replacement.predecessor_session_ref,
                        replacement.cart_ref,
                        replacement.quote_ref,
                        replacement.order_ref,
                        replacement.payment_ref,
                        replacement.last_upstream_evidence_ref,
                        replacement.human_handoff_ref,
                        replacement.transition_count,
                        now,
                        now,
                        replacement_purge_after_utc,
                    ),
                )
        return replacement


class PostgresIdentityBindingStore:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def put(
        self,
        record: IdentityBindingRecord,
        *,
        created_at_utc: datetime,
        purge_after_utc: datetime,
    ) -> None:
        with transaction(self._connection_factory) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO channel_identity_binding (
                        identity_binding_ref, identity_ref,
                        canonical_channel_subject_ref, subject_attestation_ref,
                        conversation_ref, tenant_ref, merchant_ref, location_ref,
                        table_ref, dining_area_ref, context_binding_ref,
                        issued_at_utc, expires_at_utc, row_version,
                        created_at_utc, updated_at_utc, purge_after_utc
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s)
                    """,
                    (
                        record.identity_binding_ref,
                        record.identity_ref,
                        record.canonical_channel_subject_ref,
                        record.subject_attestation_ref,
                        record.conversation_ref,
                        record.tenant_ref,
                        record.merchant_ref,
                        record.location_ref,
                        record.table_ref,
                        record.dining_area_ref,
                        record.context_binding_ref,
                        _from_epoch(record.issued_at_epoch),
                        _from_epoch(record.expires_at_epoch),
                        _utc(created_at_utc),
                        _utc(created_at_utc),
                        _utc(purge_after_utc),
                    ),
                )

    def resolve(self, identity_binding_ref: str) -> IdentityBindingRecord | None:
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM channel_identity_binding
                    WHERE identity_binding_ref = %s
                    """,
                    (identity_binding_ref,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return IdentityBindingRecord(
            identity_binding_ref=row["identity_binding_ref"],
            identity_ref=row["identity_ref"],
            canonical_channel_subject_ref=row["canonical_channel_subject_ref"],
            subject_attestation_ref=row["subject_attestation_ref"],
            conversation_ref=row["conversation_ref"],
            tenant_ref=row["tenant_ref"],
            merchant_ref=row["merchant_ref"],
            location_ref=row["location_ref"],
            table_ref=row["table_ref"],
            dining_area_ref=row["dining_area_ref"],
            context_binding_ref=row["context_binding_ref"],
            issued_at_epoch=_to_epoch(row["issued_at_utc"]),
            expires_at_epoch=_to_epoch(row["expires_at_utc"]),
        )

    def resolve_active_for_session_context(
        self,
        session: CustomerSessionSnapshot,
        *,
        now_utc: datetime,
    ) -> IdentityBindingRecord | None:
        """Re-establish one unexpired binding for the durable session context."""

        if (
            session.owner_identity_ref is None
            or session.merchant_ref is None
            or session.location_ref is None
            or session.context_binding_ref is None
        ):
            return None
        now = _utc(now_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM channel_identity_binding
                    WHERE identity_ref = %s
                      AND conversation_ref = %s
                      AND tenant_ref IS NOT DISTINCT FROM %s
                      AND merchant_ref = %s
                      AND location_ref = %s
                      AND table_ref IS NOT DISTINCT FROM %s
                      AND dining_area_ref IS NOT DISTINCT FROM %s
                      AND context_binding_ref = %s
                      AND expires_at_utc > %s
                    ORDER BY issued_at_utc DESC, identity_binding_ref
                    LIMIT 2
                    """,
                    (
                        session.owner_identity_ref,
                        session.conversation_ref,
                        session.tenant_ref,
                        session.merchant_ref,
                        session.location_ref,
                        session.table_ref,
                        session.dining_area_ref,
                        session.context_binding_ref,
                        now,
                    ),
                )
                first = cursor.fetchone()
                second = cursor.fetchone()
        if second is not None:
            raise ActiveIdentityBindingAmbiguous(
                "active_identity_binding_ambiguous"
            )
        if first is None:
            return None
        return IdentityBindingRecord(
            identity_binding_ref=first["identity_binding_ref"],
            identity_ref=first["identity_ref"],
            canonical_channel_subject_ref=first["canonical_channel_subject_ref"],
            subject_attestation_ref=first["subject_attestation_ref"],
            conversation_ref=first["conversation_ref"],
            tenant_ref=first["tenant_ref"],
            merchant_ref=first["merchant_ref"],
            location_ref=first["location_ref"],
            table_ref=first["table_ref"],
            dining_area_ref=first["dining_area_ref"],
            context_binding_ref=first["context_binding_ref"],
            issued_at_epoch=_to_epoch(first["issued_at_utc"]),
            expires_at_epoch=_to_epoch(first["expires_at_utc"]),
        )


class PostgresEntryTokenStore:
    """Single-use claim durability with one atomic conditional update."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def put(
        self,
        record: EntryTokenRecord,
        *,
        created_at_utc: datetime,
        purge_after_utc: datetime,
    ) -> None:
        now = _utc(created_at_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO entry_token_claim (
                        token_ref, tenant_ref, merchant_ref, location_ref,
                        table_ref, dining_area_ref, purpose, context_binding_ref,
                        token_version, replay_policy, consumed_at_utc,
                        expires_at_utc, row_version, created_at_utc,
                        updated_at_utc, purge_after_utc
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s)
                    """,
                    (
                        record.token_ref,
                        record.tenant_ref,
                        record.merchant_ref,
                        record.location_ref,
                        record.table_ref,
                        record.dining_area_ref,
                        record.purpose.value,
                        record.context_binding_ref,
                        record.version,
                        record.replay_policy.value,
                        _optional_from_epoch(record.consumed_at_epoch),
                        _from_epoch(record.expires_at_epoch),
                        now,
                        now,
                        _utc(purge_after_utc),
                    ),
                )

    def consume_if_unconsumed(
        self,
        token_ref: str,
        consumed_at_epoch: int,
    ) -> EntryTokenRecord | None:
        consumed = _from_epoch(consumed_at_epoch)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    UPDATE entry_token_claim
                    SET consumed_at_utc = %s,
                        updated_at_utc = %s,
                        row_version = row_version + 1
                    WHERE token_ref = %s
                      AND consumed_at_utc IS NULL
                      AND expires_at_utc > %s
                    RETURNING *
                    """,
                    (consumed, consumed, token_ref, consumed),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return _entry_token_from_row(row)


class PostgresProvenanceStore:
    """Opaque server-issued provenance handles; never authoritative ledger truth."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def claim_confirmation(
        self,
        *,
        provenance_handle_ref: str,
        client_submit_ref: str,
        updated_at_utc: datetime,
    ) -> DurableProvenanceHandle:
        now = _utc(updated_at_utc)
        with transaction(self._connection_factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM provenance_handle
                    WHERE provenance_handle_ref = %s
                    FOR UPDATE
                    """,
                    (provenance_handle_ref,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise KeyError(provenance_handle_ref)
                existing = row["client_submit_ref_claim"]
                if existing is not None and existing != client_submit_ref:
                    raise ProvenanceHandleConflict(
                        "confirmation_handle_client_submit_conflict"
                    )
                if existing is None:
                    cursor.execute(
                        """
                        UPDATE provenance_handle
                        SET client_submit_ref_claim = %s,
                            updated_at_utc = %s,
                            row_version = row_version + 1
                        WHERE provenance_handle_ref = %s
                          AND row_version = %s
                        RETURNING *
                        """,
                        (
                            client_submit_ref,
                            now,
                            provenance_handle_ref,
                            row["row_version"],
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise ProvenanceHandleConflict(
                            "provenance_handle_compare_and_swap_conflict"
                        )
        return _provenance_from_row(row)


def _session_from_row(
    row: dict[str, Any],
    *,
    security_binding_complete: bool,
) -> CustomerSessionSnapshot:
    purpose = row["entry_purpose"]
    return CustomerSessionSnapshot(
        session_ref=row["session_ref"],
        conversation_ref=row["conversation_ref"],
        correlation_ref=row["correlation_ref"],
        state=ChannelState(row["state"]),
        entry_token_ref=row["entry_token_ref"],
        owner_identity_ref=row["owner_identity_ref"],
        tenant_ref=row["tenant_ref"],
        merchant_ref=row["merchant_ref"],
        location_ref=row["location_ref"],
        table_ref=row["table_ref"],
        dining_area_ref=row["dining_area_ref"],
        entry_purpose=None if purpose is None else EntryPurpose(purpose),
        context_binding_ref=row["context_binding_ref"],
        created_at_epoch=_to_epoch(row["created_at_utc"]),
        expires_at_epoch=_to_epoch(row["expires_at_utc"]),
        generation=int(row["generation"]),
        predecessor_session_ref=row["predecessor_session_ref"],
        rotated_to_session_ref=row["rotated_to_session_ref"],
        invalidated_at_epoch=_optional_to_epoch(row["invalidated_at_utc"]),
        security_binding_complete=security_binding_complete,
        cart_ref=row["cart_ref"],
        quote_ref=row["quote_ref"],
        order_ref=row["order_ref"],
        payment_ref=row["payment_ref"],
        last_upstream_evidence_ref=row["last_upstream_evidence_ref"],
        human_handoff_ref=row["human_handoff_ref"],
        transition_count=int(row["transition_count"]),
    )


def _entry_token_from_row(row: dict[str, Any]) -> EntryTokenRecord:
    return EntryTokenRecord(
        token_ref=row["token_ref"],
        tenant_ref=row["tenant_ref"],
        merchant_ref=row["merchant_ref"],
        location_ref=row["location_ref"],
        table_ref=row["table_ref"],
        dining_area_ref=row["dining_area_ref"],
        purpose=EntryPurpose(row["purpose"]),
        context_binding_ref=row["context_binding_ref"],
        expires_at_epoch=_to_epoch(row["expires_at_utc"]),
        version=int(row["token_version"]),
        replay_policy=ReplayPolicy(row["replay_policy"]),
        consumed_at_epoch=_optional_to_epoch(row["consumed_at_utc"]),
    )


def _provenance_from_row(row: dict[str, Any]) -> DurableProvenanceHandle:
    return DurableProvenanceHandle(
        provenance_handle_ref=row["provenance_handle_ref"],
        handle_kind=row["handle_kind"],
        session_ref=row["session_ref"],
        session_generation=int(row["session_generation"]),
        correlation_ref=row["correlation_ref"],
        upstream_ref=row["upstream_ref"],
        binding_json=dict(row["binding_json"]),
        customer_safe_projection_json=(
            None
            if row["customer_safe_projection_json"] is None
            else dict(row["customer_safe_projection_json"])
        ),
        commercial_fingerprint=row["commercial_fingerprint"],
        client_submit_ref_claim=row["client_submit_ref_claim"],
        row_version=int(row["row_version"]),
    )


def _from_epoch(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _optional_from_epoch(value: int | None) -> datetime | None:
    return None if value is None else _from_epoch(value)


def _to_epoch(value: datetime) -> int:
    return int(_utc(value).timestamp())


def _optional_to_epoch(value: datetime | None) -> int | None:
    return None if value is None else _to_epoch(value)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timezone_aware_datetime_required")
    return value.astimezone(timezone.utc)

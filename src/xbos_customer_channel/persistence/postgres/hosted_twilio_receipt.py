"""Durable TEST-only Twilio sandbox replay/processing receipt.

A signed, enrolled, fixed-sandbox ingress invokes this boundary. Only hashed
provider event identities are stored, never provider IDs, phone numbers, raw
messages, tokens or merchant information. No permission to dispatch commerce
is inferred from owning a receipt.
"""
from __future__ import annotations

import hmac
import re
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row

from .database import ConnectionFactory, PostgresUnavailable, transaction

STAGING_DATABASE = "xafpay_customer_channel_staging_db"
PROVIDER = "twilio_whatsapp"
ENDPOINT = "hosted_sandbox_test_only"


class HostedTwilioReceiptLedger:
    """One durable processing claim with replay-safe retry/finish transitions."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._factory = connection_factory

    @staticmethod
    def _valid_key(key: str) -> bool:
        return isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key) is not None

    @staticmethod
    def _assert_staging(cursor) -> None:
        cursor.execute("SELECT current_database() AS name")
        row = cursor.fetchone()
        if row is None or row["name"] != STAGING_DATABASE:
            raise PostgresUnavailable("not_approved_cc_staging_database")
        cursor.execute("""
            SELECT rolname, rolsuper, rolcreaterole, rolcreatedb,
                   rolbypassrls, rolreplication,
                   session_user AS authenticated_role
            FROM pg_roles WHERE rolname = current_user
        """)
        role = cursor.fetchone()
        if (not role or any(role.get(flag) for flag in (
            "rolsuper", "rolcreaterole", "rolcreatedb",
            "rolbypassrls", "rolreplication"
        )) or role.get("authenticated_role") != role.get("rolname")
        or not str(role.get("rolname", "")).startswith("cc_swes_receipt_")):
            raise PostgresUnavailable("receipt_role_not_least_privileged")
        cursor.execute("""
            SELECT has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
                   has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
                   has_table_privilege(current_user, 'public.provider_message_receipt', 'SELECT') AS can_select,
                   has_table_privilege(current_user, 'public.provider_message_receipt', 'INSERT') AS can_insert,
                   has_table_privilege(current_user, 'public.provider_message_receipt', 'UPDATE') AS can_update,
                   has_table_privilege(current_user, 'public.provider_message_receipt', 'DELETE') AS can_delete,
                   has_table_privilege(current_user, 'public.provider_message_receipt', 'TRUNCATE') AS can_truncate,
                   has_table_privilege(current_user, 'public.provider_message_receipt', 'REFERENCES') AS can_reference,
                   has_table_privilege(current_user, 'public.provider_message_receipt', 'TRIGGER') AS can_trigger
        """)
        grants = cursor.fetchone()
        if (not grants or not all(grants.get(key) is True for key in (
            "schema_usage", "can_select", "can_insert", "can_update"
        )) or any(grants.get(key) is not False for key in (
            "schema_create", "can_delete", "can_truncate",
            "can_reference", "can_trigger"
        ))):
            raise PostgresUnavailable("receipt_table_privilege_scope_not_admitted")

    def check_and_claim(self, *, key: str, digest: str, now_epoch: int) -> str:
        if (
            not self._valid_key(key)
            or not self._valid_key(digest)
            or type(now_epoch) is not int
        ):
            return "unavailable"
        now = datetime.fromtimestamp(now_epoch, tz=timezone.utc)
        with transaction(self._factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                self._assert_staging(cursor)
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
                        gen_random_uuid(),%s,%s,%s,'message',
                        NULL,NULL,%s,%s,'hosted_sandbox_signed',
                        'processing',%s,1,NULL,NULL,1,%s,%s,%s
                    )
                    ON CONFLICT (provider_code, provider_endpoint_ref,
                        provider_message_ref, event_kind)
                    DO NOTHING RETURNING payload_digest
                    """,
                    (PROVIDER, ENDPOINT, key, now, digest, now,
                     now, now, now + timedelta(days=7)),
                )
                if cursor.fetchone() is not None:
                    return "accepted"
                cursor.execute(
                    """
                    SELECT payload_digest,processing_state
                    FROM provider_message_receipt
                    WHERE provider_code=%s AND provider_endpoint_ref=%s
                      AND provider_message_ref=%s AND event_kind='message'
                    """,
                    (PROVIDER, ENDPOINT, key),
                )
                prior = cursor.fetchone()
                if not prior or not isinstance(prior["payload_digest"], str):
                    raise PostgresUnavailable("provider_receipt_replay_unavailable")
                if not hmac.compare_digest(prior["payload_digest"], digest):
                    return "collision"
                if prior["processing_state"] == "failed_retryable":
                    cursor.execute(
                        """
                        UPDATE provider_message_receipt
                        SET processing_state='processing',
                            processing_started_at_utc=%s,
                            processing_attempt=processing_attempt+1,
                            updated_at_utc=%s,
                            row_version=row_version+1
                        WHERE provider_code=%s AND provider_endpoint_ref=%s
                          AND provider_message_ref=%s AND event_kind='message'
                          AND processing_state='failed_retryable'
                        RETURNING provider_message_receipt_ref
                        """,
                        (now, now, PROVIDER, ENDPOINT, key),
                    )
                    if cursor.fetchone() is not None:
                        return "accepted"
                return "duplicate"

    def finish_claim(self, *, key: str, now_epoch: int, success: bool) -> None:
        if not self._valid_key(key) or type(now_epoch) is not int:
            raise PostgresUnavailable("invalid_durable_claim_identity")
        now = datetime.fromtimestamp(now_epoch, tz=timezone.utc)
        new_state = "processed" if success else "failed_retryable"
        with transaction(self._factory) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                self._assert_staging(cursor)
                cursor.execute(
                    """
                    UPDATE provider_message_receipt
                    SET processing_state=%s, updated_at_utc=%s,
                        row_version=row_version+1
                    WHERE provider_code=%s AND provider_endpoint_ref=%s
                      AND provider_message_ref=%s AND event_kind='message'
                      AND processing_state='processing'
                    RETURNING provider_message_receipt_ref
                    """,
                    (new_state, now, PROVIDER, ENDPOINT, key),
                )
                if cursor.fetchone() is None:
                    raise PostgresUnavailable("durable_receipt_transition_conflict")

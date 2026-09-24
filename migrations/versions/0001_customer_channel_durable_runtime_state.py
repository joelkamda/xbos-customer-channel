"""Customer Channel durable runtime state foundation.

Revision ID: cc_h1s3_0001
Revises:
Create Date: 2026-09-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "cc_h1s3_0001"
down_revision = None
branch_labels = None
depends_on = None


def _timestamps(*, include_purge: bool = True) -> list[sa.Column]:
    columns: list[sa.Column] = [
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
    ]
    if include_purge:
        columns.append(
            sa.Column("purge_after_utc", sa.DateTime(timezone=True), nullable=False)
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "channel_conversation",
        sa.Column("conversation_ref", sa.Text(), primary_key=True),
        sa.Column("channel_code", sa.Text(), nullable=False),
        sa.Column("provider_endpoint_ref", sa.Text(), nullable=False),
        sa.Column("external_user_lookup_hash", sa.String(length=64), nullable=False),
        sa.Column("locator_hash_key_version", sa.Text(), nullable=False),
        sa.Column("identity_ref", sa.Text(), nullable=True),
        sa.Column("last_activity_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.CheckConstraint("row_version >= 1", name="ck_channel_conversation_row_version"),
    )
    op.create_index(
        "uq_channel_conversation_active_locator",
        "channel_conversation",
        [
            "channel_code",
            "provider_endpoint_ref",
            "external_user_lookup_hash",
            "locator_hash_key_version",
        ],
        unique=True,
        postgresql_where=sa.text("closed_at_utc IS NULL"),
    )

    op.create_table(
        "channel_session",
        sa.Column("session_ref", sa.Text(), primary_key=True),
        sa.Column(
            "conversation_ref",
            sa.Text(),
            sa.ForeignKey("channel_conversation.conversation_ref", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("correlation_ref", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("entry_token_ref", sa.Text(), nullable=True),
        sa.Column("owner_identity_ref", sa.Text(), nullable=True),
        sa.Column("tenant_ref", sa.Text(), nullable=True),
        sa.Column("merchant_ref", sa.Text(), nullable=True),
        sa.Column("location_ref", sa.Text(), nullable=True),
        sa.Column("table_ref", sa.Text(), nullable=True),
        sa.Column("dining_area_ref", sa.Text(), nullable=True),
        sa.Column("entry_purpose", sa.Text(), nullable=True),
        sa.Column("context_binding_ref", sa.Text(), nullable=True),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("legacy_alias_ref", sa.Text(), nullable=True),
        sa.Column("predecessor_session_ref", sa.Text(), nullable=True),
        sa.Column("rotated_to_session_ref", sa.Text(), nullable=True),
        sa.Column("invalidated_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cart_ref", sa.Text(), nullable=True),
        sa.Column("quote_ref", sa.Text(), nullable=True),
        sa.Column("order_ref", sa.Text(), nullable=True),
        sa.Column("payment_ref", sa.Text(), nullable=True),
        sa.Column("last_upstream_evidence_ref", sa.Text(), nullable=True),
        sa.Column("human_handoff_ref", sa.Text(), nullable=True),
        sa.Column("transition_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint(
            "conversation_ref",
            "generation",
            name="uq_channel_session_conversation_generation",
        ),
        sa.CheckConstraint("generation >= 0", name="ck_channel_session_generation"),
        sa.CheckConstraint("row_version >= 1", name="ck_channel_session_row_version"),
    )
    op.create_index(
        "uq_channel_session_legacy_alias",
        "channel_session",
        ["legacy_alias_ref"],
        unique=True,
        postgresql_where=sa.text("legacy_alias_ref IS NOT NULL"),
    )
    op.create_index(
        "ix_channel_session_expiry",
        "channel_session",
        ["expires_at_utc"],
        unique=False,
    )

    op.create_table(
        "w1_runtime_state",
        sa.Column(
            "session_ref",
            sa.Text(),
            sa.ForeignKey("channel_session.session_ref", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("state_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column(
            "interaction_state_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("last_provider_message_ref", sa.Text(), nullable=True),
        sa.Column("last_transition_ref", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("row_version >= 1", name="ck_w1_runtime_state_row_version"),
        sa.CheckConstraint(
            "state_schema_version >= 1",
            name="ck_w1_runtime_state_schema_version",
        ),
    )

    op.create_table(
        "channel_identity_binding",
        sa.Column("identity_binding_ref", sa.Text(), primary_key=True),
        sa.Column("identity_ref", sa.Text(), nullable=False),
        sa.Column("canonical_channel_subject_ref", sa.Text(), nullable=False),
        sa.Column("subject_attestation_ref", sa.Text(), nullable=False),
        sa.Column(
            "conversation_ref",
            sa.Text(),
            sa.ForeignKey("channel_conversation.conversation_ref", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_ref", sa.Text(), nullable=True),
        sa.Column("merchant_ref", sa.Text(), nullable=False),
        sa.Column("location_ref", sa.Text(), nullable=False),
        sa.Column("table_ref", sa.Text(), nullable=True),
        sa.Column("dining_area_ref", sa.Text(), nullable=True),
        sa.Column("context_binding_ref", sa.Text(), nullable=False),
        sa.Column("issued_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_channel_identity_binding_row_version",
        ),
    )
    op.create_index(
        "ix_channel_identity_binding_identity_ref",
        "channel_identity_binding",
        ["identity_ref"],
        unique=False,
    )

    op.create_table(
        "provider_message_receipt",
        sa.Column(
            "provider_message_receipt_ref",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("provider_code", sa.Text(), nullable=False),
        sa.Column("provider_endpoint_ref", sa.Text(), nullable=False),
        sa.Column("provider_message_ref", sa.Text(), nullable=False),
        sa.Column("event_kind", sa.Text(), nullable=False),
        sa.Column("conversation_ref", sa.Text(), nullable=True),
        sa.Column("sender_lookup_hash", sa.String(length=64), nullable=True),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("normalized_action_code", sa.Text(), nullable=True),
        sa.Column("processing_state", sa.Text(), nullable=False),
        sa.Column("processing_started_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "idempotency_result_ref",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "transport_delivery_ref",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint(
            "provider_code",
            "provider_endpoint_ref",
            "provider_message_ref",
            "event_kind",
            name="uq_provider_message_receipt_event",
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name="ck_provider_message_receipt_row_version",
        ),
    )

    op.create_table(
        "idempotency_result",
        sa.Column(
            "idempotency_result_ref",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("conversation_ref", sa.Text(), nullable=True),
        sa.Column("session_ref", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column(
            "safe_result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint(
            "scope",
            "idempotency_key",
            name="uq_idempotency_result_scope_key",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_idempotency_result_row_version"),
    )

    op.create_table(
        "provenance_handle",
        sa.Column("provenance_handle_ref", sa.Text(), primary_key=True),
        sa.Column("handle_kind", sa.Text(), nullable=False),
        sa.Column("session_ref", sa.Text(), nullable=False),
        sa.Column("session_generation", sa.Integer(), nullable=False),
        sa.Column("correlation_ref", sa.Text(), nullable=False),
        sa.Column("upstream_ref", sa.Text(), nullable=True),
        sa.Column(
            "binding_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "customer_safe_projection_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("commercial_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("client_submit_ref_claim", sa.Text(), nullable=True),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.CheckConstraint("row_version >= 1", name="ck_provenance_handle_row_version"),
    )
    op.create_index(
        "ix_provenance_handle_session_ref",
        "provenance_handle",
        ["session_ref"],
        unique=False,
    )

    op.create_table(
        "transport_delivery",
        sa.Column("delivery_ref", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "receipt_ref",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "provider_message_receipt.provider_message_receipt_ref",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("conversation_ref", sa.Text(), nullable=True),
        sa.Column("render_ordinal", sa.Integer(), nullable=False),
        sa.Column("recipient_lookup_hash", sa.String(length=64), nullable=False),
        sa.Column("message_kind", sa.Text(), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("delivery_state", sa.Text(), nullable=False),
        sa.Column("provider_code", sa.Text(), nullable=False),
        sa.Column("provider_message_ref", sa.Text(), nullable=True),
        sa.Column("send_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_occurred_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_error_code", sa.Text(), nullable=True),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint(
            "receipt_ref",
            "render_ordinal",
            name="uq_transport_delivery_receipt_ordinal",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_transport_delivery_row_version"),
    )
    op.create_index(
        "uq_transport_delivery_provider_message",
        "transport_delivery",
        ["provider_code", "provider_message_ref"],
        unique=True,
        postgresql_where=sa.text("provider_message_ref IS NOT NULL"),
    )

    op.create_table(
        "entry_token_claim",
        sa.Column("token_ref", sa.Text(), primary_key=True),
        sa.Column("tenant_ref", sa.Text(), nullable=True),
        sa.Column("merchant_ref", sa.Text(), nullable=False),
        sa.Column("location_ref", sa.Text(), nullable=False),
        sa.Column("table_ref", sa.Text(), nullable=True),
        sa.Column("dining_area_ref", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("context_binding_ref", sa.Text(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("replay_policy", sa.Text(), nullable=False),
        sa.Column("consumed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.CheckConstraint("row_version >= 1", name="ck_entry_token_claim_row_version"),
    )


def downgrade() -> None:
    op.drop_table("entry_token_claim")
    op.drop_index(
        "uq_transport_delivery_provider_message",
        table_name="transport_delivery",
    )
    op.drop_table("transport_delivery")
    op.drop_index("ix_provenance_handle_session_ref", table_name="provenance_handle")
    op.drop_table("provenance_handle")
    op.drop_table("idempotency_result")
    op.drop_table("provider_message_receipt")
    op.drop_index(
        "ix_channel_identity_binding_identity_ref",
        table_name="channel_identity_binding",
    )
    op.drop_table("channel_identity_binding")
    op.drop_table("w1_runtime_state")
    op.drop_index("ix_channel_session_expiry", table_name="channel_session")
    op.drop_index("uq_channel_session_legacy_alias", table_name="channel_session")
    op.drop_table("channel_session")
    op.drop_index(
        "uq_channel_conversation_active_locator",
        table_name="channel_conversation",
    )
    op.drop_table("channel_conversation")

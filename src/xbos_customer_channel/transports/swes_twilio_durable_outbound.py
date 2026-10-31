"""Default-OFF durable TEST-only provider REST sends with no automatic retry.

Use the existing PostgreSQL idempotency_result store. Its unique (scope,key)
reservation must commit before an external provider request. An uncertain
outcome remains blocked pending independent provider-side reconciliation.
This module is NOT registered in the hosted application runtime.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from ..channel_contract import ChannelOutboundIntent, outbound_payload_digest
from ..persistence.postgres.transport_state_stores import IdempotencyResult
from .swes_twilio_sandbox_outbound_gate import SWESSandboxStatusSender
from .twilio_whatsapp import TwilioSendResult, TwilioWhatsAppOutboundAdapter

_SCOPE = "swes:cc:twilio_sandbox_status:v1"
_PROVIDER_SID = re.compile(r"SM[A-Za-z0-9]{6,64}\Z")


class SWESOutboundDeliveryUncertain(RuntimeError):
    """Never retry this logical notification without provider reconciliation."""


class SWESOutboundFingerprintConflict(PermissionError):
    """Same logical notification was requested with a changed body or context."""


class DurableIdempotencyPort(Protocol):
    def reserve(
        self, *, scope: str, idempotency_key: str,
        request_fingerprint: str, conversation_ref: str | None,
        session_ref: str | None, now_utc: datetime,
        purge_after_utc: datetime,
    ) -> tuple[IdempotencyResult, bool]: ...

    def mark_unknown(
        self, *, idempotency_result_ref: UUID,
        expected_row_version: int, now_utc: datetime,
    ) -> IdempotencyResult: ...

    def complete(
        self, *, idempotency_result_ref: UUID,
        expected_row_version: int, result_ref: str | None,
        safe_result_json: dict[str, object] | None,
        completed_at_utc: datetime,
    ) -> IdempotencyResult: ...


@dataclass(frozen=True, slots=True)
class SWESOutboundDeliveryResult:
    provider_message_ref: str
    duplicate: bool


class SWESDurableSandboxStatusSender:
    """Claim before send; completed duplicates return prior ref; uncertain never retry."""

    def __init__(
        self, *,
        gate: SWESSandboxStatusSender,
        provider: TwilioWhatsAppOutboundAdapter,
        store: DurableIdempotencyPort,
    ) -> None:
        self._gate = gate
        self._provider = provider
        self._store = store

    def send(
        self, *, intent: ChannelOutboundIntent, now: datetime,
    ) -> SWESOutboundDeliveryResult:
        # This check MUST occur before any durable mutation or provider action.
        self._gate.authorize(intent=intent, now=now)
        if not isinstance(intent.projection.get("body"), str) or not intent.projection["body"].strip():
            raise SWESOutboundFingerprintConflict("nonempty_authoritative_status_body_required")
        if not intent.correlation_id.strip() or not intent.session_ref.strip():
            raise SWESOutboundFingerprintConflict("stable_status_correlation_required")
        identity = "\x00".join((
            intent.merchant_ref, intent.conversation_ref, intent.session_ref,
            intent.recipient, intent.event_type, intent.correlation_id,
        ))
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        fingerprint = outbound_payload_digest(intent)
        instant = now.astimezone(timezone.utc)
        try:
            row, created = self._store.reserve(
                scope=_SCOPE,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                conversation_ref=intent.conversation_ref,
                session_ref=intent.session_ref,
                now_utc=instant,
                purge_after_utc=instant + timedelta(days=7),
            )
        except Exception as exc:
            # Any DB failure must prevent a provider request.
            raise SWESOutboundDeliveryUncertain("durable_delivery_reservation_unavailable") from None

        if not created:
            if row.request_fingerprint != fingerprint:
                raise SWESOutboundFingerprintConflict("outbound_fingerprint_conflict")
            if row.status == "completed" and isinstance(row.result_ref, str) and _PROVIDER_SID.fullmatch(row.result_ref):
                return SWESOutboundDeliveryResult(row.result_ref, duplicate=True)
            raise SWESOutboundDeliveryUncertain("outbound_already_reserved_or_unknown_do_not_retry")

        try:
            response: TwilioSendResult = self._provider.send(intent)
            if not _PROVIDER_SID.fullmatch(response.provider_message_ref):
                raise SWESOutboundDeliveryUncertain("provider_acknowledgement_unqualified")
            self._store.complete(
                idempotency_result_ref=row.idempotency_result_ref,
                expected_row_version=row.row_version,
                result_ref=response.provider_message_ref,
                safe_result_json=None,
                completed_at_utc=instant,
            )
            return SWESOutboundDeliveryResult(response.provider_message_ref, duplicate=False)
        except Exception:
            # A timeout/5xx/429, malformed 2xx, or DB finalization failure may
            # have happened AFTER provider acceptance. Never auto-resend.
            try:
                self._store.mark_unknown(
                    idempotency_result_ref=row.idempotency_result_ref,
                    expected_row_version=row.row_version,
                    now_utc=instant,
                )
            except Exception:
                # If completion committed before an exception, mark_unknown CAS
                # is expected to fail. The same unique key still blocks replay.
                pass
            raise SWESOutboundDeliveryUncertain(
                "provider_or_finalization_outcome_uncertain_reconcile_before_retry"
            ) from None

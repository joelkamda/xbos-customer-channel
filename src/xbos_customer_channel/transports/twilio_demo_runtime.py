"""Controlled Twilio WhatsApp transport qualification runtime.

This runtime intentionally stops before commerce when no already-authorized
Customer Channel session exists. It records provider receipt and outbound
delivery state using the accepted durable stores without creating a session,
calling XBOS, or performing payment.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..application.command_validation import CommandValidator
from ..application.w1_command_mapping import propose_w1_command
from ..channel_contract import (
    ChannelCommand,
    ChannelInboundEvent,
    ChannelOutboundIntent,
    CommandValidationResult,
)
from ..persistence.postgres.outbound_intent_store import (
    PostgresChannelOutboundIntentStore,
)
from ..persistence.postgres.transport_state_stores import (
    PostgresProviderMessageReceiptStore,
    PostgresTransportDeliveryStore,
)
from ..session_state import CustomerSessionSnapshot
from .twilio_whatsapp import (
    TWILIO_PROVIDER_CODE,
    TwilioChannelContext,
    TwilioInboundMessage,
    TwilioWhatsAppConfig,
    TwilioWhatsAppInboundAdapter,
    TwilioWhatsAppOutboundAdapter,
    canonical_twilio_form_digest,
    recipient_lookup_hash,
)


ContextResolver = Callable[
    [TwilioInboundMessage, str],
    tuple[TwilioChannelContext, CustomerSessionSnapshot | None],
]


@dataclass(frozen=True, slots=True)
class TwilioDemoTransportResult:
    duplicate: bool
    event: ChannelInboundEvent | None
    command: ChannelCommand | None
    validation: CommandValidationResult | None
    session_blocker: str | None
    receipt_ref: str
    delivery_ref: str | None
    outbound_state: str | None
    provider_message_ref: str | None


def unbound_context_resolver(
    message: TwilioInboundMessage,
    channel_event_id: str,
) -> tuple[TwilioChannelContext, None]:
    del message
    return TwilioChannelContext.unbound(channel_event_id), None


class TwilioWhatsAppDemoRuntime:
    """One-message controlled transport proof, never a commerce authority."""

    def __init__(
        self,
        *,
        config: TwilioWhatsAppConfig,
        inbound: TwilioWhatsAppInboundAdapter,
        outbound: TwilioWhatsAppOutboundAdapter,
        receipt_store: PostgresProviderMessageReceiptStore,
        delivery_store: PostgresTransportDeliveryStore,
        context_resolver: ContextResolver = unbound_context_resolver,
        command_validator: CommandValidator | None = None,
    ) -> None:
        self._config = config
        self._inbound = inbound
        self._outbound = outbound
        self._receipt_store = receipt_store
        self._delivery = PostgresChannelOutboundIntentStore(delivery_store)
        self._context_resolver = context_resolver
        self._validator = command_validator or CommandValidator(
            real_xbos_write_available=False
        )

    def handle(
        self,
        *,
        form: dict[str, str],
        signature: str | None,
        received_at: datetime | None = None,
    ) -> TwilioDemoTransportResult:
        now = (received_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        message = self._inbound.parse_verified(
            form=form,
            signature=signature,
            received_at=now,
        )
        provisional_event_id = _event_id_for_message(message)
        context, trusted_session = self._context_resolver(
            message,
            provisional_event_id,
        )
        event = self._inbound.to_channel_event(
            message=message,
            context=context,
        )
        if event.channel_event_id != provisional_event_id:
            raise RuntimeError("twilio_event_identity_not_stable")

        receipt, created = self._receipt_store.get_or_create(
            provider_code=TWILIO_PROVIDER_CODE,
            provider_endpoint_ref=message.normalized_recipient,
            provider_message_ref=message.provider_message_ref,
            event_kind="message",
            conversation_ref=(
                context.conversation_ref if context.trusted_session else None
            ),
            sender_lookup_hash=recipient_lookup_hash(
                auth_token=self._config.auth_token,
                recipient=message.normalized_sender,
            ),
            occurred_at_utc=message.occurred_at,
            payload_digest=canonical_twilio_form_digest(form),
            normalized_action_code=_normalized_action(message.body),
            now_utc=now,
            purge_after_utc=now + timedelta(days=7),
        )
        if not created:
            return TwilioDemoTransportResult(
                duplicate=True,
                event=None,
                command=None,
                validation=None,
                session_blocker=None,
                receipt_ref=str(receipt.provider_message_receipt_ref),
                delivery_ref=None,
                outbound_state=None,
                provider_message_ref=None,
            )

        processing = self._receipt_store.begin_processing(
            receipt_ref=receipt.provider_message_receipt_ref,
            expected_row_version=receipt.row_version,
            now_utc=now,
        )

        command = propose_w1_command(event)
        validation: CommandValidationResult | None = None
        session_blocker: str | None = None
        if command is not None:
            if trusted_session is None:
                session_blocker = "trusted_session_absent"
            else:
                validation = self._validator.validate(
                    event=event,
                    command=command,
                    session=trusted_session,
                    now_epoch=int(now.timestamp()),
                )

        intent = _transport_qualification_intent(
            event=event,
            session_blocker=session_blocker,
            validation=validation,
        )
        reservation = self._delivery.reserve(
            intent=intent,
            receipt_ref=processing.provider_message_receipt_ref,
            render_ordinal=0,
            recipient_lookup_hash=recipient_lookup_hash(
                auth_token=self._config.auth_token,
                recipient=message.normalized_sender,
            ),
            provider_code=TWILIO_PROVIDER_CODE,
            now_utc=now,
            purge_after_utc=now + timedelta(days=7),
        )

        try:
            provider_result = self._outbound.send(intent)
        except Exception as exc:
            failed = self._delivery.record_send_outcome(
                reservation=reservation,
                delivery_state="failed",
                provider_message_ref=None,
                provider_error_code=type(exc).__name__,
                now_utc=now,
            )
            self._receipt_store.finish_processing(
                receipt_ref=processing.provider_message_receipt_ref,
                expected_row_version=processing.row_version,
                processing_state="failed_retryable",
                idempotency_result_ref=None,
                transport_delivery_ref=failed.delivery_ref,
                now_utc=now,
            )
            return TwilioDemoTransportResult(
                duplicate=False,
                event=event,
                command=command,
                validation=validation,
                session_blocker=session_blocker,
                receipt_ref=str(processing.provider_message_receipt_ref),
                delivery_ref=str(failed.delivery_ref),
                outbound_state="failed",
                provider_message_ref=None,
            )

        sent = self._delivery.record_send_outcome(
            reservation=reservation,
            delivery_state="sent",
            provider_message_ref=provider_result.provider_message_ref,
            provider_error_code=None,
            now_utc=now,
        )
        self._receipt_store.finish_processing(
            receipt_ref=processing.provider_message_receipt_ref,
            expected_row_version=processing.row_version,
            processing_state="processed",
            idempotency_result_ref=None,
            transport_delivery_ref=sent.delivery_ref,
            now_utc=now,
        )
        return TwilioDemoTransportResult(
            duplicate=False,
            event=event,
            command=command,
            validation=validation,
            session_blocker=session_blocker,
            receipt_ref=str(processing.provider_message_receipt_ref),
            delivery_ref=str(sent.delivery_ref),
            outbound_state="sent",
            provider_message_ref=provider_result.provider_message_ref,
        )


def _transport_qualification_intent(
    *,
    event: ChannelInboundEvent,
    session_blocker: str | None,
    validation: CommandValidationResult | None,
) -> ChannelOutboundIntent:
    if session_blocker is not None:
        body = (
            "XafPay Customer Channel transport is connected. "
            "Commerce session is not active for this controlled test identity."
        )
    elif validation is not None and not validation.may_dispatch:
        body = (
            "XafPay Customer Channel transport is connected. "
            "The requested action is not authorized in the current session."
        )
    else:
        body = "XafPay Customer Channel transport qualification passed."

    return ChannelOutboundIntent(
        recipient=event.sender_identity,
        channel=event.channel,
        merchant_ref=event.merchant_ref,
        conversation_ref=event.conversation_ref,
        session_ref=event.session_ref,
        purpose="transport_qualification",
        event_type="twilio_demo_response",
        projection={"kind": "text", "body": body},
        template_variables={},
        correlation_id=event.correlation_ref,
    )


def _event_id_for_message(message: TwilioInboundMessage) -> str:
    from ..channel_contract import stable_channel_event_id

    return stable_channel_event_id(
        channel="whatsapp",
        recipient_identity=message.normalized_recipient,
        provider_message_id=message.provider_message_ref,
    )


def _normalized_action(body: str) -> str:
    normalized = body.strip().casefold()
    if not normalized:
        return "text:empty"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"text:{digest}"

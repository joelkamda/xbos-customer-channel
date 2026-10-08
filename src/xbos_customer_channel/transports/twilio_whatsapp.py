"""Twilio WhatsApp sandbox transport adapter.

Twilio remains a transport boundary only. Provider credentials, request
signature rules, sender formatting and REST request details stay in this
module. Domain code sees only ChannelInboundEvent / ChannelOutboundIntent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlencode

import httpx

from ..channel_contract import (
    ChannelInboundEvent,
    ChannelOutboundIntent,
    stable_channel_event_id,
)


TWILIO_PROVIDER_CODE = "twilio_whatsapp"
WHATSAPP_PREFIX = "whatsapp:"


class TwilioWebhookRejected(ValueError):
    pass


class TwilioWebhookSignatureMissing(TwilioWebhookRejected):
    pass


class TwilioWebhookSignatureInvalid(TwilioWebhookRejected):
    pass


class TwilioPayloadRejected(TwilioWebhookRejected):
    pass


class TwilioProviderError(RuntimeError):
    def __init__(self, *, status_code: int, provider_code: int | None) -> None:
        self.status_code = status_code
        self.provider_code = provider_code
        super().__init__(f"twilio_provider_error:{status_code}")


@dataclass(frozen=True, slots=True)
class TwilioWhatsAppConfig:
    account_sid: str
    auth_token: str = field(repr=False)
    sender: str
    webhook_url: str

    def __post_init__(self) -> None:
        if not self.account_sid.startswith("AC"):
            raise ValueError("twilio_account_sid_required")
        if not self.auth_token:
            raise ValueError("twilio_auth_token_required")
        if not self.sender.startswith(WHATSAPP_PREFIX):
            raise ValueError("twilio_whatsapp_sender_required")
        if not self.webhook_url.startswith("https://"):
            raise ValueError("twilio_https_webhook_url_required")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "TwilioWhatsAppConfig":
        source = os.environ if environ is None else environ
        try:
            return cls(
                account_sid=source["TWILIO_ACCOUNT_SID"],
                auth_token=source["TWILIO_AUTH_TOKEN"],
                sender=source["TWILIO_WHATSAPP_FROM"],
                webhook_url=source["TWILIO_WHATSAPP_WEBHOOK_URL"],
            )
        except KeyError as error:
            raise ValueError(
                f"missing_twilio_whatsapp_configuration:{error.args[0]}"
            ) from None


@dataclass(frozen=True, slots=True)
class TwilioChannelContext:
    conversation_ref: str
    session_ref: str
    merchant_ref: str
    subject_ref: str
    correlation_ref: str
    trusted_session: bool

    @classmethod
    def unbound(cls, channel_event_id: str) -> "TwilioChannelContext":
        suffix = channel_event_id[-16:]
        return cls(
            conversation_ref=f"transport-unbound:conversation:{suffix}",
            session_ref=f"transport-unbound:session:{suffix}",
            merchant_ref="transport-unbound:merchant",
            subject_ref=f"transport-unbound:subject:{suffix}",
            correlation_ref=f"transport-proof:{suffix}",
            trusted_session=False,
        )


@dataclass(frozen=True, slots=True)
class TwilioInboundMessage:
    provider_message_ref: str
    sender: str
    recipient: str
    body: str
    occurred_at: datetime
    account_sid: str | None
    wa_id: str | None

    def __post_init__(self) -> None:
        if not self.provider_message_ref:
            raise TwilioPayloadRejected("twilio_message_sid_missing")
        if not self.sender.startswith(WHATSAPP_PREFIX):
            raise TwilioPayloadRejected("twilio_whatsapp_sender_missing")
        if not self.recipient.startswith(WHATSAPP_PREFIX):
            raise TwilioPayloadRejected("twilio_whatsapp_recipient_missing")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise TwilioPayloadRejected("twilio_received_at_must_be_aware")

    @property
    def normalized_sender(self) -> str:
        return self.sender.removeprefix(WHATSAPP_PREFIX)

    @property
    def normalized_recipient(self) -> str:
        return self.recipient.removeprefix(WHATSAPP_PREFIX)


@dataclass(frozen=True, slots=True)
class TwilioSendResult:
    provider_message_ref: str


@dataclass(frozen=True, slots=True)
class TwilioHttpResponse:
    status_code: int
    body: bytes


class TwilioHttpClient(Protocol):
    def post_form(
        self,
        *,
        url: str,
        account_sid: str,
        auth_token: str,
        form: Mapping[str, str],
        timeout_seconds: float,
    ) -> TwilioHttpResponse: ...


class HttpxTwilioHttpClient:
    def post_form(
        self,
        *,
        url: str,
        account_sid: str,
        auth_token: str,
        form: Mapping[str, str],
        timeout_seconds: float,
    ) -> TwilioHttpResponse:
        response = httpx.post(
            url,
            auth=(account_sid, auth_token),
            data=dict(form),
            timeout=timeout_seconds,
        )
        return TwilioHttpResponse(response.status_code, response.content)


def twilio_signature(
    *,
    auth_token: str,
    url: str,
    form: Mapping[str, str],
) -> str:
    material = url + "".join(
        f"{key}{form[key]}"
        for key in sorted(form)
    )
    digest = hmac.new(
        auth_token.encode("utf-8"),
        material.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_twilio_signature(
    *,
    config: TwilioWhatsAppConfig,
    form: Mapping[str, str],
    signature: str | None,
) -> None:
    if signature is None:
        raise TwilioWebhookSignatureMissing("missing_x_twilio_signature")
    expected = twilio_signature(
        auth_token=config.auth_token,
        url=config.webhook_url,
        form=form,
    )
    if not hmac.compare_digest(expected, signature):
        raise TwilioWebhookSignatureInvalid("invalid_x_twilio_signature")


class TwilioWhatsAppInboundAdapter:
    def __init__(self, config: TwilioWhatsAppConfig) -> None:
        self._config = config

    def parse_verified(
        self,
        *,
        form: Mapping[str, str],
        signature: str | None,
        received_at: datetime | None = None,
    ) -> TwilioInboundMessage:
        verify_twilio_signature(
            config=self._config,
            form=form,
            signature=signature,
        )
        when = received_at or datetime.now(timezone.utc)
        message_sid = _required(form, "MessageSid")
        sender = _required(form, "From")
        recipient = _required(form, "To")
        body = form.get("Body", "")
        account_sid = form.get("AccountSid")
        wa_id = form.get("WaId")
        return TwilioInboundMessage(
            provider_message_ref=message_sid,
            sender=sender,
            recipient=recipient,
            body=body,
            occurred_at=when.astimezone(timezone.utc),
            account_sid=account_sid,
            wa_id=wa_id,
        )

    def receive(
        self,
        *,
        form: Mapping[str, str],
        signature: str | None,
        context: TwilioChannelContext,
        received_at: datetime | None = None,
    ) -> ChannelInboundEvent:
        message = self.parse_verified(
            form=form,
            signature=signature,
            received_at=received_at,
        )
        return self.to_channel_event(message=message, context=context)

    @staticmethod
    def to_channel_event(
        *,
        message: TwilioInboundMessage,
        context: TwilioChannelContext,
    ) -> ChannelInboundEvent:
        event_id = stable_channel_event_id(
            channel="whatsapp",
            recipient_identity=message.normalized_recipient,
            provider_message_id=message.provider_message_ref,
        )
        safe_metadata = {
            "provider": TWILIO_PROVIDER_CODE,
            "timestamp_source": "server_received",
        }
        if message.wa_id:
            safe_metadata["wa_id_present"] = "yes"
        return ChannelInboundEvent(
            channel_event_id=event_id,
            provider_message_id=message.provider_message_ref,
            channel="whatsapp",
            sender_identity=message.normalized_sender,
            recipient_identity=message.normalized_recipient,
            conversation_ref=context.conversation_ref,
            session_ref=context.session_ref,
            merchant_ref=context.merchant_ref,
            subject_ref=context.subject_ref,
            occurred_at=message.occurred_at,
            message_type="text",
            payload={"text": message.body},
            correlation_ref=context.correlation_ref,
            safe_provider_metadata=safe_metadata,
        )


class TwilioWhatsAppOutboundAdapter:
    def __init__(
        self,
        *,
        config: TwilioWhatsAppConfig,
        http_client: TwilioHttpClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._config = config
        self._http = http_client or HttpxTwilioHttpClient()
        self._timeout_seconds = timeout_seconds

    def send(self, intent: ChannelOutboundIntent) -> TwilioSendResult:
        if intent.channel != "whatsapp":
            raise ValueError("twilio_whatsapp_channel_required")
        recipient = _whatsapp_address(intent.recipient)
        body = _render_intent_text(intent)
        response = self._http.post_form(
            url=(
                "https://api.twilio.com/2010-04-01/Accounts/"
                f"{self._config.account_sid}/Messages.json"
            ),
            account_sid=self._config.account_sid,
            auth_token=self._config.auth_token,
            form={
                "From": self._config.sender,
                "To": recipient,
                "Body": body,
            },
            timeout_seconds=self._timeout_seconds,
        )
        payload = _json_object(response.body)
        if not 200 <= response.status_code < 300:
            code = payload.get("code")
            raise TwilioProviderError(
                status_code=response.status_code,
                provider_code=code if isinstance(code, int) else None,
            )
        sid = payload.get("sid")
        if not isinstance(sid, str) or not sid:
            raise TwilioProviderError(
                status_code=response.status_code,
                provider_code=None,
            )
        return TwilioSendResult(sid)


def canonical_twilio_form_digest(form: Mapping[str, str]) -> str:
    return hashlib.sha256(
        urlencode(sorted(form.items())).encode("utf-8")
    ).hexdigest()


def recipient_lookup_hash(*, auth_token: str, recipient: str) -> str:
    return hmac.new(
        auth_token.encode("utf-8"),
        recipient.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _render_intent_text(intent: ChannelOutboundIntent) -> str:
    body = intent.projection.get("body")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("twilio_outbound_body_required")
    lines = [body.strip()]

    buttons = intent.projection.get("buttons")
    if isinstance(buttons, (tuple, list)):
        for entry in buttons:
            if isinstance(entry, dict):
                title = entry.get("title")
                if isinstance(title, str) and title.strip():
                    lines.append(f"- {title.strip()}")

    sections = intent.projection.get("list_sections")
    if isinstance(sections, (tuple, list)):
        for section in sections:
            if not isinstance(section, dict):
                continue
            rows = section.get("rows")
            if not isinstance(rows, (tuple, list)):
                continue
            for row in rows:
                if isinstance(row, dict):
                    title = row.get("title")
                    if isinstance(title, str) and title.strip():
                        lines.append(f"- {title.strip()}")

    rendered = "\n".join(lines)
    if len(rendered) > 1600:
        raise ValueError("twilio_outbound_text_too_long")
    return rendered


def _whatsapp_address(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith(WHATSAPP_PREFIX):
        return stripped
    if stripped.startswith("+"):
        return WHATSAPP_PREFIX + stripped
    raise ValueError("twilio_whatsapp_recipient_invalid")


def _required(form: Mapping[str, str], key: str) -> str:
    value = form.get(key)
    if not isinstance(value, str) or not value:
        raise TwilioPayloadRejected(f"twilio_{key.casefold()}_missing")
    return value


def _json_object(body: bytes) -> Mapping[str, object]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

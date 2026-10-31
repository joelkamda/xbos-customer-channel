"""Read-only evidence classification for one exact SWES Twilio sandbox transport send.

No provider network traffic, mutation, message resend, or payment status authority.
A caller retrieves bounded messages with a read-only Twilio GET and provides them
to this pure function. Never expose phone numbers, auth material, or provider SID.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping, Sequence

SANDBOX_TRANSPORT_BODY = (
    "Wine & Dine / XafPay SANDBOX TEST ONLY: outbound WhatsApp transport "
    "check. No order or payment was created. No action needed."
)
_STATUSES = frozenset((
    "accepted", "scheduled", "canceled", "queued", "sending", "sent",
    "failed", "delivered", "undelivered", "read",
))
_SID = re.compile(r"SM[0-9a-fA-F]{32}\Z")


class SWESTwilioStatusNotProven(ValueError):
    """The provided Twilio list cannot prove an unambiguous TEST send."""


@dataclass(frozen=True, slots=True)
class SWESTwilioDeliveryStatus:
    provider_status: str
    delivery_confirmed: bool
    terminal_failure: bool
    match_count: int


def reconcile_sandbox_transport_status(
    *,
    messages: Sequence[Mapping[str, object]],
    account_sid: str,
    sender: str,
    recipient: str,
    created_after_utc: datetime,
    created_before_utc: datetime,
) -> SWESTwilioDeliveryStatus:
    if (
        not re.fullmatch(r"AC[0-9a-fA-F]{32}", account_sid)
        or sender != "whatsapp:+14155238886"
        or not re.fullmatch(r"whatsapp:\+[1-9][0-9]{7,14}", recipient)
        or created_after_utc.tzinfo is None
        or created_before_utc.tzinfo is None
        or created_after_utc >= created_before_utc
        or created_before_utc - created_after_utc > timedelta(hours=2)
        or len(messages) > 30
    ):
        raise SWESTwilioStatusNotProven("sandbox_scope_or_window_invalid")
    matched: list[Mapping[str, object]] = []
    for candidate in messages:
        if not isinstance(candidate, Mapping):
            raise SWESTwilioStatusNotProven("invalid_provider_result_shape")
        if any(candidate.get(key) != val for key, val in (
            ("account_sid", account_sid), ("from", sender),
            ("to", recipient), ("body", SANDBOX_TRANSPORT_BODY),
        )):
            continue
        if candidate.get("direction") != "outbound-api":
            continue
        try:
            created = parsedate_to_datetime(str(candidate["date_created"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise SWESTwilioStatusNotProven("matched_message_date_invalid") from exc
        if created.tzinfo is None:
            raise SWESTwilioStatusNotProven("matched_message_date_unaware")
        start = created_after_utc.astimezone(timezone.utc)
        end = created_before_utc.astimezone(timezone.utc)
        if start <= created.astimezone(timezone.utc) <= end:
            matched.append(candidate)
    if len(matched) != 1:
        raise SWESTwilioStatusNotProven("zero_or_multiple_exact_sandbox_messages")
    message = matched[0]
    if not isinstance(message.get("sid"), str) or not _SID.fullmatch(message["sid"]):
        raise SWESTwilioStatusNotProven("matched_message_sid_invalid")
    status = message.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        raise SWESTwilioStatusNotProven("provider_status_unknown")
    return SWESTwilioDeliveryStatus(
        provider_status=status,
        delivery_confirmed=status in {"delivered", "read"},
        terminal_failure=status in {"failed", "undelivered", "canceled"},
        match_count=1,
    )

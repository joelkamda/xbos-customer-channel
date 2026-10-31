"""Default-disconnected WND MENU composition over trusted CC receipt/session ports.

No HTTP route, provider send, credentials, guessed merchant or synthetic fallback.
The hosted composition requires independently admitted signed/durable receipt,
server-owned session and the existing authenticated real WND catalog adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from xml.sax.saxutils import escape

from ..adapters.real_xbos_catalog import RealXBOSCatalogAdapter
from ..channel_contract import ChannelInboundEvent
from ..session_state import CustomerSessionSnapshot
from .swes_wnd_verified_menu import (
    SWESWNDMenuNotAdmitted, SWESWNDMenuReadGate, read_verified_wnd_menu,
)
from .w1_conversation import W1RenderKind, W1RenderedMessage


class TrustedProcessedReceiptPort(Protocol):
    def is_first_processed_signed_event(self, *, channel_event_id: str,
                                        provider_message_id: str,
                                        correlation_ref: str) -> bool: ...


class TrustedCustomerSessionPort(Protocol):
    def load_for_verified_event(self, *, channel_event_id: str,
                                correlation_ref: str) -> CustomerSessionSnapshot | None: ...


@dataclass(frozen=True, slots=True)
class SWESMenuReply:
    twiml: str
    category_count: int


def _safe_menu_twiml(message: W1RenderedMessage) -> SWESMenuReply:
    """Render only authentic W1 category names with XML escaping and size caps."""
    if (message.kind != W1RenderKind.LIST
        or len(message.list_sections) != 1
        or not message.list_sections[0].rows
        or len(message.list_sections[0].rows) > 15):
        raise SWESWNDMenuNotAdmitted("bounded_real_menu_required")
    rows = message.list_sections[0].rows
    titles: list[str] = []
    for row in rows:
        title = row.title.strip()
        if (not title or len(title) > 90
            or any(ord(char) < 32 and char not in "\t\n\r" for char in title)
            or not title.isprintable()):
            raise SWESWNDMenuNotAdmitted("invalid_catalog_section_title")
        titles.append(title)
    if len({title.casefold() for title in titles}) != len(titles):
        raise SWESWNDMenuNotAdmitted("duplicate_catalog_section_title")
    body = "Wine & Dine menu categories:\n" + "\n".join(
        f"{index}. {title}" for index, title in enumerate(titles, start=1)
    )
    if len(body) > 1450:
        raise SWESWNDMenuNotAdmitted("menu_transport_limit_exceeded")
    return SWESMenuReply(
        twiml=f"<Response><Message>{escape(body)}</Message></Response>",
        category_count=len(titles),
    )


def prepare_trusted_wnd_menu_reply(
    *, enabled: bool, gate: SWESWNDMenuReadGate, inbound: ChannelInboundEvent,
    receipts: TrustedProcessedReceiptPort, sessions: TrustedCustomerSessionPort,
    catalog: RealXBOSCatalogAdapter, now: datetime,
) -> SWESMenuReply:
    """Compose only after independently verified first durable signed receipt.

    Does NOT constitute a hosted admission: the caller MUST bind ports to
    authoritative CC receipt/session persistence and real WND private reader,
    not user-controlled booleans or webhook body metadata.
    """
    if not enabled:
        raise SWESWNDMenuNotAdmitted("wnd_menu_orchestration_disabled")
    try:
        proof = receipts.is_first_processed_signed_event(
            channel_event_id=inbound.channel_event_id,
            provider_message_id=inbound.provider_message_id,
            correlation_ref=inbound.correlation_ref,
        )
    except Exception as exc:
        raise SWESWNDMenuNotAdmitted("durable_signed_receipt_unavailable") from exc
    if proof is not True:
        raise SWESWNDMenuNotAdmitted("durable_signed_first_receipt_required")
    try:
        session = sessions.load_for_verified_event(
            channel_event_id=inbound.channel_event_id,
            correlation_ref=inbound.correlation_ref,
        )
    except Exception as exc:
        raise SWESWNDMenuNotAdmitted("trusted_session_loader_unavailable") from exc
    if not isinstance(session, CustomerSessionSnapshot):
        raise SWESWNDMenuNotAdmitted("trusted_session_required")
    projection = read_verified_wnd_menu(
        gate=gate, session=session, inbound=inbound, catalog=catalog, now=now,
    )
    return _safe_menu_twiml(projection)

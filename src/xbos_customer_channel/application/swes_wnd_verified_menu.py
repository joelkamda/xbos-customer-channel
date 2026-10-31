"""SWES: default-OFF verified WND menu read into the existing W1 presentation.

The caller must inject a session loaded from its trusted durable state, never
build a session from provider form values. No outbound transport, order, payment,
catalog fallback, session mutation or static price is implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..adapters.real_xbos_catalog import RealXBOSCatalogAdapter
from ..channel_contract import ChannelInboundEvent
from ..session_state import ChannelState, CustomerSessionSnapshot
from .w1_conversation import (
    W1ListRow, W1ListSection, W1RenderedMessage, W1RenderKind,
)


class SWESWNDMenuNotAdmitted(PermissionError):
    """The requested catalog cannot be safely projected to this session."""


@dataclass(frozen=True, slots=True)
class SWESWNDMenuReadGate:
    enabled: bool
    merchant_admitted: bool
    service_reader_admitted: bool
    tenant_ref: str
    merchant_ref: str
    location_ref: str

    def __post_init__(self) -> None:
        if not self.tenant_ref or not self.merchant_ref or not self.location_ref:
            raise SWESWNDMenuNotAdmitted("server_owned_wnd_context_required")


def read_verified_wnd_menu(
    *,
    gate: SWESWNDMenuReadGate,
    session: CustomerSessionSnapshot,
    inbound: ChannelInboundEvent,
    catalog: RealXBOSCatalogAdapter,
    now: datetime,
) -> W1RenderedMessage:
    """Read WND XBOS-owned menu only after a server-owned context is admitted.

    A caller may use this projection after signed inbound and durable claim
    validation. Until a secure session loader and authoritative catalog reader
    have been registered, this operation must remain disconnected from the provider webhook.
    """
    if not gate.enabled or not gate.merchant_admitted or not gate.service_reader_admitted:
        raise SWESWNDMenuNotAdmitted("wnd_menu_read_not_admitted")
    if not isinstance(catalog, RealXBOSCatalogAdapter):
        raise SWESWNDMenuNotAdmitted("real_xbos_catalog_reader_required")
    if now.tzinfo is None or now.utcoffset() is None:
        raise SWESWNDMenuNotAdmitted("server_clock_required")
    epoch = int(now.timestamp())
    if (
        not session.security_binding_complete
        or session.state not in {ChannelState.BROWSING, ChannelState.CART}
        or not session.owner_identity_ref
        or not session.context_binding_ref
        or session.context_binding_ref == "legacy_unattested"
        or not session.correlation_ref
        or not session.expires_at_epoch
        or epoch >= session.expires_at_epoch
        or session.invalidated_at_epoch is not None
        or session.rotated_to_session_ref is not None
        or session.tenant_ref != gate.tenant_ref
        or session.merchant_ref != gate.merchant_ref
        or session.location_ref != gate.location_ref
    ):
        raise SWESWNDMenuNotAdmitted("trusted_wnd_session_not_admitted")
    message_text = inbound.payload.get("text")
    if (
        inbound.channel != "whatsapp"
        or inbound.message_type != "text"
        or not isinstance(message_text, str)
        or message_text.strip().casefold() != "menu"
        or inbound.conversation_ref != session.conversation_ref
        or inbound.session_ref != session.session_ref
        or inbound.merchant_ref != session.merchant_ref
        or inbound.subject_ref != session.owner_identity_ref
        or inbound.correlation_ref != session.correlation_ref
        or inbound.occurred_at.astimezone(timezone.utc) > now.astimezone(timezone.utc)
    ):
        raise SWESWNDMenuNotAdmitted("signed_inbound_session_correlation_required")

    try:
        projection = catalog.get_catalog_bound(
            context_binding_ref=session.context_binding_ref,
            merchant_ref=gate.merchant_ref,
            location_ref=gate.location_ref,
            effective_at=now,
            correlation_ref=session.correlation_ref,
        )
    except Exception as exc:
        # Never fall back to fake catalogs or leak private bearer/host details.
        raise SWESWNDMenuNotAdmitted("real_wnd_catalog_unavailable") from exc

    if (
        projection.merchant_ref != gate.merchant_ref
        or projection.location_ref != gate.location_ref
        or not projection.sections
    ):
        raise SWESWNDMenuNotAdmitted("wnd_catalog_scope_or_content_unavailable")
    categories = tuple(
        W1ListRow(f"section:{section.section_ref}", section.name)
        for section in sorted(projection.sections, key=lambda section: section.sort_order)
    )
    return W1RenderedMessage(
        W1RenderKind.LIST,
        "Welcome. Choose a menu category.",
        list_button_label="Menu",
        list_sections=(W1ListSection("Categories", categories),),
    )

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from .catalog import QuoteLineSnapshot
from .order import AuthoritativeOrderConfirmationSnapshot, CommercialChargeSnapshot, ServiceMode
from .session_state import CustomerSessionSnapshot, UpstreamStateProjection


class ProvenanceConflict(RuntimeError):
    """A server-issued provenance handle was reused with conflicting semantics."""


@dataclass(frozen=True, slots=True)
class SessionSecurityBinding:
    """CR1 session/context binding copied into a server-owned provenance record."""

    session_ref: str
    session_generation: int
    owner_identity_ref: str | None
    entry_token_ref: str | None
    tenant_ref: str | None
    merchant_ref: str | None
    location_ref: str | None
    table_ref: str | None
    dining_area_ref: str | None
    context_binding_ref: str | None
    correlation_ref: str


@dataclass(frozen=True, slots=True)
class EvidenceProvenanceRecord:
    """Server-issued locator for an internally obtained upstream state projection."""

    evidence_handle_ref: str
    binding: SessionSecurityBinding
    projection: UpstreamStateProjection


@dataclass(frozen=True, slots=True)
class ServerIssuedConfirmation:
    """Customer-safe confirmation presentation plus an opaque server-issued handle.

    The opaque handle is the only value accepted by order submission. The XBOS
    confirmation reference is intentionally not exposed as submit authority.
    """

    confirmation_handle_ref: str
    quote_ref: str
    quote_version: str
    merchant_ref: str
    location_ref: str
    service_context_ref: str
    service_mode: ServiceMode
    lines: tuple[QuoteLineSnapshot, ...]
    delivery_fee: Decimal | None
    taxes_charges: tuple[CommercialChargeSnapshot, ...]
    total: Decimal
    currency: str
    expires_at_epoch: int


@dataclass(frozen=True, slots=True)
class ConfirmationProvenanceRecord:
    """Server-owned binding between an opaque handle and authoritative XBOS material."""

    confirmation_handle_ref: str
    authoritative_xbos_confirmation_ref: str
    binding: SessionSecurityBinding
    quote_ref: str
    quote_version: str
    service_context_ref: str
    service_mode: ServiceMode
    commercial_fingerprint: str
    authoritative_snapshot: AuthoritativeOrderConfirmationSnapshot
    client_submit_ref_claim: str | None = None


def binding_from_session(session: CustomerSessionSnapshot) -> SessionSecurityBinding:
    return SessionSecurityBinding(
        session_ref=session.session_ref,
        session_generation=session.generation,
        owner_identity_ref=session.owner_identity_ref,
        entry_token_ref=session.entry_token_ref,
        tenant_ref=session.tenant_ref,
        merchant_ref=session.merchant_ref,
        location_ref=session.location_ref,
        table_ref=session.table_ref,
        dining_area_ref=session.dining_area_ref,
        context_binding_ref=session.context_binding_ref,
        correlation_ref=session.correlation_ref,
    )


def binding_matches_session(binding: SessionSecurityBinding, session: CustomerSessionSnapshot) -> bool:
    return binding == binding_from_session(session)


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def confirmation_commercial_fingerprint(snapshot: AuthoritativeOrderConfirmationSnapshot) -> str:
    """Stable fingerprint of authoritative material whose change requires re-confirmation."""

    payload = {
        "confirmation_ref": snapshot.confirmation_ref,
        "quote_ref": snapshot.quote_ref,
        "quote_version": snapshot.quote_version,
        "merchant_ref": snapshot.merchant_ref,
        "location_ref": snapshot.location_ref,
        "service_context_ref": snapshot.service_context_ref,
        "service_mode": snapshot.service_mode.value,
        "lines": [
            {
                "item_ref": line.item_ref,
                "quantity": line.quantity,
                "option_refs": list(line.option_refs),
                "unit_price": _decimal(line.unit_price),
                "line_total": _decimal(line.line_total),
                "availability": line.availability.value,
            }
            for line in snapshot.lines
        ],
        "delivery_fee": _decimal(snapshot.delivery_fee),
        "taxes_charges": [
            {
                "charge_ref": charge.charge_ref,
                "label": charge.label,
                "amount": _decimal(charge.amount),
            }
            for charge in snapshot.taxes_charges
        ],
        "total": _decimal(snapshot.total),
        "currency": snapshot.currency,
        "expires_at_epoch": snapshot.expires_at_epoch,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def presentation_from_record(record: ConfirmationProvenanceRecord) -> ServerIssuedConfirmation:
    snapshot = record.authoritative_snapshot
    return ServerIssuedConfirmation(
        confirmation_handle_ref=record.confirmation_handle_ref,
        quote_ref=snapshot.quote_ref,
        quote_version=snapshot.quote_version,
        merchant_ref=snapshot.merchant_ref,
        location_ref=snapshot.location_ref,
        service_context_ref=snapshot.service_context_ref,
        service_mode=snapshot.service_mode,
        lines=tuple(snapshot.lines),
        delivery_fee=snapshot.delivery_fee,
        taxes_charges=tuple(snapshot.taxes_charges),
        total=snapshot.total,
        currency=snapshot.currency,
        expires_at_epoch=snapshot.expires_at_epoch,
    )

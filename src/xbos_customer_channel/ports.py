from __future__ import annotations

from typing import Protocol, Sequence

from .catalog import CatalogProjection, CommercialQuoteSnapshot, InteractionCart
from .entry_context import EntryContextAttestation, EntryPurpose, EntryTokenRecord, MerchantContextProjection
from .identity import ChannelIdentity, ChannelSubjectResolution, ConsentPurpose, ConsentRecord, IdentityBindingRecord
from .order import AuthoritativeOrderConfirmationSnapshot, CanonicalOrderProjection, OrderSubmitRequest, ServiceMode, ServiceModeProjection
from .payment_experience import CanonicalPaymentRequestProjection
from .provenance import ConfirmationProvenanceRecord, EvidenceProvenanceRecord, ServerIssuedConfirmation
from .order_lifecycle import (
    CancellationDecision,
    CancellationRequest,
    FulfillmentProjection,
    OrderChangeDecision,
    OrderChangeRequest,
    OrderLifecycleProjection,
)
from .models import (
    EntryContext,
    MenuItem,
    OrderRef,
    OutboundMessage,
    PaymentRequest,
    PaymentState,
    Quote,
    Receipt,
)
from .session_state import CustomerSessionSnapshot, UpstreamStateProjection


class XBOSCommercePort(Protocol):
    def resolve_entry_context(self, entry_ref: str) -> EntryContext: ...
    def get_menu(self, context: EntryContext) -> Sequence[MenuItem]: ...
    def resolve_quote(self, context: EntryContext, item_refs: Sequence[str]) -> Quote: ...
    def open_order(self, quote: Quote, idempotency_key: str) -> OrderRef: ...
    def update_order(self, order_ref: str, item_refs: Sequence[str], idempotency_key: str) -> OrderRef: ...
    def confirm_order(self, order_ref: str, idempotency_key: str) -> OrderRef: ...
    def get_order_status(self, order_ref: str) -> OrderRef: ...
    def get_receipt(self, order_ref: str) -> Receipt: ...
    def request_cancel_or_correction(self, order_ref: str, reason: str, idempotency_key: str) -> OrderRef: ...


class XBOSContextPort(Protocol):
    """Future-facing XBOS customer-safe context boundary; fake/typed only until IA0/F facade freezes."""

    def resolve_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        purpose: EntryPurpose,
    ) -> MerchantContextProjection: ...

    def attest_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        purpose: EntryPurpose,
        dining_area_ref: str | None = None,
    ) -> EntryContextAttestation: ...


class EntryTokenStorePort(Protocol):
    """Channel entry/session infrastructure only. Never a merchant/location/table master."""

    def put(self, record: EntryTokenRecord) -> None: ...
    def get(self, token_ref: str) -> EntryTokenRecord | None: ...
    def consume_if_unconsumed(self, token_ref: str, consumed_at_epoch: int) -> EntryTokenRecord | None: ...


class XBOSCatalogPort(Protocol):
    """Future-facing XBOS SO1/R2 catalog + quote boundary; fake/typed only until IA0/F facade freezes."""

    def get_catalog(self, *, merchant_ref: str, location_ref: str) -> CatalogProjection: ...

    def resolve_quote(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        cart: InteractionCart,
        now_epoch: int,
    ) -> CommercialQuoteSnapshot: ...


class XBOSOrderPort(Protocol):
    """Future-facing XBOS Restaurant order boundary; fake/typed only until IA0/F facade freezes."""

    def resolve_service_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        mode: ServiceMode,
        table_ref: str | None,
        contact_ref: str | None,
        delivery_address_ref: str | None,
    ) -> ServiceModeProjection: ...

    def prepare_order_confirmation(
        self,
        *,
        quote: CommercialQuoteSnapshot,
        service_context: ServiceModeProjection,
        now_epoch: int,
    ) -> AuthoritativeOrderConfirmationSnapshot: ...

    def resolve_order_confirmation(self, confirmation_ref: str) -> AuthoritativeOrderConfirmationSnapshot | None: ...
    def submit_order(self, request: OrderSubmitRequest) -> CanonicalOrderProjection: ...
    def get_order_by_client_ref(self, client_submit_ref: str) -> CanonicalOrderProjection | None: ...


class XBOSOrderChangeFulfillmentPort(Protocol):
    """Future-facing XBOS R1/R2/R3 lifecycle boundary; fake/typed only until IA0/F facade freezes."""

    def request_order_change(self, request: OrderChangeRequest) -> OrderChangeDecision: ...
    def request_cancellation(self, request: CancellationRequest) -> CancellationDecision: ...
    def get_cancellation_projection(self, *, order_ref: str, correlation_ref: str) -> CancellationDecision | None: ...
    def get_fulfillment_projection(self, *, order_ref: str, correlation_ref: str) -> FulfillmentProjection | None: ...
    def reconcile_order_lifecycle(self, *, order_ref: str, correlation_ref: str) -> OrderLifecycleProjection: ...


class XBOSPaymentRequestPort(Protocol):
    """Typed XBOS payment-request projection boundary; real adapter blocked until IA0/F facade freezes."""

    def get_payment_request(
        self,
        *,
        order_ref: str,
        correlation_ref: str,
    ) -> CanonicalPaymentRequestProjection: ...


class CustomerSessionStorePort(Protocol):
    """Channel session persistence only; no authoritative order/payment state storage."""

    def put(self, session: CustomerSessionSnapshot) -> CustomerSessionSnapshot: ...
    def put_with_legacy_alias(self, session: CustomerSessionSnapshot, alias: str | None) -> CustomerSessionSnapshot: ...
    def get(self, session_ref: str) -> CustomerSessionSnapshot | None: ...
    def canonical_ref(self, session_ref: str) -> str: ...
    def idempotent_result(self, session_ref: str, idempotency_key: str) -> CustomerSessionSnapshot | None: ...
    def record_idempotent_result(
        self,
        session_ref: str,
        idempotency_key: str,
        snapshot: CustomerSessionSnapshot,
    ) -> CustomerSessionSnapshot: ...
    def rotate_if_active(
        self,
        *,
        session_ref: str,
        expected_owner_identity_ref: str,
        now_epoch: int,
        replacement: CustomerSessionSnapshot,
    ) -> CustomerSessionSnapshot | None: ...


class XBOSStateReconciliationPort(Protocol):
    """Typed state projection boundary. Real adapter remains blocked until the customer-safe facade freezes."""

    def reconcile_session(self, session: CustomerSessionSnapshot) -> UpstreamStateProjection | None: ...


class ChannelProvenanceStorePort(Protocol):
    """Server-owned CR2 provenance handles; in-memory contract until composed durability gate."""

    def issue_evidence(
        self,
        *,
        session: CustomerSessionSnapshot,
        projection: UpstreamStateProjection,
    ) -> str: ...

    def resolve_evidence(self, evidence_handle_ref: str) -> EvidenceProvenanceRecord | None: ...

    def issue_confirmation(
        self,
        *,
        session: CustomerSessionSnapshot,
        confirmation: AuthoritativeOrderConfirmationSnapshot,
    ) -> ServerIssuedConfirmation: ...

    def resolve_confirmation(self, confirmation_handle_ref: str) -> ConfirmationProvenanceRecord | None: ...

    def claim_confirmation(
        self,
        *,
        confirmation_handle_ref: str,
        client_submit_ref: str,
    ) -> ConfirmationProvenanceRecord: ...


class PaymentPort(Protocol):
    def create_payment_request(self, quote: Quote, idempotency_key: str) -> PaymentRequest: ...
    def get_payment_status(self, payment_ref: str) -> PaymentState: ...
    def cancel_payment_request_if_supported(self, payment_ref: str, idempotency_key: str) -> PaymentState: ...


class TransportPort(Protocol):
    def send_message(self, customer_ref: str, body: str, idempotency_key: str) -> OutboundMessage: ...
    def receive_event(self, event_ref: str) -> dict[str, str]: ...
    def acknowledge_event(self, event_ref: str) -> None: ...


class CustomerIdentityPort(Protocol):
    """Typed Channel identity/Party boundary. Caller locators are never identity authority."""

    def resolve_channel_subject(
        self,
        *,
        channel: str,
        channel_user_ref: str,
        subject_evidence_ref: str,
        now_epoch: int,
    ) -> ChannelSubjectResolution: ...

    def resolve_identity(
        self,
        *,
        channel: str,
        identity_ref: str,
        canonical_channel_subject_ref: str,
        subject_attestation_ref: str,
        conversation_ref: str,
        candidate_party_refs: tuple[str, ...],
    ) -> ChannelIdentity: ...

    def verify_identity(
        self,
        *,
        identity_ref: str,
        verification_evidence_ref: str,
        now_epoch: int,
    ) -> ChannelIdentity: ...

    def link_verified_party(
        self,
        *,
        identity_ref: str,
        party_ref: str,
        verification_ref: str,
        tenant_ref: str | None,
        merchant_ref: str,
        context_binding_ref: str,
    ) -> ChannelIdentity: ...

    def restrict_identity(self, *, identity_ref: str, restriction_ref: str) -> ChannelIdentity: ...

    def record_consent(
        self,
        *,
        identity_ref: str,
        purpose: ConsentPurpose,
        granted: bool,
        consent_version: str,
        evidence_ref: str,
        idempotency_key: str,
    ) -> ConsentRecord: ...

    def consent_for(self, *, identity_ref: str, purpose: ConsentPurpose) -> ConsentRecord | None: ...


class ConsentPolicyPort(Protocol):
    """Server-governed current consent-policy version source."""

    def current_version(self, purpose: ConsentPurpose) -> str: ...


class IdentityBindingStorePort(Protocol):
    """Server-issued CR3 session identity binding store; in-memory contract in this tranche."""

    def issue_binding(
        self,
        *,
        identity_ref: str,
        canonical_channel_subject_ref: str,
        subject_attestation_ref: str,
        conversation_ref: str,
        tenant_ref: str | None,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        dining_area_ref: str | None,
        context_binding_ref: str,
        issued_at_epoch: int,
        expires_at_epoch: int,
    ) -> IdentityBindingRecord: ...

    def resolve_binding(self, identity_binding_ref: str) -> IdentityBindingRecord | None: ...

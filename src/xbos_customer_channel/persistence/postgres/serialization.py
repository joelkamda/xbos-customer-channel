from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ...application.catalog_service import CatalogSession
from ...application.w1_checkout_ux import (
    W1CheckoutStage,
    W1CheckoutState,
    W1CustomerSafePaymentOptions,
)
from ...application.w1_conversation import W1NavigationCursor
from ...application.w1_whatsapp_runtime import W1RuntimeConversationState
from ...catalog import CartLine, CatalogProjection, InteractionCart
from ...entry_context import (
    EntryPurpose,
    MerchantContextProjection,
    ResolvedEntryContext,
)
from ...order import (
    AuthoritativeOrderConfirmationSnapshot,
    CanonicalOrderProjection,
    ServiceMode,
)
from ...payment_experience import PaymentMethodCode
from ...session_state import ChannelState, CustomerSessionSnapshot
from .schema import (
    LOCATOR_HMAC_CURRENT_KEY_ENV,
    LOCATOR_HMAC_CURRENT_VERSION_ENV,
    LOCATOR_HMAC_PREVIOUS_KEY_ENV,
    LOCATOR_HMAC_PREVIOUS_VERSION_ENV,
    STATE_SCHEMA_VERSION,
)


class StateSerializationError(ValueError):
    pass


class StateResolutionRequired(StateSerializationError):
    pass


@dataclass(frozen=True, slots=True)
class LocatorDigest:
    key_version: str
    digest: str
    needs_rehash: bool


@dataclass(frozen=True, slots=True)
class LocatorKeyRing:
    current_key: str
    current_version: str
    previous_key: str | None = None
    previous_version: str | None = None

    def __post_init__(self) -> None:
        if not self.current_key or not self.current_version:
            raise ValueError("locator_current_key_and_version_required")
        if (self.previous_key is None) != (self.previous_version is None):
            raise ValueError("locator_previous_key_and_version_must_pair")
        if self.previous_version == self.current_version:
            raise ValueError("locator_key_versions_must_differ")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "LocatorKeyRing":
        source = os.environ if environ is None else environ
        current_key = source.get(LOCATOR_HMAC_CURRENT_KEY_ENV, "")
        current_version = source.get(LOCATOR_HMAC_CURRENT_VERSION_ENV, "")
        previous_key = source.get(LOCATOR_HMAC_PREVIOUS_KEY_ENV) or None
        previous_version = source.get(LOCATOR_HMAC_PREVIOUS_VERSION_ENV) or None
        return cls(
            current_key=current_key,
            current_version=current_version,
            previous_key=previous_key,
            previous_version=previous_version,
        )

    @staticmethod
    def canonical_material(
        *,
        channel_code: str,
        provider_endpoint_ref: str,
        raw_sender: str,
    ) -> bytes:
        if not channel_code or not provider_endpoint_ref or not raw_sender:
            raise ValueError("locator_material_required")
        return (
            "v1"
            + "\x1f"
            + channel_code
            + "\x1f"
            + provider_endpoint_ref
            + "\x1f"
            + raw_sender
        ).encode("utf-8")

    @classmethod
    def derive(
        cls,
        *,
        key: str,
        channel_code: str,
        provider_endpoint_ref: str,
        raw_sender: str,
    ) -> str:
        if not key:
            raise ValueError("locator_hmac_key_required")
        material = cls.canonical_material(
            channel_code=channel_code,
            provider_endpoint_ref=provider_endpoint_ref,
            raw_sender=raw_sender,
        )
        return hmac.new(key.encode("utf-8"), material, hashlib.sha256).hexdigest()

    def current(
        self,
        *,
        channel_code: str,
        provider_endpoint_ref: str,
        raw_sender: str,
    ) -> LocatorDigest:
        return LocatorDigest(
            key_version=self.current_version,
            digest=self.derive(
                key=self.current_key,
                channel_code=channel_code,
                provider_endpoint_ref=provider_endpoint_ref,
                raw_sender=raw_sender,
            ),
            needs_rehash=False,
        )

    def candidates(
        self,
        *,
        channel_code: str,
        provider_endpoint_ref: str,
        raw_sender: str,
    ) -> tuple[LocatorDigest, ...]:
        current = self.current(
            channel_code=channel_code,
            provider_endpoint_ref=provider_endpoint_ref,
            raw_sender=raw_sender,
        )
        if self.previous_key is None or self.previous_version is None:
            return (current,)
        previous = LocatorDigest(
            key_version=self.previous_version,
            digest=self.derive(
                key=self.previous_key,
                channel_code=channel_code,
                provider_endpoint_ref=provider_endpoint_ref,
                raw_sender=raw_sender,
            ),
            needs_rehash=True,
        )
        return (current, previous)

    def match(
        self,
        *,
        stored_digest: str,
        stored_key_version: str,
        channel_code: str,
        provider_endpoint_ref: str,
        raw_sender: str,
    ) -> LocatorDigest | None:
        for candidate in self.candidates(
            channel_code=channel_code,
            provider_endpoint_ref=provider_endpoint_ref,
            raw_sender=raw_sender,
        ):
            if (
                candidate.key_version == stored_key_version
                and hmac.compare_digest(candidate.digest, stored_digest)
            ):
                return candidate
        return None


def _exact_keys(document: Mapping[str, Any], allowed: set[str], name: str) -> None:
    extras = set(document) - allowed
    missing = allowed - set(document)
    if extras or missing:
        raise StateSerializationError(
            f"{name}_shape_mismatch:missing={sorted(missing)}:extra={sorted(extras)}"
        )


def _enum_value(value: object | None) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if not isinstance(enum_value, str):
        raise StateSerializationError("enum_value_required")
    return enum_value


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def serialize_customer_session(snapshot: CustomerSessionSnapshot) -> dict[str, Any]:
    return {
        "session_ref": snapshot.session_ref,
        "conversation_ref": snapshot.conversation_ref,
        "correlation_ref": snapshot.correlation_ref,
        "state": snapshot.state.value,
        "entry_token_ref": snapshot.entry_token_ref,
        "owner_identity_ref": snapshot.owner_identity_ref,
        "tenant_ref": snapshot.tenant_ref,
        "merchant_ref": snapshot.merchant_ref,
        "location_ref": snapshot.location_ref,
        "table_ref": snapshot.table_ref,
        "dining_area_ref": snapshot.dining_area_ref,
        "entry_purpose": _enum_value(snapshot.entry_purpose),
        "context_binding_ref": snapshot.context_binding_ref,
        "created_at_epoch": snapshot.created_at_epoch,
        "expires_at_epoch": snapshot.expires_at_epoch,
        "generation": snapshot.generation,
        "predecessor_session_ref": snapshot.predecessor_session_ref,
        "rotated_to_session_ref": snapshot.rotated_to_session_ref,
        "invalidated_at_epoch": snapshot.invalidated_at_epoch,
        "cart_ref": snapshot.cart_ref,
        "quote_ref": snapshot.quote_ref,
        "order_ref": snapshot.order_ref,
        "payment_ref": snapshot.payment_ref,
        "last_upstream_evidence_ref": snapshot.last_upstream_evidence_ref,
        "human_handoff_ref": snapshot.human_handoff_ref,
        "transition_count": snapshot.transition_count,
    }


_SESSION_KEYS = {
    "session_ref",
    "conversation_ref",
    "correlation_ref",
    "state",
    "entry_token_ref",
    "owner_identity_ref",
    "tenant_ref",
    "merchant_ref",
    "location_ref",
    "table_ref",
    "dining_area_ref",
    "entry_purpose",
    "context_binding_ref",
    "created_at_epoch",
    "expires_at_epoch",
    "generation",
    "predecessor_session_ref",
    "rotated_to_session_ref",
    "invalidated_at_epoch",
    "cart_ref",
    "quote_ref",
    "order_ref",
    "payment_ref",
    "last_upstream_evidence_ref",
    "human_handoff_ref",
    "transition_count",
}


def deserialize_customer_session(
    document: Mapping[str, Any],
    *,
    security_binding_complete: bool,
) -> CustomerSessionSnapshot:
    _exact_keys(document, _SESSION_KEYS, "customer_session")
    purpose_value = document["entry_purpose"]
    return CustomerSessionSnapshot(
        session_ref=str(document["session_ref"]),
        conversation_ref=str(document["conversation_ref"]),
        correlation_ref=str(document["correlation_ref"]),
        state=ChannelState(str(document["state"])),
        entry_token_ref=_optional_text(document["entry_token_ref"]),
        owner_identity_ref=_optional_text(document["owner_identity_ref"]),
        tenant_ref=_optional_text(document["tenant_ref"]),
        merchant_ref=_optional_text(document["merchant_ref"]),
        location_ref=_optional_text(document["location_ref"]),
        table_ref=_optional_text(document["table_ref"]),
        dining_area_ref=_optional_text(document["dining_area_ref"]),
        entry_purpose=None if purpose_value is None else EntryPurpose(str(purpose_value)),
        context_binding_ref=_optional_text(document["context_binding_ref"]),
        created_at_epoch=_optional_int(document["created_at_epoch"]),
        expires_at_epoch=_optional_int(document["expires_at_epoch"]),
        generation=int(document["generation"]),
        predecessor_session_ref=_optional_text(document["predecessor_session_ref"]),
        rotated_to_session_ref=_optional_text(document["rotated_to_session_ref"]),
        invalidated_at_epoch=_optional_int(document["invalidated_at_epoch"]),
        security_binding_complete=security_binding_complete,
        cart_ref=_optional_text(document["cart_ref"]),
        quote_ref=_optional_text(document["quote_ref"]),
        order_ref=_optional_text(document["order_ref"]),
        payment_ref=_optional_text(document["payment_ref"]),
        last_upstream_evidence_ref=_optional_text(document["last_upstream_evidence_ref"]),
        human_handoff_ref=_optional_text(document["human_handoff_ref"]),
        transition_count=int(document["transition_count"]),
    )


def serialize_catalog_session(session: CatalogSession) -> dict[str, Any]:
    return {
        "entry": {
            "token_ref": session.entry.token_ref,
            "tenant_ref": session.entry.tenant_ref,
            "merchant_ref": session.entry.merchant_ref,
            "location_ref": session.entry.location_ref,
            "table_ref": session.entry.table_ref,
            "dining_area_ref": session.entry.dining_area_ref,
            "purpose": session.entry.purpose.value,
            "context_binding_ref": session.entry.context_binding_ref,
        },
        "cart_lines": [
            {
                "item_ref": line.item_ref,
                "quantity": line.quantity,
                "option_refs": list(line.option_refs),
            }
            for line in session.cart.lines
        ],
    }


def deserialize_catalog_session(
    document: Mapping[str, Any],
    *,
    entry_projection: MerchantContextProjection,
    catalog_projection: CatalogProjection,
) -> CatalogSession:
    _exact_keys(document, {"entry", "cart_lines"}, "catalog_session")
    entry_doc = _mapping(document["entry"], "catalog_entry")
    _exact_keys(
        entry_doc,
        {
            "token_ref",
            "tenant_ref",
            "merchant_ref",
            "location_ref",
            "table_ref",
            "dining_area_ref",
            "purpose",
            "context_binding_ref",
        },
        "catalog_entry",
    )
    merchant_ref = str(entry_doc["merchant_ref"])
    location_ref = str(entry_doc["location_ref"])
    if (
        catalog_projection.merchant_ref != merchant_ref
        or catalog_projection.location_ref != location_ref
        or entry_projection.merchant_ref != merchant_ref
        or entry_projection.location_ref != location_ref
    ):
        raise StateSerializationError("catalog_projection_context_mismatch")
    lines: list[CartLine] = []
    raw_lines = document["cart_lines"]
    if not isinstance(raw_lines, list):
        raise StateSerializationError("cart_lines_must_be_array")
    for item in raw_lines:
        item_doc = _mapping(item, "cart_line")
        _exact_keys(item_doc, {"item_ref", "quantity", "option_refs"}, "cart_line")
        options = item_doc["option_refs"]
        if not isinstance(options, list) or any(not isinstance(v, str) for v in options):
            raise StateSerializationError("cart_option_refs_invalid")
        lines.append(
            CartLine(
                item_ref=str(item_doc["item_ref"]),
                quantity=int(item_doc["quantity"]),
                option_refs=tuple(options),
            )
        )
    entry = ResolvedEntryContext(
        token_ref=str(entry_doc["token_ref"]),
        tenant_ref=_optional_text(entry_doc["tenant_ref"]),
        merchant_ref=merchant_ref,
        location_ref=location_ref,
        table_ref=_optional_text(entry_doc["table_ref"]),
        dining_area_ref=_optional_text(entry_doc["dining_area_ref"]),
        purpose=EntryPurpose(str(entry_doc["purpose"])),
        context_binding_ref=str(entry_doc["context_binding_ref"]),
        projection=entry_projection,
    )
    return CatalogSession(
        entry=entry,
        projection=catalog_projection,
        cart=InteractionCart(tuple(lines)),
    )


def serialize_navigation(cursor: W1NavigationCursor) -> dict[str, Any]:
    return {
        "active_section_ref": cursor.active_section_ref,
        "active_item_ref": cursor.active_item_ref,
        "quantity": cursor.quantity,
    }


def deserialize_navigation(document: Mapping[str, Any]) -> W1NavigationCursor:
    _exact_keys(
        document,
        {"active_section_ref", "active_item_ref", "quantity"},
        "navigation",
    )
    return W1NavigationCursor(
        active_section_ref=_optional_text(document["active_section_ref"]),
        active_item_ref=_optional_text(document["active_item_ref"]),
        quantity=int(document["quantity"]),
    )


def serialize_payment_options(
    options: W1CustomerSafePaymentOptions,
) -> dict[str, Any]:
    return {
        "payment_request_ref": options.payment_request_ref,
        "order_ref": options.order_ref,
        "canonical_amount": _decimal_text(options.canonical_amount),
        "currency": options.currency,
        "permitted_methods": [method.value for method in options.permitted_methods],
        "payment_request_state": options.payment_request_state,
        "safe_next_action": options.safe_next_action,
    }


def deserialize_payment_options(
    document: Mapping[str, Any],
) -> W1CustomerSafePaymentOptions:
    _exact_keys(
        document,
        {
            "payment_request_ref",
            "order_ref",
            "canonical_amount",
            "currency",
            "permitted_methods",
            "payment_request_state",
            "safe_next_action",
        },
        "payment_options",
    )
    methods = document["permitted_methods"]
    if not isinstance(methods, list):
        raise StateSerializationError("payment_methods_must_be_array")
    return W1CustomerSafePaymentOptions(
        payment_request_ref=str(document["payment_request_ref"]),
        order_ref=str(document["order_ref"]),
        canonical_amount=Decimal(str(document["canonical_amount"])),
        currency=str(document["currency"]),
        permitted_methods=tuple(PaymentMethodCode(str(value)) for value in methods),
        payment_request_state=str(document["payment_request_state"]),
        safe_next_action=str(document["safe_next_action"]),
    )


def serialize_checkout_state(state: W1CheckoutState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "stage": state.stage.value,
        "service_mode": _enum_value(state.service_mode),
        "confirmation_ref": (
            None if state.confirmation is None else state.confirmation.confirmation_ref
        ),
        "order_ref": None if state.order is None else state.order.order_ref,
        "payment_options": (
            None
            if state.payment_options is None
            else serialize_payment_options(state.payment_options)
        ),
        "selected_method": _enum_value(state.selected_method),
    }


def deserialize_checkout_state(
    document: Mapping[str, Any] | None,
    *,
    confirmation_resolver: Callable[
        [str], AuthoritativeOrderConfirmationSnapshot | None
    ]
    | None = None,
    order_resolver: Callable[[str], CanonicalOrderProjection | None] | None = None,
) -> W1CheckoutState | None:
    if document is None:
        return None
    _exact_keys(
        document,
        {
            "stage",
            "service_mode",
            "confirmation_ref",
            "order_ref",
            "payment_options",
            "selected_method",
        },
        "checkout",
    )
    confirmation_ref = _optional_text(document["confirmation_ref"])
    order_ref = _optional_text(document["order_ref"])
    confirmation = None
    order = None
    if confirmation_ref is not None:
        if confirmation_resolver is None:
            raise StateResolutionRequired("confirmation_resolver_required")
        confirmation = confirmation_resolver(confirmation_ref)
        if confirmation is None or confirmation.confirmation_ref != confirmation_ref:
            raise StateResolutionRequired("confirmation_reference_unresolved")
    if order_ref is not None:
        if order_resolver is None:
            raise StateResolutionRequired("order_resolver_required")
        order = order_resolver(order_ref)
        if order is None or order.order_ref != order_ref:
            raise StateResolutionRequired("order_reference_unresolved")
    payment_doc = document["payment_options"]
    if payment_doc is not None and not isinstance(payment_doc, Mapping):
        raise StateSerializationError("payment_options_must_be_object")
    return W1CheckoutState(
        stage=W1CheckoutStage(str(document["stage"])),
        service_mode=(
            None
            if document["service_mode"] is None
            else ServiceMode(str(document["service_mode"]))
        ),
        confirmation=confirmation,
        order=order,
        payment_options=(
            None
            if payment_doc is None
            else deserialize_payment_options(payment_doc)
        ),
        selected_method=(
            None
            if document["selected_method"] is None
            else PaymentMethodCode(str(document["selected_method"]))
        ),
    )


def serialize_runtime_state(state: W1RuntimeConversationState) -> dict[str, Any]:
    return {
        "state_schema_version": STATE_SCHEMA_VERSION,
        "session": serialize_customer_session(state.session),
        "catalog": serialize_catalog_session(state.catalog_session),
        "navigation": serialize_navigation(state.navigation),
        "checkout": serialize_checkout_state(state.checkout),
    }


def deserialize_runtime_state(
    document: Mapping[str, Any],
    *,
    entry_projection: MerchantContextProjection,
    catalog_projection: CatalogProjection,
    security_binding_complete: bool,
    confirmation_resolver: Callable[
        [str], AuthoritativeOrderConfirmationSnapshot | None
    ]
    | None = None,
    order_resolver: Callable[[str], CanonicalOrderProjection | None] | None = None,
) -> W1RuntimeConversationState:
    _exact_keys(
        document,
        {"state_schema_version", "session", "catalog", "navigation", "checkout"},
        "runtime_state",
    )
    version = int(document["state_schema_version"])
    if version != STATE_SCHEMA_VERSION:
        raise StateSerializationError("unsupported_runtime_state_schema_version")
    session_doc = _mapping(document["session"], "session")
    catalog_doc = _mapping(document["catalog"], "catalog")
    navigation_doc = _mapping(document["navigation"], "navigation")
    checkout_raw = document["checkout"]
    if checkout_raw is not None and not isinstance(checkout_raw, Mapping):
        raise StateSerializationError("checkout_must_be_object")
    return W1RuntimeConversationState(
        session=deserialize_customer_session(
            session_doc,
            security_binding_complete=security_binding_complete,
        ),
        catalog_session=deserialize_catalog_session(
            catalog_doc,
            entry_projection=entry_projection,
            catalog_projection=catalog_projection,
        ),
        navigation=deserialize_navigation(navigation_doc),
        checkout=deserialize_checkout_state(
            checkout_raw,
            confirmation_resolver=confirmation_resolver,
            order_resolver=order_resolver,
        ),
    )


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateSerializationError(f"{name}_must_be_object")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StateSerializationError("optional_text_invalid")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise StateSerializationError("optional_int_invalid")
    return value

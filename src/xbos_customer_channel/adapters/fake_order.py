from __future__ import annotations

from decimal import Decimal

from ..catalog import CommercialQuoteSnapshot
from ..order import (
    AuthoritativeOrderConfirmationSnapshot,
    CanonicalOrderProjection,
    CanonicalOrderState,
    CommercialChargeSnapshot,
    OrderSubmissionConflict,
    OrderSubmitRequest,
    OrderTransportUnknown,
    ServiceMode,
    ServiceModeProjection,
    ServiceModeUnavailable,
)


class FakeXBOSOrderClient:
    """Deterministic XBOS order-contract fixture. It simulates XBOS authority; Channel owns none of it."""

    def __init__(self) -> None:
        self._table_contexts = {
            ("merchant:fixture:alpha", "location:fixture:one", "table:fixture:a1"),
            ("merchant:fixture:beta", "location:fixture:two", "table:fixture:b1"),
        }
        self._delivery_addresses = {
            ("merchant:fixture:alpha", "location:fixture:one", "address:fixture:inside"): (
                "service-area:fixture:alpha-local",
                Decimal("750"),
            ),
        }
        self._orders_by_client_ref: dict[str, tuple[str, CanonicalOrderProjection]] = {}
        self._order_counter = 0
        self.fail_next_after_effect = False
        self.fail_next_before_effect = False

    def resolve_service_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        mode: ServiceMode,
        table_ref: str | None,
        contact_ref: str | None,
        delivery_address_ref: str | None,
    ) -> ServiceModeProjection:
        if mode is ServiceMode.DINE_IN:
            if table_ref is None or (merchant_ref, location_ref, table_ref) not in self._table_contexts:
                raise ServiceModeUnavailable("invalid_or_stale_table_context")
            return ServiceModeProjection(
                service_context_ref=f"service-context:fixture:dine:{table_ref}",
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                mode=mode,
                available=True,
                table_ref=table_ref,
                currency="XAF",
            )

        if mode is ServiceMode.TAKEAWAY:
            if not contact_ref:
                raise ServiceModeUnavailable("takeaway_contact_required")
            return ServiceModeProjection(
                service_context_ref=f"service-context:fixture:takeaway:{merchant_ref}:{location_ref}",
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                mode=mode,
                available=True,
                pickup_context_ref=f"pickup-context:fixture:{location_ref}",
                contact_ref=contact_ref,
                currency="XAF",
            )

        if mode is ServiceMode.DELIVERY:
            if not contact_ref or not delivery_address_ref:
                raise ServiceModeUnavailable("delivery_contact_and_address_required")
            delivery = self._delivery_addresses.get((merchant_ref, location_ref, delivery_address_ref))
            if delivery is None:
                raise ServiceModeUnavailable("delivery_service_area_unavailable")
            service_area_ref, delivery_fee = delivery
            return ServiceModeProjection(
                service_context_ref=f"service-context:fixture:delivery:{delivery_address_ref}",
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                mode=mode,
                available=True,
                contact_ref=contact_ref,
                delivery_address_ref=delivery_address_ref,
                service_area_ref=service_area_ref,
                delivery_fee=delivery_fee,
                currency="XAF",
            )

        raise ServiceModeUnavailable("unsupported_service_mode")

    def prepare_order_confirmation(
        self,
        *,
        quote: CommercialQuoteSnapshot,
        service_context: ServiceModeProjection,
        now_epoch: int,
    ) -> AuthoritativeOrderConfirmationSnapshot:
        if not service_context.available:
            raise ServiceModeUnavailable("service_context_unavailable")
        if (quote.merchant_ref, quote.location_ref) != (service_context.merchant_ref, service_context.location_ref):
            raise ServiceModeUnavailable("quote_service_context_mismatch")
        if quote.expires_at_epoch <= now_epoch:
            raise ServiceModeUnavailable("quote_expired")

        # These arithmetic semantics live inside the fake XBOS authority fixture, not Channel application code.
        delivery_fee = service_context.delivery_fee
        charges: tuple[CommercialChargeSnapshot, ...] = ()
        total = quote.total + (delivery_fee or Decimal("0"))
        confirmation_ref = (
            f"confirmation:fixture:{quote.version}:{service_context.mode.value}:"
            f"{service_context.service_context_ref}"
        )
        return AuthoritativeOrderConfirmationSnapshot(
            confirmation_ref=confirmation_ref,
            quote_ref=quote.quote_ref,
            quote_version=quote.version,
            merchant_ref=quote.merchant_ref,
            location_ref=quote.location_ref,
            service_context_ref=service_context.service_context_ref,
            service_mode=service_context.mode,
            lines=quote.lines,
            delivery_fee=delivery_fee,
            taxes_charges=charges,
            total=total,
            currency=quote.currency,
            expires_at_epoch=quote.expires_at_epoch,
        )

    def submit_order(self, request: OrderSubmitRequest) -> CanonicalOrderProjection:
        existing = self._orders_by_client_ref.get(request.client_submit_ref)
        if existing is not None:
            prior_confirmation_ref, prior = existing
            if prior_confirmation_ref != request.confirmation_ref:
                raise OrderSubmissionConflict("idempotency_payload_conflict")
            return prior

        if self.fail_next_before_effect:
            self.fail_next_before_effect = False
            raise OrderTransportUnknown("transport_unknown_before_authoritative_effect")

        self._order_counter += 1
        order = CanonicalOrderProjection(
            order_ref=f"order:fixture:{self._order_counter}",
            client_submit_ref=request.client_submit_ref,
            correlation_ref=request.correlation_ref,
            confirmation_ref=request.confirmation_ref,
            state=CanonicalOrderState.CONFIRMED,
            evidence_ref=f"xbos-order-evidence:fixture:{self._order_counter}",
        )
        self._orders_by_client_ref[request.client_submit_ref] = (request.confirmation_ref, order)

        if self.fail_next_after_effect:
            self.fail_next_after_effect = False
            raise OrderTransportUnknown("transport_unknown_after_authoritative_effect")
        return order

    def get_order_by_client_ref(self, client_submit_ref: str) -> CanonicalOrderProjection | None:
        existing = self._orders_by_client_ref.get(client_submit_ref)
        return existing[1] if existing is not None else None

    @property
    def canonical_order_effect_count(self) -> int:
        return len(self._orders_by_client_ref)

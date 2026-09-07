from __future__ import annotations

from dataclasses import replace

from ..entry_context import EntryContextAttestation, EntryPurpose, MerchantContextProjection


class FakeXBOSContextClient:
    """Typed contract fixture; never durable merchant/location/table authority."""

    def __init__(self) -> None:
        alpha = MerchantContextProjection(
            merchant_ref="merchant:fixture:alpha",
            location_ref="location:fixture:one",
            service_available=True,
            display_name="Fixture Merchant Alpha",
            terminology=(("table", "Table"), ("order", "Order")),
            currency="XAF",
            allowed_fulfillment_modes=("dine_in", "takeaway", "delivery"),
        )
        beta = MerchantContextProjection(
            merchant_ref="merchant:fixture:beta",
            location_ref="location:fixture:two",
            service_available=True,
            display_name="Fixture Merchant Beta",
            terminology=(("table", "Table"), ("order", "Order")),
            currency="XAF",
            allowed_fulfillment_modes=("dine_in", "takeaway"),
        )
        self._contexts: dict[tuple[str, str], tuple[str, MerchantContextProjection]] = {
            ("merchant:fixture:alpha", "location:fixture:one"): ("tenant:fixture:a", alpha),
            ("merchant:fixture:beta", "location:fixture:two"): ("tenant:fixture:b", beta),
        }
        self._tables: dict[tuple[str, str, str], tuple[str | None, str]] = {
            ("merchant:fixture:alpha", "location:fixture:one", "table:fixture:a1"): ("area:fixture:main", "ctxbind:alpha:one:a1:v1"),
            ("merchant:fixture:alpha", "location:fixture:one", "table:fixture:a2"): ("area:fixture:main", "ctxbind:alpha:one:a2:v1"),
            ("merchant:fixture:beta", "location:fixture:two", "table:fixture:b1"): (None, "ctxbind:beta:two:b1:v1"),
        }
        self._discovery_bindings: dict[tuple[str, str], str] = {
            ("merchant:fixture:alpha", "location:fixture:one"): "ctxbind:alpha:one:discovery:v1",
            ("merchant:fixture:beta", "location:fixture:two"): "ctxbind:beta:two:discovery:v1",
        }

    def reassign_table(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str,
        dining_area_ref: str | None = None,
    ) -> None:
        key = (merchant_ref, location_ref, table_ref)
        if key not in self._tables:
            raise KeyError("table_context_not_found")
        _, current_binding = self._tables[key]
        version = int(current_binding.rsplit(":v", 1)[1]) + 1
        stem = current_binding.rsplit(":v", 1)[0]
        self._tables[key] = (dining_area_ref, f"{stem}:v{version}")

    def resolve_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        purpose: EntryPurpose,
    ) -> MerchantContextProjection:
        # Frozen XC3/XC6 compatibility projection. Full CR1 membership proof uses attest_context.
        try:
            _, projection = self._contexts[(merchant_ref, location_ref)]
        except KeyError:
            raise KeyError("xbos_context_not_found") from None
        if purpose.value not in projection.allowed_fulfillment_modes and purpose is not EntryPurpose.MERCHANT_DISCOVERY:
            raise PermissionError("entry_purpose_not_available")
        if purpose is EntryPurpose.DINE_IN and table_ref is None:
            raise ValueError("table_required_for_dine_in")
        return projection

    def attest_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        purpose: EntryPurpose,
        dining_area_ref: str | None = None,
    ) -> EntryContextAttestation:
        try:
            tenant_ref, projection = self._contexts[(merchant_ref, location_ref)]
        except KeyError:
            raise KeyError("xbos_context_not_found") from None
        if purpose.value not in projection.allowed_fulfillment_modes and purpose is not EntryPurpose.MERCHANT_DISCOVERY:
            raise PermissionError("entry_purpose_not_available")
        if purpose is EntryPurpose.DINE_IN and table_ref is None:
            raise ValueError("table_required_for_dine_in")

        resolved_area: str | None = None
        if table_ref is not None:
            table_key = (merchant_ref, location_ref, table_ref)
            try:
                resolved_area, binding_ref = self._tables[table_key]
            except KeyError:
                raise PermissionError("table_not_member_of_context") from None
            if dining_area_ref is not None and resolved_area != dining_area_ref:
                raise PermissionError("dining_area_not_member_of_context")
        else:
            binding_ref = self._discovery_bindings[(merchant_ref, location_ref)]
            if dining_area_ref is not None:
                raise PermissionError("dining_area_requires_table_context")

        return EntryContextAttestation(
            tenant_ref=tenant_ref,
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            table_ref=table_ref,
            dining_area_ref=resolved_area,
            purpose=purpose,
            context_binding_ref=binding_ref,
            projection=projection,
        )

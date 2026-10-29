"""P0D R5 bounded signed-WhatsApp to WND staging test bridge.

This module is deliberately test-only and default OFF. It never derives tenant,
merchant, location, catalog item, contact, or WND authorization from WhatsApp
payload fields. All commerce authority is server-owned configuration and WND
revalidates the distinct private service bearer and context on every request.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from ..adapters.wnd_merchant_http import (
    WNDMerchantAdmissionRequired,
    WNDMerchantBinding,
)
from ..adapters.wnd_r1_order_port import WNDTrustBoundaryRejected
from ..order import OrderSubmissionConflict, OrderTransportUnknown
from .w1_composition import XBOSW1ContractUnavailable, compose_wnd_merchant_order_port

FLAG = "CC_P0D_R5_WND_BRIDGE_ENABLED"
MERCHANT_ADMITTED = "CC_P0D_R5_WND_MERCHANT_ADMITTED"
SERVICE_ADMITTED = "CC_P0D_R5_WND_SERVICE_ADMITTED"
BASE_URL = "CC_P0D_R5_WND_BASE_URL"
EXPECTED_HOST = "CC_P0D_R5_WND_EXPECTED_HOST"
BEARER = "CC_P0D_R5_WND_SERVICE_BEARER"
CREDENTIAL_REF = "CC_P0D_R5_WND_SERVICE_CREDENTIAL_REF"
TENANT_ID = "CC_P0D_R5_WND_TENANT_ID"
MERCHANT_PUBLIC_ID = "CC_P0D_R5_WND_MERCHANT_PUBLIC_ID"
LOCATION_PUBLIC_ID = "CC_P0D_R5_WND_LOCATION_PUBLIC_ID"
LOCATION_ID = "CC_P0D_R5_WND_LOCATION_ID"
CONTEXT_BINDING_PUBLIC_ID = "CC_P0D_R5_WND_CONTEXT_BINDING_PUBLIC_ID"
SERVICE_IDENTITY_PUBLIC_ID = "CC_P0D_R5_WND_SERVICE_IDENTITY_PUBLIC_ID"
CATALOG_ENTRY_REF = "CC_P0D_R5_WND_CATALOG_ENTRY_REF"
CONTACT_REF = "CC_P0D_R5_WND_CONTACT_REF"

R5_SYNTHETIC_TENANT_ID = 910001
R5_WND_STAGING_HOST = "wnd-xafpay-demo-austin-backend.onrender.com"
R5_WND_STAGING_URL = "https://" + R5_WND_STAGING_HOST


class P0DR5BridgeNotConfigured(RuntimeError):
    """The bounded staging bridge has not been explicitly admitted."""


class P0DR5BridgeRejected(PermissionError):
    """A server-owned binding or WND response violated the R5 contract."""


@dataclass(frozen=True, slots=True)
class P0DR5BridgeConfig:
    base_url: str
    expected_host: str
    bearer: str = field(repr=False)
    credential_ref: str
    tenant_id: int
    merchant_public_id: str
    location_public_id: str
    location_id: int
    context_binding_public_id: str
    service_identity_public_id: str
    catalog_entry_ref: str
    contact_ref: str
    merchant_admitted: bool
    service_admitted: bool

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> P0DR5BridgeConfig:
        env = os.environ if environ is None else environ
        if env.get(FLAG) != "1":
            raise P0DR5BridgeNotConfigured("wnd_bridge_disabled")
        if env.get(MERCHANT_ADMITTED) != "1" or env.get(SERVICE_ADMITTED) != "1":
            raise P0DR5BridgeNotConfigured("wnd_bridge_identity_not_admitted")

        try:
            tenant_id = int(env[TENANT_ID])
            location_id = int(env[LOCATION_ID])
            values = {
                "base_url": env[BASE_URL].rstrip("/"),
                "expected_host": env[EXPECTED_HOST],
                "bearer": env[BEARER],
                "credential_ref": env[CREDENTIAL_REF],
                "merchant_public_id": env[MERCHANT_PUBLIC_ID],
                "location_public_id": env[LOCATION_PUBLIC_ID],
                "context_binding_public_id": env[CONTEXT_BINDING_PUBLIC_ID],
                "service_identity_public_id": env[SERVICE_IDENTITY_PUBLIC_ID],
                "catalog_entry_ref": env[CATALOG_ENTRY_REF],
                "contact_ref": env[CONTACT_REF],
            }
        except (KeyError, ValueError) as exc:
            raise P0DR5BridgeNotConfigured("wnd_bridge_configuration_incomplete") from exc

        if tenant_id != R5_SYNTHETIC_TENANT_ID or location_id <= 0:
            raise P0DR5BridgeRejected("only_r5_synthetic_tenant_allowed")
        if values["base_url"] != R5_WND_STAGING_URL:
            raise P0DR5BridgeRejected("exact_wnd_staging_url_required")
        if values["expected_host"] != R5_WND_STAGING_HOST:
            raise P0DR5BridgeRejected("exact_wnd_staging_host_required")
        url = urlsplit(values["base_url"])
        if url.scheme != "https" or url.hostname != R5_WND_STAGING_HOST:
            raise P0DR5BridgeRejected("https_wnd_staging_required")
        if not values["credential_ref"].startswith("wnd-channel-service:"):
            raise P0DR5BridgeRejected("distinct_wnd_service_credential_ref_required")
        if (
            not values["bearer"].strip()
            or "\r" in values["bearer"]
            or "\n" in values["bearer"]
            or len(values["bearer"]) > 4096
        ):
            raise P0DR5BridgeRejected("wnd_service_bearer_invalid")

        for key in (
            "merchant_public_id",
            "location_public_id",
            "context_binding_public_id",
            "service_identity_public_id",
            "catalog_entry_ref",
            "contact_ref",
        ):
            try:
                parsed = UUID(values[key])
            except (ValueError, TypeError) as exc:
                raise P0DR5BridgeRejected("server_owned_uuid_required") from exc
            if parsed.int == 0:
                raise P0DR5BridgeRejected("server_owned_nonzero_uuid_required")

        return cls(
            base_url=values["base_url"],
            expected_host=values["expected_host"],
            bearer=values["bearer"],
            credential_ref=values["credential_ref"],
            tenant_id=tenant_id,
            merchant_public_id=values["merchant_public_id"],
            location_public_id=values["location_public_id"],
            location_id=location_id,
            context_binding_public_id=values["context_binding_public_id"],
            service_identity_public_id=values["service_identity_public_id"],
            catalog_entry_ref=values["catalog_entry_ref"],
            contact_ref=values["contact_ref"],
            merchant_admitted=True,
            service_admitted=True,
        )


@dataclass(frozen=True, slots=True)
class P0DR5ConnectedOrderResult:
    order_ref: str
    canonical_total: Decimal
    state: str


def dispatch_p0d_r5_test_order(
    *,
    event_digest: str,
    settings: P0DR5BridgeConfig | None = None,
    http_client: Any | None = None,
) -> P0DR5ConnectedOrderResult:
    """Submit one deterministic synthetic WND order after signed ingress.

    The caller may only invoke this for the explicit R5 test-order action.
    A greeting is never routed here. The event digest is a non-reversible
    correlation token derived only after provider signature and tester checks.
    """

    if not re.fullmatch(r"[0-9a-f]{20}", event_digest):
        raise P0DR5BridgeRejected("verified_event_digest_required")
    cfg = settings or P0DR5BridgeConfig.from_environment()
    correlation = "p0d-r5:" + event_digest
    binding = WNDMerchantBinding(
        tenant_id=cfg.tenant_id,
        merchant_public_id=cfg.merchant_public_id,
        location_public_id=cfg.location_public_id,
        location_id=cfg.location_id,
        context_binding_public_id=cfg.context_binding_public_id,
        service_identity_public_id=cfg.service_identity_public_id,
        correlation_ref=correlation,
    )

    owned_client = http_client is None
    client = http_client or httpx.Client(trust_env=False)
    try:
        port = compose_wnd_merchant_order_port(
            enabled=True,
            merchant_identity_admitted=cfg.merchant_admitted,
            service_identity_admitted=cfg.service_admitted,
            binding=binding,
            base_url=cfg.base_url,
            expected_host=cfg.expected_host,
            wnd_service_bearer=cfg.bearer,
            wnd_service_credential_ref=cfg.credential_ref,
            http_client=client,
        )
        create_ref = f"p0d-r5:{event_digest}:create"
        add_ref = f"p0d-r5:{event_digest}:add"
        submit_ref = f"p0d-r5:{event_digest}:submit"
        draft_ref, order_ref, confirmation_ref, canonical_total = (
            port.prepare_wnd_draft_test_only(
                catalog_entry_ref=cfg.catalog_entry_ref,
                contact_ref=cfg.contact_ref,
                create_command_ref=create_ref,
                add_command_ref=add_ref,
                quantity=2,
            )
        )
        submitted = port.submit_wnd_draft_test_only(
            draft_ref=draft_ref,
            expected_order_ref=order_ref,
            confirmation_ref=confirmation_ref,
            client_submit_ref=submit_ref,
        )
        if submitted.order_ref != order_ref:
            raise P0DR5BridgeRejected("wnd_order_identity_changed")
        if canonical_total != Decimal(3500):
            raise P0DR5BridgeRejected("wnd_server_price_not_expected_test_total")
        return P0DR5ConnectedOrderResult(
            order_ref=order_ref,
            canonical_total=canonical_total,
            state=submitted.state.value,
        )
    except (XBOSW1ContractUnavailable, WNDMerchantAdmissionRequired, OrderTransportUnknown) as exc:
        raise P0DR5BridgeNotConfigured("wnd_private_order_port_unavailable") from exc
    except (WNDTrustBoundaryRejected, OrderSubmissionConflict) as exc:
        raise P0DR5BridgeRejected("wnd_private_order_rejected") from exc
    finally:
        if owned_client:
            client.close()

"""W1 application composition over explicit, injected Customer Channel boundaries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from ..adapters.real_xbos_catalog import (
    PrivateXBOSCatalogBindingResolver,
    PrivateXBOSMenuReadClient,
    RealXBOSCatalogAdapter,
)
from ..adapters.real_xbos_context import RealXBOSContextAdapter
from ..adapters.xbos_private_http import PrivateXBOSHTTPClient
from ..ports import XBOSCatalogPort, XBOSContextPort
from ..runtime.config import RuntimeConfig
from ..transports.meta_whatsapp import MetaWhatsAppInboundAdapter, MetaWhatsAppOutboundAdapter
from .catalog_service import CatalogQuoteService
from .w1_checkout_ux import W1CheckoutUX
from .w1_conversation import W1ConversationRouter
from .w1_whatsapp_runtime import W1RuntimeStatePort, W1WhatsAppRuntime

CUSTOMER_SAFE_MERCHANT_LOCATION_MENU_PRICE_AVAILABILITY_PROJECTION = (
    "CUSTOMER_SAFE_MERCHANT_LOCATION_MENU_PRICE_AVAILABILITY_PROJECTION"
)
REAL_XBOS_ADAPTER_STATE = "WAITING_FOR_XBOS_CONTRACT"
XBOS_CATALOG_ADAPTER_FAKE = "fake"
XBOS_CATALOG_ADAPTER_REAL = "real"


class XBOSW1ContractUnavailable(RuntimeError):
    pass


def select_xbos_catalog_adapter(
    selector: str,
    *,
    fake: XBOSCatalogPort,
    real: XBOSCatalogPort | None,
) -> XBOSCatalogPort:
    """Select catalog authority explicitly; real selection never falls back to fake."""

    normalized = selector.strip().lower()
    if normalized == XBOS_CATALOG_ADAPTER_FAKE:
        return fake
    if normalized == XBOS_CATALOG_ADAPTER_REAL:
        if real is None:
            raise XBOSW1ContractUnavailable(
                "REAL_XBOS_CATALOG_RUNTIME_BINDING_UNAVAILABLE"
            )
        return real
    raise ValueError("invalid_xbos_catalog_adapter_selector")


def compose_real_xbos_boundaries(
    config: RuntimeConfig,
    *,
    http_client: httpx.Client | None = None,
    sleeper: Callable[[float], None] | None = None,
    effective_at_factory: Callable[[], datetime] | None = None,
) -> tuple[XBOSContextPort, XBOSCatalogPort]:
    """Materialize, but do not activate, the accepted private XBOS read boundaries."""

    token = config.xbos_catalog_read_token
    if not token:
        raise XBOSW1ContractUnavailable(
            "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_READ_TOKEN_REQUIRED"
        )
    client_kwargs = {
        "base_url": config.xbos_private_base_url,
        "bearer_token": token,
        "client": http_client,
    }
    if sleeper is not None:
        client_kwargs["sleeper"] = sleeper
    transport = PrivateXBOSHTTPClient(**client_kwargs)
    context = RealXBOSContextAdapter(transport)
    catalog = RealXBOSCatalogAdapter(
        client=PrivateXBOSMenuReadClient(transport),
        binding_resolver=PrivateXBOSCatalogBindingResolver(transport),
        effective_at_factory=effective_at_factory
        or (lambda: datetime.now(timezone.utc)),
    )
    return context, catalog


def require_real_xbos_w1_contract(
    *,
    context: XBOSContextPort,
    catalog: XBOSCatalogPort,
) -> None:
    """Typed boundary only; do not compose a fake or infer an XBOS HTTP API shape."""
    del context, catalog
    raise XBOSW1ContractUnavailable(REAL_XBOS_ADAPTER_STATE)


def compose_w1_whatsapp_runtime(
    *,
    inbound: MetaWhatsAppInboundAdapter,
    outbound: MetaWhatsAppOutboundAdapter,
    catalog: CatalogQuoteService,
    checkout: W1CheckoutUX,
    state_port: W1RuntimeStatePort,
) -> W1WhatsAppRuntime:
    """Compose real WhatsApp transport with Channel logic and injected domain boundaries."""
    router = W1ConversationRouter(catalog, checkout=checkout)
    return W1WhatsAppRuntime(
        inbound=inbound,
        outbound=outbound,
        router=router,
        state_port=state_port,
    )

"""HTTP composition for the controlled CC-WA-ORDER-R2 Twilio sandbox proof.

This is intentionally isolated from the production Customer Channel app. It
uses the Twilio demo runtime, existing durable transport stores, and an
operator-provided database URL. It never creates a trusted commerce session.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qsl

import psycopg
from fastapi import FastAPI, Request, Response

from ..persistence.postgres.transport_state_stores import (
    PostgresProviderMessageReceiptStore,
    PostgresTransportDeliveryStore,
)
from .twilio_demo_runtime import TwilioWhatsAppDemoRuntime
from .twilio_whatsapp import (
    TwilioWebhookRejected,
    TwilioWhatsAppConfig,
    TwilioWhatsAppInboundAdapter,
    TwilioWhatsAppOutboundAdapter,
)


def _connection_factory():
    url = os.environ.get("CC_WA_ORDER_R2_DEMO_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("missing_cc_wa_order_r2_demo_database_url")
    return psycopg.connect(url, connect_timeout=4, autocommit=False)


def _build_runtime() -> TwilioWhatsAppDemoRuntime:
    config = TwilioWhatsAppConfig.from_environment()
    return TwilioWhatsAppDemoRuntime(
        config=config,
        inbound=TwilioWhatsAppInboundAdapter(config),
        outbound=TwilioWhatsAppOutboundAdapter(config=config),
        receipt_store=PostgresProviderMessageReceiptStore(_connection_factory),
        delivery_store=PostgresTransportDeliveryStore(_connection_factory),
    )


app = FastAPI(title="XafPay Customer Channel R2 Twilio Demo")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "authority": "CC-WA-ORDER-R2"}


@app.post("/demo/twilio/whatsapp")
async def twilio_whatsapp_demo(request: Request) -> Response:
    raw = await request.body()
    form = dict(parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
    signature = request.headers.get("X-Twilio-Signature")
    try:
        result = _build_runtime().handle(
            form=form,
            signature=signature,
        )
    except TwilioWebhookRejected:
        return Response(
            content="<Response/>",
            media_type="application/xml",
            status_code=403,
        )
    except Exception:
        return Response(
            content="<Response/>",
            media_type="application/xml",
            status_code=500,
        )

    headers = {
        "X-XafPay-R2-Duplicate": "yes" if result.duplicate else "no",
        "X-XafPay-R2-Session-Blocker": result.session_blocker or "none",
        "X-XafPay-R2-Outbound-State": result.outbound_state or "none",
    }
    return Response(
        content="<Response/>",
        media_type="application/xml",
        status_code=200,
        headers=headers,
    )

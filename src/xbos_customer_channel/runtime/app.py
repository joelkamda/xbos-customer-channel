from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..transports.meta_whatsapp import (
    InvalidWebhookSignature,
    MetaWhatsAppInboundAdapter,
    MissingWebhookSignature,
    ProviderPayloadRejected,
)
from .config import RuntimeConfig


app = FastAPI(
    title="XafPay Customer Channel",
    version="xc9-h1r1",
)


def _config() -> RuntimeConfig:
    try:
        return RuntimeConfig.from_environment()
    except ValueError:
        raise HTTPException(
            status_code=503,
            detail="runtime_configuration_unavailable",
        ) from None


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "xbos-customer-channel",
        "w1_dispatch": "disabled",
    }


@app.get("/webhooks/meta/whatsapp", response_class=PlainTextResponse)
def verify_meta_subscription(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    inbound = MetaWhatsAppInboundAdapter(_config().meta)
    accepted = inbound.verify_subscription(
        mode=mode,
        verify_token=verify_token,
        challenge=challenge,
    )
    if accepted is None:
        raise HTTPException(
            status_code=403,
            detail="webhook_verification_rejected",
        )
    return PlainTextResponse(accepted, status_code=200)


@app.post("/webhooks/meta/whatsapp")
async def receive_meta_callback(request: Request) -> JSONResponse:
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    inbound = MetaWhatsAppInboundAdapter(_config().meta)

    try:
        inbound.receive(
            raw_body=raw_body,
            signature=signature,
        )
    except (MissingWebhookSignature, InvalidWebhookSignature):
        raise HTTPException(
            status_code=401,
            detail="invalid_webhook_signature",
        ) from None
    except ProviderPayloadRejected:
        raise HTTPException(
            status_code=400,
            detail="invalid_webhook_payload",
        ) from None

    return JSONResponse(
        {
            "status": "accepted",
            "w1_dispatch": "disabled",
        },
        status_code=200,
    )

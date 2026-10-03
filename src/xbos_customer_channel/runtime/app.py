from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..application.w1_composition import XBOSW1ContractUnavailable
from ..persistence.postgres.schema import DATABASE_URL_ENV
from ..transports.meta_whatsapp import (
    InvalidWebhookSignature,
    MetaWhatsAppInboundAdapter,
    MissingWebhookSignature,
    ProviderPayloadRejected,
)
from .config import RuntimeConfig
from .durable_w1 import (
    DurableW1SessionRuntime,
    compose_durable_w1_session_runtime,
)


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


def _materialize_durable_w1_session_runtime(
    request: Request,
    config: RuntimeConfig,
) -> DurableW1SessionRuntime | None:
    """Bind durable W1/session state only when the canonical DB key is present."""

    if not os.environ.get(DATABASE_URL_ENV, "").strip():
        return None

    try:
        runtime = compose_durable_w1_session_runtime(config)
    except (ValueError, XBOSW1ContractUnavailable):
        raise HTTPException(
            status_code=503,
            detail="durable_runtime_configuration_unavailable",
        ) from None

    request.app.state.durable_w1_session_runtime = runtime
    return runtime


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
    config = _config()
    inbound = MetaWhatsAppInboundAdapter(config.meta)

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

    _materialize_durable_w1_session_runtime(request, config)

    return JSONResponse(
        {
            "status": "accepted",
            "w1_dispatch": "disabled",
        },
        status_code=200,
    )

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..application.w1_composition import XBOSW1ContractUnavailable
from ..persistence.postgres.schema import DATABASE_URL_ENV
from ..transports.twilio_signed_ingress import (
    IngressNotConfigured,
    IngressRejected,
    LocalNoCommerceReplayLedger,
    SandboxIngressConfig,
    verify_sandbox_inbound,
)
from ..transports.meta_whatsapp import (
    InvalidWebhookSignature,
    MetaWhatsAppConfig,
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

# This is an ingress-only process receipt guard, NOT a durable order CAS.
_twilio_ingress_receipts = LocalNoCommerceReplayLedger()


def _config() -> RuntimeConfig:
    try:
        return RuntimeConfig.from_environment()
    except ValueError:
        raise HTTPException(
            status_code=503,
            detail="runtime_configuration_unavailable",
        ) from None


def _meta_config() -> MetaWhatsAppConfig:
    try:
        return MetaWhatsAppConfig.from_environment()
    except ValueError:
        raise HTTPException(
            status_code=503,
            detail="provider_configuration_unavailable",
        ) from None


def _materialize_durable_w1_session_runtime(
    request: Request,
    config: RuntimeConfig,
    *,
    configured_endpoint_ref: str,
) -> DurableW1SessionRuntime | None:
    """Bind durable W1/session state only when the canonical DB key is present."""

    if not os.environ.get(DATABASE_URL_ENV, "").strip():
        return None

    try:
        runtime = compose_durable_w1_session_runtime(
            config,
            configured_endpoint_ref=configured_endpoint_ref,
        )
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


@app.post("/webhooks/twilio/whatsapp")
async def receive_signed_sandbox_ingress(request: Request) -> PlainTextResponse:
    """CEO-only signed ingress proof; no customer order, reply or payment."""

    try:
        settings = SandboxIngressConfig.from_environment()
    except IngressNotConfigured:
        raise HTTPException(
            status_code=503, detail="sandbox_ingress_unavailable"
        ) from None
    try:
        # Verify configured public host; never use untrusted forwarded host
        # or scheme to construct the signature URL.
        verified = verify_sandbox_inbound(
            settings=settings,
            ledger=_twilio_ingress_receipts,
            raw_body=await request.body(),
            content_type=request.headers.get("Content-Type"),
            signature=request.headers.get("X-Twilio-Signature"),
            request_host=request.headers.get("Host"),
            forwarded_proto=request.headers.get("X-Forwarded-Proto"),
            now=datetime.now(timezone.utc),
        )
    except IngressNotConfigured:
        raise HTTPException(
            status_code=503, detail="sandbox_ingress_unavailable"
        ) from None
    except IngressRejected:
        raise HTTPException(
            status_code=403, detail="sandbox_request_rejected"
        ) from None
    # Never expose a sender, provider message ID, bearer or event correlation
    # in the HTTP response. An empty TwiML response sends no WhatsApp reply.
    return PlainTextResponse(
        "<Response></Response>",
        media_type="application/xml",
        status_code=200,
        headers={"X-XafPay-Ingress": verified.disposition},
    )


@app.get("/webhooks/meta/whatsapp", response_class=PlainTextResponse)
def verify_meta_subscription(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    inbound = MetaWhatsAppInboundAdapter(_meta_config())
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
    meta_config = _meta_config()
    inbound = MetaWhatsAppInboundAdapter(meta_config)

    try:
        inbound.receive(
            raw_body=raw_body,
            signature=request.headers.get("X-Hub-Signature-256"),
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

    config = _config()
    _materialize_durable_w1_session_runtime(
        request,
        config,
        configured_endpoint_ref=meta_config.phone_number_id,
    )

    return JSONResponse(
        {
            "status": "accepted",
            "w1_dispatch": "disabled",
        },
        status_code=200,
    )

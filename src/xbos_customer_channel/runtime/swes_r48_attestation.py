"""Default-off R48 CC receipt evidence endpoint, independently authenticated.

Read-only. Never returns a participant/session claim, sender verification, or
status paid. A WND attestor MUST NOT treat receipt-only proof as verified sender.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ..persistence.postgres.database import PostgresUnavailable
from ..transports.swes_r48_receipt_attestor import (
    ReceiptAttestationDenied,
    WNDAttestorTrust,
    attest_cc_processed_receipt,
)
from ..transports.twilio_signed_ingress import (
    IngressNotConfigured,
    SandboxIngressConfig,
)

router = APIRouter(prefix="/internal/swes/track-a/test-only")


@router.post("/receipt-attestations")
async def attest_r48_receipt(request: Request, payload: Annotated[dict, Body()]):
    """Require independent WND query identity and real restricted CC DB state."""
    try:
        trust = WNDAttestorTrust.from_environment(os.environ)
        settings = SandboxIngressConfig.from_environment()
    except (ReceiptAttestationDenied, IngressNotConfigured):
        return JSONResponse(status_code=503, content={"detail": "attestation_unavailable"})
    if (
        request.headers.get("host") != settings.expected_host
        or request.headers.get("x-forwarded-proto") not in (None, "https")
    ):
        return JSONResponse(status_code=403, content={"detail": "attestation_denied"})
    raw_auth = request.headers.get("authorization", "")
    if not raw_auth.startswith("Bearer ") or raw_auth.count(" ") != 1:
        return JSONResponse(status_code=403, content={"detail": "attestation_denied"})
    try:
        # Import existing restricted receipt factory, never the general DB URL.
        from .app import _sandbox_receipt_ledger

        ledger = _sandbox_receipt_ledger()
        result = attest_cc_processed_receipt(
            payload=payload, bearer=raw_auth[7:],
            trust=trust, settings=settings, ledger=ledger,
            now=datetime.now(UTC),
        )
        return result
    except ReceiptAttestationDenied:
        return JSONResponse(status_code=403, content={"detail": "attestation_denied"})
    except (PostgresUnavailable, IngressNotConfigured):
        return JSONResponse(status_code=503, content={"detail": "attestation_unavailable"})
    except Exception:  # noqa: BLE001 - fail closed on unexpected restricted DB errors
        return JSONResponse(status_code=503, content={"detail": "attestation_unavailable"})

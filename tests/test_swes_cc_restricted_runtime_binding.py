"""TEST-only gated receipt connection must not reuse elevated app credentials."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from xbos_customer_channel.runtime.app import _sandbox_receipt_ledger
from xbos_customer_channel.transports.twilio_signed_ingress import IngressNotConfigured

BASE = {
    "CC_HOSTED_SANDBOX_E2E_ENABLED": "1",
    "CC_HOSTED_SANDBOX_DURABLE_INGRESS_ENABLED": "1",
    "XAFPAY_CUSTOMER_CHANNEL_DATABASE_URL":
        "postgresql://owner@private-db/ccdb?sslmode=require",
}
RESTRICTED = "postgresql://cc_swes_receipt_test@private-db/ccdb?sslmode=require"

@pytest.mark.parametrize("overrides", [
    {},
    {"CC_HOSTED_SANDBOX_RECEIPT_DATABASE_URL": ""},
    {"CC_HOSTED_SANDBOX_RECEIPT_DATABASE_URL":
        BASE["XAFPAY_CUSTOMER_CHANNEL_DATABASE_URL"]},
])
def test_no_dedicated_receipt_identity_fails_closed(overrides):
    with patch.dict(os.environ, {**BASE, **overrides}, clear=True):
        with pytest.raises(IngressNotConfigured, match="restricted_receipt_identity"):
            _sandbox_receipt_ledger()

def test_distinct_receipt_identity_selected_without_network_call():
    captured = []
    with patch.dict(os.environ, {
        **BASE, "CC_HOSTED_SANDBOX_RECEIPT_DATABASE_URL": RESTRICTED
    }, clear=True), patch(
        "xbos_customer_channel.runtime.app.psycopg_connection_factory",
        side_effect=lambda c: captured.append(c.database_url) or (lambda: None)
    ):
        ledger = _sandbox_receipt_ledger()
    assert ledger is not None
    assert captured == [RESTRICTED]
    assert "owner@" not in captured[0]

def test_malformed_restricted_dsn_fails_closed():
    with patch.dict(os.environ, {
        **BASE, "CC_HOSTED_SANDBOX_RECEIPT_DATABASE_URL":
        "postgresql://cc_swes_receipt_test@private-db/ccdb?sslmode=disable"
    }, clear=True):
        with pytest.raises(IngressNotConfigured, match="staging_receipt_database"):
            _sandbox_receipt_ledger()

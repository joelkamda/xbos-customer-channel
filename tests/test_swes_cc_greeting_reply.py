"""Sandbox greeting TwiML: signed first durable receipt only; no provider sends."""
import os
import sys
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from xbos_customer_channel.runtime.app import app
from xbos_customer_channel.persistence.postgres.hosted_twilio_receipt import HostedTwilioReceiptLedger
from test_p0d_r4_twilio_signed_ingress import env, form, signed, historical_sandbox_clock

class FakeDurable(HostedTwilioReceiptLedger):
    def __init__(self):
        self.keys = set()
        self.finished = []
    def check_and_claim(self, *, key, digest, now_epoch):
        if key in self.keys:
            return "duplicate"
        self.keys.add(key)
        return "accepted"
    def finish_claim(self, *, key, now_epoch, success):
        self.finished.append(success)

def post(values):
    client = TestClient(app, base_url="https://xafpay-customer-channel-staging.onrender.com")
    return client.post("/webhooks/twilio/whatsapp", data=values,
        headers={"X-Twilio-Signature": signed(values), "X-Forwarded-Proto":"https"})

def test_greeting_requires_durable_first_receipt_and_flag():
    ledger = FakeDurable()
    settings = env()
    settings.update({"CC_HOSTED_SANDBOX_E2E_ENABLED":"1",
        "CC_HOSTED_SANDBOX_DURABLE_INGRESS_ENABLED":"1",
        "CC_TWILIO_SANDBOX_GREETING_REPLY_ENABLED":"1"})
    with patch.dict(os.environ, settings, clear=True), patch(
        "xbos_customer_channel.runtime.app._sandbox_receipt_ledger", return_value=ledger
    ):
        first = post(form(sid="SMGREET01"))
        repeat = post(form(sid="SMGREET01"))
        other = post(form(sid="SMOTHER01", body="other"))
    assert first.status_code == 200
    assert first.text.startswith("<Response><Message>")
    assert "Wine &amp; Dine" in first.text
    assert repeat.text == "<Response></Response>"
    assert other.text == "<Response></Response>"
    assert ledger.finished == [True, True]

def test_flag_off_keeps_empty_twi_ml_even_with_durable_receipt():
    ledger = FakeDurable()
    settings = env()
    settings.update({"CC_HOSTED_SANDBOX_E2E_ENABLED":"1",
        "CC_HOSTED_SANDBOX_DURABLE_INGRESS_ENABLED":"1"})
    with patch.dict(os.environ, settings, clear=True), patch(
        "xbos_customer_channel.runtime.app._sandbox_receipt_ledger", return_value=ledger
    ):
        response = post(form(sid="SMGREETO2"))
    assert response.status_code == 200
    assert response.text == "<Response></Response>"

def test_local_ledger_never_replies_even_when_flag_enabled():
    settings = env()
    settings["CC_TWILIO_SANDBOX_GREETING_REPLY_ENABLED"] = "1"
    with patch.dict(os.environ, settings, clear=True):
        response = post(form(sid="SMGREET03"))
    assert response.status_code == 200
    assert response.text == "<Response></Response>"

def test_invalid_signature_never_replies():
    settings = env()
    settings.update({"CC_HOSTED_SANDBOX_E2E_ENABLED":"1",
        "CC_HOSTED_SANDBOX_DURABLE_INGRESS_ENABLED":"1",
        "CC_TWILIO_SANDBOX_GREETING_REPLY_ENABLED":"1"})
    with patch.dict(os.environ, settings, clear=True), patch(
        "xbos_customer_channel.runtime.app._sandbox_receipt_ledger", return_value=FakeDurable()
    ):
        client=TestClient(app,base_url="https://xafpay-customer-channel-staging.onrender.com")
        response=client.post("/webhooks/twilio/whatsapp",data=form(sid="SMGREET04"),
          headers={"X-Twilio-Signature":"invalid","X-Forwarded-Proto":"https"})
    assert response.status_code == 403
    assert "<Message>" not in response.text

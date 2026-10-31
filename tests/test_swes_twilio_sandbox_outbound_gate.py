"""SWES source-only sandbox status sending: zero real provider traffic."""
from __future__ import annotations
import sys
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from xbos_customer_channel.channel_contract import ChannelOutboundIntent
from xbos_customer_channel.transports.twilio_whatsapp import TwilioWhatsAppConfig,TwilioSendResult
from xbos_customer_channel.transports.swes_twilio_sandbox_outbound_gate import (
    SWESSandboxOutboundPolicy, SWESSandboxStatusSender, SWESSandboxOutboundNotAdmitted
)
NOW=datetime(2026,10,31,14,0,tzinfo=timezone.utc)
FROM="whatsapp:+14155238886"
TO="whatsapp:+237600000001"
CONFIG=TwilioWhatsAppConfig("ACtest123","token-test-only",FROM,"https://xafpay-customer-channel-staging.onrender.com/webhooks/twilio/whatsapp")
POLICY=SWESSandboxOutboundPolicy(True,True,True,TO,FROM,NOW+timedelta(hours=12))

class FakeStatusVerifier:
    def __init__(self,accepted):
        self.accepted=accepted
        self.calls=0
    def verify(self,*,intent):
        self.calls+=1
        return self.accepted

class RecordingTransport:
    def __init__(self):
        self.calls=[]
    def send(self,intent):
        self.calls.append(intent)
        return TwilioSendResult("SMTESTNOACTUALSEND")

def intent(**overrides):
    return replace(ChannelOutboundIntent(
        recipient=TO,channel="whatsapp",
        merchant_ref="merchant:bound",conversation_ref="conversation:bound",
        session_ref="session:bound",
        purpose="sandbox_status_confirmation",
        event_type="gateway_payment_status",
        projection={"body":"TEST status verified by trusted gateway."},
        template_variables={},
        correlation_id="corr:verified"
    ),**overrides)

def call(policy=POLICY,config=CONFIG,evidence_accepted=True,message=None,now=NOW):
    verifier=FakeStatusVerifier(evidence_accepted)
    transport=RecordingTransport()
    sender=SWESSandboxStatusSender(
        config=config,policy=policy,evidence=verifier,transport=transport
    )
    try:
        result=sender.send(intent=message or intent(),now=now)
        return result,verifier,transport
    except SWESSandboxOutboundNotAdmitted:
        assert not transport.calls
        raise

def test_bounded_sender_dispatches_to_fake_transport_only_with_verified_status():
    result,verifier,transport=call()
    assert result.provider_message_ref=="SMTESTNOACTUALSEND"
    assert verifier.calls==1 and len(transport.calls)==1

@pytest.mark.parametrize("changes",[
    {"enabled":False},{"nonfinancial_only":False},{"tester_consented":False},
    {"allowed_tester":"whatsapp:+237600000002"},
    {"approved_sender":"whatsapp:+14150000000"},
])
def test_unapproved_sender_policy_rejected(changes):
    with pytest.raises(SWESSandboxOutboundNotAdmitted):
        call(policy=replace(POLICY,**changes))

@pytest.mark.parametrize("changes",[
    {"recipient":"whatsapp:+237600000002"},
    {"channel":"sms"},
    {"purpose":"generic_marketing"},
    {"event_type":"payment_initiated"},
    {"event_type":"gateway_checkout_create"},
])
def test_outbound_recipient_purpose_and_event_type_fails_closed(changes):
    with pytest.raises(SWESSandboxOutboundNotAdmitted):
        call(message=intent(**changes))

def test_status_without_authoritative_evidence_rejected():
    with pytest.raises(SWESSandboxOutboundNotAdmitted):
        call(evidence_accepted=False)

def test_expired_consent_rejected():
    with pytest.raises(SWESSandboxOutboundNotAdmitted):
        call(now=NOW+timedelta(days=1))

def test_naive_clock_rejected():
    with pytest.raises(SWESSandboxOutboundNotAdmitted):
        call(now=datetime(2026,10,31,14,0))

def test_invalid_policy_values_rejected():
    with pytest.raises(SWESSandboxOutboundNotAdmitted):
        replace(POLICY,allowed_tester="not_whatsapp")

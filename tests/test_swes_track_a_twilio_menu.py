"""Track A signed first MENU → source-owned restricted merchant catalog, no network."""
import hashlib
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from xbos_customer_channel.adapters.fake_catalog import FakeXBOSCatalogClient
from xbos_customer_channel.adapters.real_xbos_catalog import RealXBOSCatalogAdapter
from xbos_customer_channel.session_state import ChannelState, CustomerSessionSnapshot
from xbos_customer_channel.transports.swes_track_a_twilio_menu import (
    TEST_MERCHANT,
    TEST_TENANT,
    TrackAMenuNotAdmitted,
    prepare_track_a_menu,
)
from xbos_customer_channel.transports.twilio_signed_ingress import (
    SandboxIngressConfig,
    VerifiedSandboxIngress,
)
from xbos_customer_channel.transports.twilio_whatsapp import (
    TwilioInboundMessage,
    TwilioWhatsAppConfig,
)

NOW = datetime(2026, 11, 1, 14, 45, tzinfo=UTC)
LOCATION = "location:synthetic-910001"
ACCOUNT = "AC" + "a" * 32
TESTER = "whatsapp:+237600000001"
SENDER = "whatsapp:+14155238886"
SID = "SM" + "a" * 32
SETTINGS = SandboxIngressConfig(
    TwilioWhatsAppConfig(ACCOUNT, "test-secret-only", SENDER,
                         "https://cc.example.test/webhooks/twilio/whatsapp"),
    TESTER, NOW + timedelta(hours=1), "cc.example.test",
)
MESSAGE = TwilioInboundMessage(SID, TESTER, SENDER, " MENU ",
                               NOW - timedelta(seconds=1), ACCOUNT, None)
KEY = hashlib.sha256((SID + "\x00" + SENDER + "\x00" + ACCOUNT).encode()).hexdigest()
SIGNED = VerifiedSandboxIngress("accepted", KEY[:20], "receipt_only",
                                 False, KEY)

class SessionPort:
    def __init__(self, session=None):
        self.session = default_session() if session is None else session
        self.calls = 0
    def load_for_provider_event(self, *, provider_event_digest, sender, recipient, now):
        self.calls += 1
        assert provider_event_digest == KEY[:20]
        assert sender == TESTER.removeprefix("whatsapp:")
        assert recipient == SENDER.removeprefix("whatsapp:")
        return self.session

class ReceiptPort:
    def __init__(self, admitted=True):
        self.admitted = admitted
        self.calls = []
    def is_first_processed_signed_event(self, **kwargs):
        self.calls.append(kwargs)
        return self.admitted

class Reader(RealXBOSCatalogAdapter):
    def __init__(self):
        self.calls = []
        self.fail = False
    def get_catalog_bound(self, **kw):
        self.calls.append(kw)
        if self.fail: raise RuntimeError("private_reader_denied")
        original = FakeXBOSCatalogClient().get_catalog(
            merchant_ref="merchant:fixture:alpha", location_ref="location:fixture:one"
        )
        return replace(original, merchant_ref=TEST_MERCHANT, location_ref=LOCATION)

def default_session():
    return CustomerSessionSnapshot(
        "server-session", "server-conversation", "server-correlation",
        ChannelState.BROWSING, owner_identity_ref="approved:tester",
        tenant_ref=TEST_TENANT, merchant_ref=TEST_MERCHANT,
        location_ref=LOCATION, context_binding_ref="server-ia0-binding",
        expires_at_epoch=int((NOW + timedelta(minutes=10)).timestamp()),
        security_binding_complete=True
    )

def run(*, signed=SIGNED, message=MESSAGE, settings=SETTINGS,
        session=None, enabled=True, admitted_reader=True,
        location=LOCATION, receipts=None, reader=None, now=NOW):
    ports = SessionPort(session)
    receipts = receipts or ReceiptPort()
    reader = reader or Reader()
    result = prepare_track_a_menu(
        enabled=enabled, admitted_reader=admitted_reader,
        signed=signed, message=message, settings=settings, sessions=ports,
        receipts=receipts, catalog=reader, trusted_location_ref=location, now=now,
    )
    return result, ports, receipts, reader

def test_signed_first_real_bound_catalog_renders_safe_menu():
    result, sessions, receipts, reader = run()
    assert result.category_count > 0
    assert "Wine &amp; Dine" in result.twiml
    assert result.twiml.startswith("<Response><Message>")
    assert sessions.calls == 1 and len(receipts.calls) == 1
    assert len(reader.calls) == 1
    assert reader.calls[0]["merchant_ref"] == TEST_MERCHANT
    assert reader.calls[0]["context_binding_ref"] == "server-ia0-binding"

@pytest.mark.parametrize("overrides", [
    {"enabled":False}, {"admitted_reader":False},
    {"signed":replace(SIGNED,disposition="duplicate")},
    {"signed":replace(SIGNED,disposition="collision")},
    {"signed":replace(SIGNED,merchant_dispatch_enabled=True)},
    {"signed":replace(SIGNED,action="test_order")},
    {"signed":replace(SIGNED,receipt_key="0"*64)},
    {"signed":replace(SIGNED,event_digest="1"*20)},
    {"message":replace(MESSAGE,body="PAY")},
    {"message":replace(MESSAGE,sender="whatsapp:+237600000099")},
    {"message":replace(MESSAGE,recipient="whatsapp:+14150000000")},
    {"message":replace(MESSAGE,account_sid="AC"+"b"*32)},
    {"message":replace(MESSAGE,provider_message_ref="SM"+"b"*32)},
    {"message":replace(MESSAGE,occurred_at=NOW+timedelta(seconds=10))},
    {"message":replace(MESSAGE,occurred_at=NOW-timedelta(minutes=6))},
    {"now":NOW+timedelta(hours=2)},
])
def test_fail_closed_before_reader_on_invalid_provider_or_disabled(overrides):
    with pytest.raises(TrackAMenuNotAdmitted):
        run(**overrides)

@pytest.mark.parametrize("overrides", [
    {"tenant_ref":"2"}, {"tenant_ref":"other"},
    {"merchant_ref":"other"}, {"location_ref":"wrong-location"},
    {"context_binding_ref":None}, {"owner_identity_ref":None},
    {"security_binding_complete":False},
    {"expires_at_epoch":int(NOW.timestamp())},
    {"invalidated_at_epoch":int(NOW.timestamp())},
    {"rotated_to_session_ref":"successor"},
    {"state":ChannelState.PAID},
])
def test_invalid_server_session_cannot_read_catalog(overrides):
    reader = Reader()
    with pytest.raises(TrackAMenuNotAdmitted):
        run(session=replace(default_session(),**overrides),reader=reader)
    assert reader.calls == []

def test_denied_durable_receipt_fails_closed():
    reader=Reader()
    with pytest.raises(TrackAMenuNotAdmitted,match="authenticated_menu_not_available"):
        run(receipts=ReceiptPort(False),reader=reader)
    assert reader.calls==[]

def test_authenticated_catalog_private_read_failure_no_fixture_fallback():
    reader=Reader()
    reader.fail=True
    with pytest.raises(TrackAMenuNotAdmitted,match="authenticated_menu_not_available"):
        run(reader=reader)
    assert len(reader.calls)==1

def test_empty_test_location_denied():
    with pytest.raises(TrackAMenuNotAdmitted):
        run(location="")

"""SWES staged menu seam: no hosted enable, fake catalog only as test fixture."""
from __future__ import annotations
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from xbos_customer_channel.adapters.fake_catalog import FakeXBOSCatalogClient
from xbos_customer_channel.adapters.real_xbos_catalog import RealXBOSCatalogAdapter
from xbos_customer_channel.application.swes_wnd_verified_menu import (
    SWESWNDMenuReadGate, SWESWNDMenuNotAdmitted, read_verified_wnd_menu
)
from xbos_customer_channel.application.w1_conversation import W1RenderKind
from xbos_customer_channel.channel_contract import ChannelInboundEvent
from xbos_customer_channel.session_state import CustomerSessionSnapshot, ChannelState

NOW=datetime(2026,10,31,13,45,tzinfo=timezone.utc)
MERCHANT="merchant:fixture:alpha"
LOCATION="location:fixture:one"
GATE=SWESWNDMenuReadGate(True,True,True,"910001",MERCHANT,LOCATION)

class TestOnlyBoundCatalogReader(RealXBOSCatalogAdapter):
    __test__=False
    def __init__(self):
        self.calls=[]
        self.broken=False
        self.cross_tenant=False
    def get_catalog_bound(self,**kwargs):
        self.calls.append(kwargs)
        if self.broken:
            raise RuntimeError("private_auth_failed_bearer_unavailable")
        projection=FakeXBOSCatalogClient().get_catalog(
            merchant_ref=MERCHANT,location_ref=LOCATION
        )
        return replace(projection,merchant_ref="merchant:other") if self.cross_tenant else projection

def secure_session():
    return CustomerSessionSnapshot(
        "session:test","conversation:test","correlation:test",ChannelState.BROWSING,
        owner_identity_ref="identity:trusted",tenant_ref="910001",
        merchant_ref=MERCHANT,location_ref=LOCATION,
        context_binding_ref="binding:trusted",
        expires_at_epoch=int((NOW+timedelta(minutes=15)).timestamp()),
        security_binding_complete=True
    )

def signed_verified_event():
    return ChannelInboundEvent(
        "event:verified","SMTESTTOKEN01","whatsapp",
        "whatsapp:+237600000001","whatsapp:+14155238886",
        "conversation:test","session:test",MERCHANT,"identity:trusted",
        NOW,"text",{"text":" MENU "},"correlation:test",{"provider":"twilio_whatsapp"}
    )

def call(reader=None,gate=GATE,session=None,event=None):
    reader=reader or TestOnlyBoundCatalogReader()
    return read_verified_wnd_menu(
        gate=gate,session=session or secure_session(),
        inbound=event or signed_verified_event(),catalog=reader,now=NOW
    )

def test_authorized_real_reader_bound_menu_categories():
    reader=TestOnlyBoundCatalogReader()
    result=call(reader)
    assert result.kind is W1RenderKind.LIST
    assert result.body=="Welcome. Choose a menu category."
    assert len(result.list_sections)==1
    assert len(result.list_sections[0].rows)>0
    assert len(reader.calls)==1
    assert reader.calls[0]=={
        "context_binding_ref":"binding:trusted",
        "merchant_ref":MERCHANT,"location_ref":LOCATION,
        "effective_at":NOW,"correlation_ref":"correlation:test"
    }

@pytest.mark.parametrize("overrides",[
    {"enabled":False},{"merchant_admitted":False},{"service_reader_admitted":False}
])
def test_disabled_or_unadmitted_never_reads(overrides):
    reader=TestOnlyBoundCatalogReader()
    gate=replace(GATE,**overrides)
    with pytest.raises(SWESWNDMenuNotAdmitted):
        call(reader,gate=gate)
    assert reader.calls==[]

@pytest.mark.parametrize("overrides",[
    {"security_binding_complete":False},
    {"tenant_ref":"other-tenant"},
    {"merchant_ref":"other-merchant"},
    {"location_ref":"other-location"},
    {"context_binding_ref":None},
    {"context_binding_ref":"legacy_unattested"},
    {"owner_identity_ref":None},
    {"expires_at_epoch":int((NOW-timedelta(seconds=1)).timestamp())},
    {"expires_at_epoch":None},
    {"invalidated_at_epoch":int(NOW.timestamp())},
    {"rotated_to_session_ref":"session:rotated"},
    {"state":ChannelState.START},
])
def test_invalid_trusted_session_fails_before_catalog_read(overrides):
    reader=TestOnlyBoundCatalogReader()
    with pytest.raises(SWESWNDMenuNotAdmitted):
        call(reader,session=replace(secure_session(),**overrides))
    assert reader.calls==[]

@pytest.mark.parametrize("overrides",[
    {"channel":"web"},{"message_type":"interactive"},
    {"payload":{"text":"ORDER WND TEST"}},
    {"payload":{"text":42}},
    {"payload":{}},
    {"conversation_ref":"other-conversation"},
    {"session_ref":"other-session"},{"merchant_ref":"other-merchant"},
    {"subject_ref":"other-identity"},
    {"correlation_ref":"other-correlation"},
    {"occurred_at":NOW+timedelta(minutes=1)},
])
def test_unbound_inbound_cannot_read_catalog(overrides):
    reader=TestOnlyBoundCatalogReader()
    with pytest.raises(SWESWNDMenuNotAdmitted):
        call(reader,event=replace(signed_verified_event(),**overrides))
    assert reader.calls==[]

def test_no_fake_catalog_fallback():
    reader=TestOnlyBoundCatalogReader()
    reader.broken=True
    with pytest.raises(SWESWNDMenuNotAdmitted,match="real_wnd_catalog_unavailable"):
        call(reader)
    assert len(reader.calls)==1

def test_cross_merchant_catalog_blocked():
    reader=TestOnlyBoundCatalogReader()
    reader.cross_tenant=True
    with pytest.raises(SWESWNDMenuNotAdmitted,match="scope_or_content"):
        call(reader)

def test_non_real_reader_rejected():
    with pytest.raises(SWESWNDMenuNotAdmitted,match="real_xbos_catalog_reader_required"):
        call(reader=FakeXBOSCatalogClient())

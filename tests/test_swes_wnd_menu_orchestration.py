"""Source-only guarded WND MENU composition; never touches actual Twilio/WND."""
import sys
from dataclasses import replace
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.application.swes_wnd_menu_orchestration import (
    _safe_menu_twiml, prepare_trusted_wnd_menu_reply,
)
from xbos_customer_channel.application.swes_wnd_verified_menu import SWESWNDMenuNotAdmitted
from xbos_customer_channel.application.w1_conversation import (
    W1ListRow, W1ListSection, W1RenderedMessage, W1RenderKind,
)
from test_swes_wnd_verified_menu import (
    GATE, NOW, TestOnlyBoundCatalogReader, secure_session, signed_verified_event,
)

class Receipt:
    def __init__(self, value=True, fail=False):
        self.value=value
        self.fail=fail
        self.calls=[]
    def is_first_processed_signed_event(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail: raise RuntimeError("secret_unavailable")
        return self.value

class Session:
    def __init__(self, value=None, fail=False):
        self.value=secure_session() if value is None else value
        self.fail=fail
        self.calls=[]
    def load_for_verified_event(self,**kwargs):
        self.calls.append(kwargs)
        if self.fail: raise RuntimeError("backend_failure_secret")
        return self.value

def assemble(*, enabled=True, receipt=None, session=None, catalog=None, gate=GATE,
             inbound=None):
    receipt=Receipt() if receipt is None else receipt
    session=Session() if session is None else session
    catalog=TestOnlyBoundCatalogReader() if catalog is None else catalog
    result=prepare_trusted_wnd_menu_reply(
        enabled=enabled, gate=gate, inbound=inbound or signed_verified_event(),
        receipts=receipt, sessions=session, catalog=catalog, now=NOW,
    )
    return result,receipt,session,catalog

def test_real_catalog_with_trusted_receipt_and_session_renders_escaped_twiml():
    result, receipt,session, catalog=assemble()
    assert result.category_count>0
    assert result.twiml.startswith("<Response><Message>")
    assert result.twiml.endswith("</Message></Response>")
    assert "Wine &amp; Dine" in result.twiml
    assert len(receipt.calls)==1
    assert len(session.calls)==1
    assert len(catalog.calls)==1
    assert "fake" not in result.twiml.casefold()

def test_disabled_orchestration_does_not_read_any_port():
    receipt,session,catalog=Receipt(),Session(),TestOnlyBoundCatalogReader()
    with pytest.raises(SWESWNDMenuNotAdmitted, match="disabled"):
        assemble(enabled=False,receipt=receipt,session=session,catalog=catalog)
    assert not receipt.calls and not session.calls and not catalog.calls

@pytest.mark.parametrize("value",[False,None,0,"true"])
def test_missing_or_fake_receipt_claim_rejected_before_session(value):
    receipt,session,catalog=Receipt(value),Session(),TestOnlyBoundCatalogReader()
    with pytest.raises(SWESWNDMenuNotAdmitted,match="first_receipt"):
        assemble(receipt=receipt,session=session,catalog=catalog)
    assert len(receipt.calls)==1
    assert not session.calls and not catalog.calls

def test_receipt_loader_exception_fails_without_private_details():
    with pytest.raises(SWESWNDMenuNotAdmitted,match="durable_signed_receipt_unavailable"):
        assemble(receipt=Receipt(fail=True))

def test_session_loader_exception_fails_without_private_details():
    with pytest.raises(SWESWNDMenuNotAdmitted,match="trusted_session_loader_unavailable"):
        assemble(session=Session(fail=True))

@pytest.mark.parametrize("value",[False,"raw-whatsapp-payload",{"merchant":"910001"}])
def test_untrusted_session_not_used(value):
    catalog=TestOnlyBoundCatalogReader()
    with pytest.raises(SWESWNDMenuNotAdmitted,match="trusted_session_required"):
        assemble(session=Session(value=value),catalog=catalog)
    assert not catalog.calls

def test_session_cross_tenant_denied_before_catalog():
    catalog=TestOnlyBoundCatalogReader()
    with pytest.raises(SWESWNDMenuNotAdmitted,match="trusted_wnd_session"):
        assemble(session=Session(replace(secure_session(),tenant_ref="other")),catalog=catalog)
    assert not catalog.calls

def test_bad_catalog_does_not_fall_back_to_fixture():
    catalog=TestOnlyBoundCatalogReader()
    catalog.broken=True
    with pytest.raises(SWESWNDMenuNotAdmitted,match="real_wnd_catalog_unavailable"):
        assemble(catalog=catalog)

@pytest.mark.parametrize("titles",[
    [],["X"]*16,["FOOD","food"],[""],["X"*91],["A\x00B"],
])
def test_unsafe_catalog_sections_rejected(titles):
    message=W1RenderedMessage(
        W1RenderKind.LIST, "Category",
        list_sections=(W1ListSection("Categories",tuple(
            W1ListRow(str(index),title) for index,title in enumerate(titles)
        )),),
    )
    with pytest.raises(SWESWNDMenuNotAdmitted):
        _safe_menu_twiml(message)

def test_real_catalog_strings_are_xml_escaped():
    message=W1RenderedMessage(
        W1RenderKind.LIST, "Category",
        list_sections=(W1ListSection("Categories",(
            W1ListRow("one","Food & Drink"),
            W1ListRow("two","Desserts <Special>"),
        )),),
    )
    result=_safe_menu_twiml(message)
    assert result.category_count==2
    assert "Food &amp; Drink" in result.twiml
    assert "Desserts &lt;Special&gt;" in result.twiml
    assert "<Special>" not in result.twiml

def test_unapproved_merchant_gate_fails_even_with_receipt_and_session():
    with pytest.raises(SWESWNDMenuNotAdmitted,match="menu_read_not_admitted"):
        assemble(gate=replace(GATE,merchant_admitted=False))

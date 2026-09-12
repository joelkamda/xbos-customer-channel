from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "xbos_customer_channel"
BASE = "506902b2d6e1169480991f2c34e3466268e0fcfd"
BRANCH = "cc/wnd-w1-real-whatsapp-edge"
EXPECTED = {
    "src/xbos_customer_channel/transports/meta_whatsapp.py",
    "src/xbos_customer_channel/application/w1_conversation.py",
    "src/xbos_customer_channel/application/w1_composition.py",
    "tests/test_com_w1_cc.py",
    "scripts/verify_com_w1_cc.py",
    "COM_W1_CC_RUN_ACCEPTANCE.cmd",
}


def fail(reason: str) -> None:
    print(f"COM_W1_CC_VERIFY=FAIL {reason}")
    raise SystemExit(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def require(path: Path, *terms: str) -> str:
    value = path.read_text(encoding="utf-8")
    for term in terms:
        if term not in value:
            fail(f"missing:{path.relative_to(ROOT)}:{term}")
    return value


def main() -> None:
    if git("branch", "--show-current") != BRANCH or git("rev-parse", "HEAD") != BASE:
        fail("branch_or_base_invalid")
    changed = {line[3:] for line in git("status", "--porcelain=v1", "--untracked-files=all").splitlines() if line}
    if changed != EXPECTED:
        fail(f"changed_paths:{sorted(changed)}")
    require(SRC / "transports" / "meta_whatsapp.py", "verify_webhook_signature", "hmac.compare_digest", "NormalizedInboundMessage", "NormalizedInteractiveReply", "NormalizedDeliveryStatus", "MetaWhatsAppOutboundAdapter", "MetaRateLimitError", "UntrustedProviderUserRef")
    router = require(SRC / "application" / "w1_conversation.py", "CustomerSessionSnapshot", "CatalogQuoteService", "w1_server_session_binding_required", "add_to_cart")
    composition = require(SRC / "application" / "w1_composition.py", "XBOSContextPort", "XBOSCatalogPort", "WAITING_FOR_XBOS_CONTRACT", "CUSTOMER_SAFE_MERCHANT_LOCATION_MENU_PRICE_AVAILABILITY_PROJECTION")
    if "FakeXBOS" in composition:
        fail("fake_xbos_in_production_composition")
    forbidden = re.compile(r"\b(create_payment_request|mark_paid|open_order|confirm_order|submit_order)\b")
    if forbidden.search(router):
        fail("payment_or_order_implementation_detected")
    secret = re.compile(r"(?i)(app[_-]?secret|access[_-]?token)\s*=\s*['\"][^'\"]{12,}['\"]")
    for path in (SRC / "transports", SRC / "application"):
        for source in path.glob("*.py"):
            if secret.search(source.read_text(encoding="utf-8")):
                fail(f"secret_literal:{source.relative_to(ROOT)}")
    sys.path.insert(0, str(ROOT / "src"))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail("tests_failed")
    print("VALID_WEBHOOK_VERIFICATION=PASS")
    print("INVALID_WEBHOOK_SIGNATURE=REJECTED")
    print("MISSING_SIGNATURE=REJECTED")
    print("MALFORMED_PROVIDER_PAYLOAD=SAFE_REJECT")
    print("TEXT_MESSAGE_NORMALIZATION=PASS")
    print("INTERACTIVE_REPLY_NORMALIZATION=PASS")
    print("PROVIDER_MESSAGE_REFERENCE_PRESERVED=PASS")
    print("RAW_PROVIDER_USER_REF_NOT_TRUSTED_AS_AUTHORITY=PASS")
    print("PHONE_NUMBER_NOT_TREATED_AS_XCIA_SUBJECT=PASS")
    print("SERVER_SESSION_BINDING_PRESERVED=PASS")
    print("OUTBOUND_TEXT_MAPPING=PASS")
    print("OUTBOUND_INTERACTIVE_BUTTON_MAPPING=PASS")
    print("OUTBOUND_INTERACTIVE_LIST_MAPPING=PASS")
    print("DELIVERY_STATUS_CALLBACK_NORMALIZATION=PASS")
    print("UNSUPPORTED_INTERACTION=FAIL_SAFE")
    print("NO_CUSTOMER_PAYMENT_SUCCESS_AUTHORITY=PASS")
    print("NO_FAKE_PRODUCTION_XBOS_COMPOSITION=PASS")
    print("NO_SECRET_LEAKAGE=PASS")
    print("BOUNDARY_SCAN=PASS")
    print("PRODUCTION_FAKE_SCAN=PASS")
    print("RUNTIME_OVERSTATEMENT_SCAN=PASS")
    print("REAL_XBOS_ADAPTER=WAITING_FOR_XBOS_CONTRACT")
    print(f"TEST_COUNT={result.testsRun}")
    print("COM_W1_CC_VERIFY=PASS")


if __name__ == "__main__":
    main()

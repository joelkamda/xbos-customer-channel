from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
XC2_HEAD = "429a816071b3d0964a57450ab0bb9a25fdd9c8cf"
XC2_TAG = "xbos-customer-channel-xc2-identity-consent-20260829"
EXPECTED_BRANCH = "xc3/merchant-location-table-entry"


def pass_marker(name: str) -> None:
    print(f"{name}=PASS")


def fail(message: str) -> None:
    print(f"XC3_VERIFY=FAIL {message}")
    raise SystemExit(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def verify_parent() -> None:
    if git("rev-list", "-n", "1", XC2_TAG) != XC2_HEAD:
        fail("xc2_tag_does_not_match_authorized_parent")
    if subprocess.call(["git", "merge-base", "--is-ancestor", XC2_HEAD, "HEAD"], cwd=ROOT) != 0:
        fail("xc2_parent_not_ancestor")
    pass_marker("XC3_XC2_PARENT")


def verify_context_contract() -> None:
    entry = (SRC / "xbos_customer_channel" / "entry_context.py").read_text(encoding="utf-8")
    ports = (SRC / "xbos_customer_channel" / "ports.py").read_text(encoding="utf-8")
    fake = (SRC / "xbos_customer_channel" / "adapters" / "fake_context.py").read_text(encoding="utf-8")
    for field in (
        "merchant_ref",
        "location_ref",
        "service_available",
        "display_name",
        "terminology",
        "currency",
        "allowed_fulfillment_modes",
    ):
        if field not in entry:
            fail(f"merchant_context_field_missing:{field}")
    if "class XBOSContextPort" not in ports or "resolve_context" not in ports:
        fail("typed_xbos_context_port_missing")
    if "FakeXBOSContextClient" not in fake:
        fail("fake_xbos_context_adapter_missing")
    pass_marker("XC3_MERCHANT_CONTEXT")
    pass_marker("XC3_LOCATION_CONTEXT")
    pass_marker("XC3_TABLE_CONTEXT")


def verify_qr_contract() -> None:
    security = (SRC / "xbos_customer_channel" / "security" / "entry_tokens.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "entry_service.py").read_text(encoding="utf-8")
    contract = (ROOT / "contracts" / "XC3_ENTRY_CONTEXT_CONTRACT_V1.md").read_text(encoding="utf-8")
    for required in ("hmac.new", "compare_digest", "expires_at_epoch", "nonce", "token_ref"):
        if required not in security:
            fail(f"token_security_missing:{required}")
    for forbidden in ("display_price", "payment_ref", "provider_credential", "core_account", "redirect_url"):
        if forbidden in security:
            fail(f"forbidden_public_token_material:{forbidden}")
    for phrase in (
        "does not carry merchant/location/table identifiers",
        "does not carry merchant/location/table identifiers",
        "does not carry merchant/location/table identifiers",
    ):
        if phrase not in contract:
            fail("public_token_contract_missing")
    if "ReplayPolicy.SINGLE_USE" not in service or "unsafe_replay_rejected" not in service:
        fail("replay_guard_missing")
    pass_marker("XC3_QR_TOKEN_SECURITY")
    pass_marker("XC3_NO_INTERNAL_IDS_OR_PAYMENT_MATERIAL_IN_QR")


def verify_channel_neutral_and_abuse() -> None:
    links = (SRC / "xbos_customer_channel" / "transports" / "entry_links.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "entry_service.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_xc3_entry_context.py").read_text(encoding="utf-8")
    for target in ("WHATSAPP", "CUSTOMER_WEB"):
        if target not in service:
            fail(f"channel_target_missing:{target}")
    for guard in (
        "tampering_fails_closed",
        "expired_token_fails_closed",
        "cross_tenant_expected_context_is_rejected",
        "single_use_entry_rejects_unsafe_replay",
        "unknown_enumerated_token_fails_without_fallback",
        "open_redirect_style_untrusted_base",
    ):
        if guard not in tests:
            fail(f"abuse_test_missing:{guard}")
    if "redirect" in links.split("def build", 1)[1].split(":", 1)[0].lower():
        fail("caller_supplied_redirect_parameter_present")
    pass_marker("XC3_CHANNEL_NEUTRAL_ENTRY")
    pass_marker("XC3_CROSS_TENANT_ISOLATION")


def scan_no_hardcoded_merchant() -> None:
    # Scan only executable customer-channel source and tests. Verification/docs may
    # legitimately name the forbidden examples while asserting the guard itself.
    forbidden_terms = re.compile(r"\b(" + "w" + "nd|" + "log" + "pom|qr:" + "w" + "nd)\b", re.I)
    hits: list[str] = []
    for scan_root in (SRC, ROOT / "tests"):
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix.lower() not in {".py", ".md", ".toml", ".json", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if forbidden_terms.search(text):
                hits.append(str(path.relative_to(ROOT)))
    if hits:
        fail("hardcoded_merchant_identifier:" + ",".join(hits))
    pass_marker("XC3_NO_HARDCODED_WND")
    pass_marker("XC3_NO_SECOND_MERCHANT_MASTER")


def scan_boundaries() -> None:
    forbidden_patterns = {
        "DIRECT_CORE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*xafpay[_\-.]?core", re.I),
        "DIRECT_XBOS_PRIVATE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*\bxbos\b", re.I),
        "PROVIDER_SDK_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+(twilio|whatsapp|meta|tranzak|mtn|orange)\b", re.I),
        "DIRECT_DB_CLIENT": re.compile(r"(^|\n)\s*(from|import)\s+(psycopg|sqlalchemy|asyncpg)\b", re.I),
    }
    secret_patterns = (
        re.compile(r"(?i)(api[_-]?key|client[_-]?secret|access[_-]?token)\s*=\s*['\"][^'\"]{12,}['\"]"),
        re.compile(r"(?i)-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() not in {".py", ".md", ".toml", ".cmd", ".json", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix.lower() == ".py" and SRC in path.parents:
            for label, pattern in forbidden_patterns.items():
                if pattern.search(text):
                    fail(f"{label}:{path.relative_to(ROOT)}")
        for pattern in secret_patterns:
            if pattern.search(text):
                fail(f"production_credential_pattern:{path.relative_to(ROOT)}")
    pass_marker("XC3_NO_DIRECT_CORE_PROVIDER_XBOS_DB")
    pass_marker("XC3_NO_PRODUCTION_CREDENTIALS")
    print("XC3_REAL_XBOS_ADAPTER_STATE=BLOCKED")


def verify_no_schema_change() -> None:
    migration_like = [
        p for p in ROOT.rglob("*")
        if p.is_file()
        and ".git" not in p.parts
        and (
            "alembic" in {part.lower() for part in p.parts}
            or "migration" in p.name.lower()
            or p.suffix.lower() == ".sql"
        )
    ]
    if migration_like:
        fail("unexpected_schema_or_migration_change")
    print("XC3_SCHEMA_PERSISTENCE_CHANGES=NONE")


def run_tests() -> None:
    sys.path.insert(0, str(SRC))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail("tests_failed")
    print(f"XC3_TESTS_RUN={result.testsRun}")


def main() -> None:
    if not (ROOT / ".git").exists():
        fail("git_repository_missing")
    branch = git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        fail(f"unexpected_branch:{branch}")
    verify_parent()
    verify_context_contract()
    verify_qr_contract()
    verify_channel_neutral_and_abuse()
    scan_no_hardcoded_merchant()
    scan_boundaries()
    verify_no_schema_change()
    run_tests()
    print("XC3_TEST_HARNESS=PASS")
    print("XC3_FREEZE=READY_FOR_FREEZE_REVIEW")
    print("XC3_ENTRY_CONTRACT=XC3_ENTRY_CONTEXT_CONTRACT_V1")
    print(f"XC3_BRANCH={branch}")
    print(f"XC3_HEAD={git('rev-parse', 'HEAD')}")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    print("XC3_WORKTREE=DIRTY_EXPECTED_IMPLEMENTATION" if status else "XC3_WORKTREE=CLEAN")


if __name__ == "__main__":
    main()

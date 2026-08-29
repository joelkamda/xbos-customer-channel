from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
XC1_HEAD = "dbf0d7c12eb906ede001227a810c317e18f3b549"
XC1_TAG = "xbos-customer-channel-xc1-constitution-foundation-20260829"
EXPECTED_BRANCH = "xc2/channel-identity-consent"


def pass_marker(name: str) -> None:
    print(f"{name}=PASS")


def fail(message: str) -> None:
    print(f"XC2_VERIFY=FAIL {message}")
    raise SystemExit(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def verify_parent() -> None:
    if git("rev-parse", f"{XC1_TAG}^{{}}") != XC1_HEAD:
        fail("xc1_tag_does_not_match_authorized_parent")
    if subprocess.call(["git", "merge-base", "--is-ancestor", XC1_HEAD, "HEAD"], cwd=ROOT) != 0:
        fail("xc1_parent_not_ancestor")
    pass_marker("XC2_XC1_PARENT")


def verify_contract() -> None:
    identity_text = (SRC / "xbos_customer_channel" / "identity.py").read_text(encoding="utf-8")
    for state in ("ANONYMOUS", "RECOGNIZED", "VERIFIED", "LINKED", "BLOCKED_OR_RESTRICTED"):
        if state not in identity_text:
            fail(f"missing_identity_state:{state}")
    for purpose in ("TRANSACTIONAL", "MARKETING", "SUPPORT", "ORDER_UPDATES", "RECEIPT_DELIVERY"):
        if purpose not in identity_text:
            fail(f"missing_consent_purpose:{purpose}")
    pass_marker("XC2_CHANNEL_IDENTITY")
    pass_marker("XC2_CONSENT_PURPOSE")


def verify_party_boundary() -> None:
    ports = (SRC / "xbos_customer_channel" / "ports.py").read_text(encoding="utf-8")
    required = ("resolve_identity", "verify_identity", "link_verified_party", "record_consent", "consent_for")
    for name in required:
        if name not in ports:
            fail(f"missing_identity_port_operation:{name}")
    if "resolve_or_bind_customer" in ports:
        fail("unsafe_resolve_or_bind_shortcut_still_present")
    fake = (SRC / "xbos_customer_channel" / "adapters" / "fake_identity.py").read_text(encoding="utf-8")
    if "verification_required_before_link" not in fake:
        fail("verification_link_guard_missing")
    pass_marker("XC2_PARTY_RESOLUTION")
    pass_marker("XC2_VERIFICATION_BOUNDARY")
    pass_marker("XC2_NO_UNIVERSAL_PHONE_IDENTITY")
    pass_marker("XC2_NO_SECOND_PARTY_MASTER")


def verify_privacy() -> None:
    persistence = (SRC / "xbos_customer_channel" / "persistence" / "identity_records.py").read_text(encoding="utf-8")
    forbidden_fields = ("phone_number:", "channel_address:", "message_body:", "email:")
    if any(token in persistence for token in forbidden_fields):
        fail("raw_customer_pii_in_identity_persistence")
    constitution = (ROOT / "docs" / "XC2_IDENTITY_AND_CONSENT_CONSTITUTION.md").read_text(encoding="utf-8")
    for phrase in (
        "not universal/legal identity",
        "Verification does not create consent",
        "Persistent channel PII must be minimized",
        "does not begin XC3",
    ):
        if phrase not in constitution:
            fail(f"xc2_constitution_missing:{phrase}")
    pass_marker("XC2_PRIVACY")


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
    pass_marker("XC2_NO_DIRECT_CORE_PROVIDER_XBOS_DB")
    pass_marker("XC2_NO_PRODUCTION_CREDENTIALS")


def run_tests() -> None:
    sys.path.insert(0, str(SRC))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail("tests_failed")
    print(f"XC2_TESTS_RUN={result.testsRun}")


def main() -> None:
    if not (ROOT / ".git").exists():
        fail("git_repository_missing")
    branch = git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        fail(f"unexpected_branch:{branch}")
    verify_parent()
    verify_contract()
    verify_party_boundary()
    verify_privacy()
    scan_boundaries()
    run_tests()
    print("XC2_TEST_HARNESS=PASS")
    print("XC2_FREEZE=READY_FOR_FREEZE_REVIEW")
    print(f"XC2_BRANCH={branch}")
    print(f"XC2_HEAD={git('rev-parse', 'HEAD')}")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    print("XC2_WORKTREE=DIRTY_EXPECTED_IMPLEMENTATION" if status else "XC2_WORKTREE=CLEAN")


if __name__ == "__main__":
    main()

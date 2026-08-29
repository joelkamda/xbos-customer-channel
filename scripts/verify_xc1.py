from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def pass_marker(name: str) -> None:
    print(f"{name}=PASS")


def fail(message: str) -> None:
    print(f"XC1_VERIFY=FAIL {message}")
    raise SystemExit(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def scan_boundaries() -> None:
    forbidden_patterns = {
        "DIRECT_CORE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*xafpay[_\-.]?core", re.I),
        "DIRECT_XBOS_PRIVATE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*\bxbos\b", re.I),
        "PROVIDER_SDK_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+(twilio|whatsapp|meta|tranzak|mtn|orange)\b", re.I),
        "DIRECT_DB_CLIENT": re.compile(r"(^|\n)\s*(from|import)\s+(psycopg|sqlalchemy|asyncpg)\b", re.I),
    }
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for label, pattern in forbidden_patterns.items():
            if pattern.search(text):
                fail(f"{label}:{path.relative_to(ROOT)}")


def verify_docs() -> None:
    constitution = (ROOT / "docs" / "XC1_CHANNEL_CONSTITUTION.md").read_text(encoding="utf-8")
    cx = (ROOT / "docs" / "XC1_CUSTOMER_EXPERIENCE_CONSTITUTION.md").read_text(encoding="utf-8")
    required = ["NO direct Core calls", "NO direct XBOS database access", "REQUIRED_NOT_YET_FROZEN"]
    if not all(item in constitution for item in required):
        fail("constitution_missing_required_rule")
    if "SEE -> CHOOSE -> CONFIRM -> PAY -> KNOW -> RECEIVE" not in cx:
        fail("cx_constitution_missing_core_journey")


def verify_ports_and_fakes() -> None:
    ports = (SRC / "xbos_customer_channel" / "ports.py").read_text(encoding="utf-8")
    for name in ("XBOSCommercePort", "PaymentPort", "TransportPort", "CustomerIdentityPort"):
        if f"class {name}" not in ports:
            fail(f"missing_port:{name}")
    for file_name, class_name in (
        ("fake_xbos.py", "FakeXBOSCommerceClient"),
        ("fake_payment.py", "FakePaymentService"),
        ("fake_transport.py", "FakeWhatsAppTransport"),
    ):
        text = (SRC / "xbos_customer_channel" / "adapters" / file_name).read_text(encoding="utf-8")
        if f"class {class_name}" not in text:
            fail(f"missing_fake:{class_name}")


def run_tests() -> None:
    sys.path.insert(0, str(SRC))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail("tests_failed")
    print(f"XC1_TESTS_RUN={result.testsRun}")


def main() -> None:
    if not (ROOT / ".git").exists():
        fail("git_repository_missing")
    branch = git("branch", "--show-current")
    if branch != "xc1/channel-constitution-foundation":
        fail(f"unexpected_branch:{branch}")

    verify_docs()
    pass_marker("XC1_REPOSITORY")
    pass_marker("XC1_CONSTITUTION")

    verify_ports_and_fakes()
    scan_boundaries()
    pass_marker("XC1_XBOS_CLIENT_BOUNDARY")
    pass_marker("XC1_PAYMENT_SERVICE_BOUNDARY")
    pass_marker("XC1_FAKE_ADAPTERS")
    pass_marker("XC1_CHANNEL_DB_BOUNDARY")

    run_tests()
    pass_marker("XC1_TEST_HARNESS")
    pass_marker("XC1_NO_DIRECT_CORE_PROVIDER_XBOS_DB")
    print("XC1_FREEZE=READY_FOR_FREEZE_REVIEW")
    print(f"XC1_BRANCH={branch}")
    print(f"XC1_HEAD={git('rev-parse', '--verify', 'HEAD') if git('rev-list', '--all', '--count') != '0' else 'UNBORN'}")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    print("XC1_WORKTREE=DIRTY_EXPECTED_BOOTSTRAP" if status else "XC1_WORKTREE=CLEAN")


if __name__ == "__main__":
    main()

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
XC6_HEAD = "b59659af1838fd1ce59c9d73347dab3f0ec4b4a1"
XC6_TAG = "xbos-customer-channel-xc6-order-draft-service-mode-confirmation-20260829"
EXPECTED_BRANCH = "xc7/order-change-cancellation-fulfillment"


def pass_marker(name: str) -> None:
    print(f"{name}=PASS")


def fail(message: str) -> None:
    print(f"XC7_VERIFY=FAIL {message}")
    raise SystemExit(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def verify_parent_and_contracts() -> None:
    if git("rev-list", "-n", "1", XC6_TAG) != XC6_HEAD:
        fail("xc6_tag_does_not_match_authorized_parent")
    if subprocess.call(["git", "merge-base", "--is-ancestor", XC6_HEAD, "HEAD"], cwd=ROOT) != 0:
        fail("xc6_parent_not_ancestor")
    pass_marker("XC7_XC6_PARENT")

    preserved = (
        ("contracts/XC6_ORDER_DRAFT_CONFIRMATION_CONTRACT_V1.md", "XC7_XC6_ORDER_DRAFT_CONFIRMATION_CONTRACT_PRESERVED"),
        ("contracts/XC5_CONVERSATION_SESSION_STATE_CONTRACT_V1.md", "XC7_XC5_SESSION_CONTRACT_PRESERVED"),
        ("contracts/XC4_CATALOG_QUOTE_CONTRACT_V1.md", "XC7_XC4_CATALOG_QUOTE_CONTRACT_PRESERVED"),
        ("contracts/XC3_ENTRY_CONTEXT_CONTRACT_V1.md", "XC7_XC3_ENTRY_CONTEXT_CONTRACT_PRESERVED"),
    )
    for path, marker in preserved:
        changed = subprocess.check_output(["git", "diff", "--name-only", XC6_TAG, "--", path], cwd=ROOT, text=True).strip()
        if changed:
            fail(f"preserved_contract_changed:{path}")
        pass_marker(marker)


def verify_change_policy() -> None:
    contract = (ROOT / "contracts" / "XC7_ORDER_CHANGE_FULFILLMENT_CONTRACT_V1.md").read_text(encoding="utf-8")
    ports = (SRC / "xbos_customer_channel" / "ports.py").read_text(encoding="utf-8")
    fake = (SRC / "xbos_customer_channel" / "adapters" / "fake_order_lifecycle.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "order_lifecycle_service.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_xc7_order_lifecycle.py").read_text(encoding="utf-8")
    for required in (
        "CHANNEL_ROLE=REQUEST_PRESENT_CONFIRM",
        "XBOS_ORDER_STATE=AUTHORITATIVE",
        "XBOS_CHANGE_POLICY=AUTHORITATIVE",
    ):
        if required not in contract:
            fail(f"change_authority_contract_missing:{required}")
    if "class XBOSOrderChangeFulfillmentPort" not in ports:
        fail("typed_xbos_lifecycle_port_missing")
    for required in ("request_order_change", "OrderChangeRequest", "OrderChangeDecision"):
        if required not in ports and required not in fake and required not in service:
            fail(f"change_boundary_missing:{required}")
    for test_name in (
        "test_pre_fulfillment_change_is_decided_by_xbos_policy",
        "test_change_after_fulfillment_started_is_rejected_by_xbos_policy",
        "test_duplicate_change_same_payload_is_one_canonical_effect",
    ):
        if test_name not in tests:
            fail(f"change_policy_test_missing:{test_name}")
    pass_marker("XC7_ORDER_CHANGE")
    pass_marker("XC7_XBOS_CHANGE_POLICY_AUTHORITY")


def verify_cancellation_policy() -> None:
    model = (SRC / "xbos_customer_channel" / "order_lifecycle.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "order_lifecycle_service.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_xc7_order_lifecycle.py").read_text(encoding="utf-8")
    for required in (
        "CANCEL_BEFORE_PAYMENT",
        "CANCEL_AFTER_PAYMENT",
        "CANCEL_AFTER_PREPARATION_BEGAN",
        "MERCHANT_INITIATED_CANCELLATION",
    ):
        if required not in model:
            fail(f"cancellation_case_missing:{required}")
    for test_name in (
        "test_cancel_before_payment_is_distinct_and_has_no_financial_correction",
        "test_cancel_after_payment_is_distinct_and_only_projects_correction_requirement",
        "test_cancel_after_preparation_is_distinct_and_xbos_policy_rejects",
        "test_merchant_initiated_cancellation_is_authoritative_projection",
    ):
        if test_name not in tests:
            fail(f"cancellation_test_missing:{test_name}")
    forbidden_calls = ("refund(", "reverse(", "reversal(", "perform_financial_correction(", "create_payment_request(")
    lowered = service.lower()
    for forbidden in forbidden_calls:
        if forbidden in lowered:
            fail(f"channel_financial_correction_operation:{forbidden}")
    pass_marker("XC7_CANCELLATION_POLICY")
    pass_marker("XC7_CANCELLATION_CASES_DISTINGUISHED")
    pass_marker("XC7_NO_CHANNEL_FINANCIAL_CORRECTION")


def verify_fulfillment_and_reentry() -> None:
    model = (SRC / "xbos_customer_channel" / "order_lifecycle.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "order_lifecycle_service.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_xc7_order_lifecycle.py").read_text(encoding="utf-8")
    required_stages = ("KITCHEN_BAR_TICKET", "PREPARATION", "READY", "SERVED", "PICKED_UP", "DISPATCHED", "DELIVERED")
    for stage in required_stages:
        if stage not in model:
            fail(f"fulfillment_stage_missing:{stage}")
    for forbidden in ("set_fulfillment", "advance_fulfillment", "fulfill_order", "mark_ready", "mark_delivered"):
        if forbidden in service:
            fail(f"channel_fulfillment_mutator:{forbidden}")
    for required in ("def fulfillment_projection", "def reconcile_reentry", "reconcile_order_lifecycle"):
        if required not in service:
            fail(f"fulfillment_reentry_operation_missing:{required}")
    for test_name in (
        "test_all_required_fulfillment_stages_project_without_channel_mutation",
        "test_reentry_reconciles_latest_authoritative_lifecycle_not_local_state",
        "test_correlation_mismatch_fails_closed",
    ):
        if test_name not in tests:
            fail(f"fulfillment_reentry_test_missing:{test_name}")
    pass_marker("XC7_FULFILLMENT_HANDOFF")
    pass_marker("XC7_NO_CHANNEL_FULFILLMENT_AUTHORITY")
    pass_marker("XC7_FULFILLMENT_PROJECTION_ONLY")
    pass_marker("XC7_REENTRY_RECONCILES_AUTHORITATIVE_STATE")


def verify_boundaries() -> None:
    runtime = (
        SRC / "xbos_customer_channel" / "order_lifecycle.py",
        SRC / "xbos_customer_channel" / "application" / "order_lifecycle_service.py",
        SRC / "xbos_customer_channel" / "adapters" / "fake_order_lifecycle.py",
    )
    forbidden_imports = {
        "DIRECT_CORE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*xafpay[_\-.]?core", re.I),
        "DIRECT_XBOS_PRIVATE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*\bxbos\b", re.I),
        "EXTERNAL_PAYMENT_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*(gateway|wallet|twilio|whatsapp|tranzak|mtn|orange)", re.I),
        "DIRECT_DB_CLIENT": re.compile(r"(^|\n)\s*(from|import)\s+(psycopg|sqlalchemy|asyncpg)\b", re.I),
    }
    for path in runtime:
        text = path.read_text(encoding="utf-8")
        for label, pattern in forbidden_imports.items():
            if pattern.search(text):
                fail(f"{label}:{path.relative_to(ROOT)}")
    pass_marker("XC7_NO_DIRECT_CORE_GATEWAY_WALLET_PROVIDER_XBOS_DB")
    print("XC7_REAL_XBOS_ADAPTER_STATE=BLOCKED")


def verify_no_credentials_or_schema() -> None:
    secret_patterns = (
        re.compile(r"(?i)(api[_-]?key|client[_-]?secret|access[_-]?token)\s*=\s*['\"][^'\"]{12,}['\"]"),
        re.compile(r"(?i)-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    xc7_paths = [
        ROOT / "contracts" / "XC7_ORDER_CHANGE_FULFILLMENT_CONTRACT_V1.md",
        ROOT / "docs" / "XC7_ORDER_LIFECYCLE_CONSTITUTION.md",
        SRC / "xbos_customer_channel" / "order_lifecycle.py",
        SRC / "xbos_customer_channel" / "application" / "order_lifecycle_service.py",
        SRC / "xbos_customer_channel" / "adapters" / "fake_order_lifecycle.py",
        ROOT / "tests" / "test_xc7_order_lifecycle.py",
    ]
    for path in xc7_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in secret_patterns:
            if pattern.search(text):
                fail(f"production_credential_pattern:{path.relative_to(ROOT)}")
    pass_marker("XC7_NO_PRODUCTION_CREDENTIALS")

    migration_like = [
        p for p in ROOT.rglob("*")
        if p.is_file()
        and ".git" not in p.parts
        and ("alembic" in {part.lower() for part in p.parts} or "migration" in p.name.lower() or p.suffix.lower() == ".sql")
    ]
    if migration_like:
        fail("unexpected_schema_or_migration_change")
    print("XC7_SCHEMA_PERSISTENCE_CHANGES=NONE")


def run_tests() -> None:
    sys.path.insert(0, str(SRC))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail("tests_failed")
    print(f"XC7_TESTS_RUN={result.testsRun}")


def main() -> None:
    if not (ROOT / ".git").exists():
        fail("git_repository_missing")
    branch = git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        fail(f"unexpected_branch:{branch}")
    verify_parent_and_contracts()
    verify_change_policy()
    verify_cancellation_policy()
    verify_fulfillment_and_reentry()
    verify_boundaries()
    verify_no_credentials_or_schema()
    run_tests()
    print("XC7_TEST_HARNESS=PASS")
    print("XC7_ORDER_CHANGE_FULFILLMENT_CONTRACT=XC7_ORDER_CHANGE_FULFILLMENT_CONTRACT_V1")
    print("XC7_FREEZE=READY_FOR_FREEZE_REVIEW")
    print(f"XC7_BRANCH={branch}")
    print(f"XC7_HEAD={git('rev-parse', 'HEAD')}")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    print("XC7_WORKTREE=DIRTY_EXPECTED_IMPLEMENTATION" if status else "XC7_WORKTREE=CLEAN")


if __name__ == "__main__":
    main()

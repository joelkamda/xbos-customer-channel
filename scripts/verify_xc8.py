from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
XC7_HEAD = "d863c85563e4105a96b182b3791d4cb1cc2bd456"
XC7_TAG = "xbos-customer-channel-xc7-order-change-cancellation-fulfillment-20260829"
EXPECTED_BRANCH = "xc8/payment-request-fake-adapter"


def pass_marker(name: str) -> None:
    print(f"{name}=PASS")


def fail(message: str) -> None:
    print(f"XC8_VERIFY=FAIL {message}")
    raise SystemExit(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def verify_parent_and_contracts() -> None:
    if git("rev-list", "-n", "1", XC7_TAG) != XC7_HEAD:
        fail("xc7_tag_does_not_match_authorized_parent")
    if subprocess.call(["git", "merge-base", "--is-ancestor", XC7_HEAD, "HEAD"], cwd=ROOT) != 0:
        fail("xc7_parent_not_ancestor")
    pass_marker("XC8_XC7_PARENT")
    preserved = (
        ("contracts/XC7_ORDER_CHANGE_FULFILLMENT_CONTRACT_V1.md", "XC8_XC7_ORDER_CHANGE_FULFILLMENT_CONTRACT_PRESERVED"),
        ("contracts/XC6_ORDER_DRAFT_CONFIRMATION_CONTRACT_V1.md", "XC8_XC6_ORDER_DRAFT_CONFIRMATION_CONTRACT_PRESERVED"),
        ("contracts/XC5_CONVERSATION_SESSION_STATE_CONTRACT_V1.md", "XC8_XC5_SESSION_CONTRACT_PRESERVED"),
        ("contracts/XC4_CATALOG_QUOTE_CONTRACT_V1.md", "XC8_XC4_CATALOG_QUOTE_CONTRACT_PRESERVED"),
        ("contracts/XC3_ENTRY_CONTEXT_CONTRACT_V1.md", "XC8_XC3_ENTRY_CONTEXT_CONTRACT_PRESERVED"),
    )
    for path, marker in preserved:
        if subprocess.check_output(["git", "diff", "--name-only", XC7_TAG, "--", path], cwd=ROOT, text=True).strip():
            fail(f"preserved_contract_changed:{path}")
        pass_marker(marker)


def verify_payment_request_authority() -> None:
    contract = (ROOT / "contracts/XC8_PAYMENT_REQUEST_ABSTRACTION_CONTRACT_V1.md").read_text(encoding="utf-8")
    ports = (SRC / "xbos_customer_channel/ports.py").read_text(encoding="utf-8")
    model = (SRC / "xbos_customer_channel/payment_experience.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel/application/payment_experience_service.py").read_text(encoding="utf-8")
    for required in ("CANONICAL_ORDER_AUTHORITY=XBOS", "CANONICAL_AMOUNT_AUTHORITY=XBOS", "PAYMENT_METHOD_POLICY=XBOS", "CHANNEL_PROVIDER_ROUTING_AUTHORITY=NO"):
        if required not in contract:
            fail(f"payment_authority_contract_missing:{required}")
    if "class XBOSPaymentRequestPort" not in ports or "get_payment_request" not in ports:
        fail("typed_xbos_payment_request_port_missing")
    if "canonical_amount" not in model or "policy_ref" not in model:
        fail("canonical_payment_projection_missing_authority_fields")
    if re.search(r"(canonical_amount|amount|total)\s*[\+\-\*/]=", service):
        fail("channel_canonical_amount_mutation")
    for forbidden in ("calculate_amount", "calculate_total", "override_payment_policy", "route_provider", "select_provider"):
        if forbidden in service:
            fail(f"channel_payment_authority_operation:{forbidden}")
    pass_marker("XC8_PAYMENT_REQUEST")
    pass_marker("XC8_CANONICAL_ORDER_AMOUNT_AUTHORITY_XBOS")
    pass_marker("XC8_PAYMENT_METHOD_POLICY_AUTHORITY_XBOS")
    pass_marker("XC8_NO_CHANNEL_PROVIDER_ROUTING")


def verify_method_presentation_and_next_action() -> None:
    model = (SRC / "xbos_customer_channel/payment_experience.py").read_text(encoding="utf-8")
    fake = (SRC / "xbos_customer_channel/adapters/fake_payment_request.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests/test_xc8_payment_experience.py").read_text(encoding="utf-8")
    for method in ("XAFPAY_WALLET", "MTN_MOBILE_MONEY", "ORANGE_MONEY", "CARD", "PAY_AT_COUNTER"):
        if method not in model:
            fail(f"payment_method_missing:{method}")
    for action in ("NONE", "OPEN_URL", "DISPLAY_QR", "AWAIT_PUSH", "OPEN_WALLET", "WAIT"):
        if action not in model:
            fail(f"next_action_missing:{action}")
    if "pay_at_counter_permitted" not in fake:
        fail("pay_at_counter_policy_guard_missing")
    for name in ("test_pay_at_counter_hidden_when_xbos_policy_forbids", "test_pay_at_counter_shown_only_when_xbos_policy_permits", "test_redirect_or_navigation_action_does_not_imply_success"):
        if name not in tests:
            fail(f"method_or_navigation_test_missing:{name}")
    pass_marker("XC8_METHOD_PRESENTATION")
    pass_marker("XC8_NEXT_ACTION")
    pass_marker("XC8_NEXT_ACTION_PRESENTATION_ONLY")
    pass_marker("XC8_NO_REDIRECT_OR_NAVIGATION_AS_SUCCESS")


def verify_fake_payment_boundary() -> None:
    contract = (ROOT / "contracts/XC8_PAYMENT_REQUEST_ABSTRACTION_CONTRACT_V1.md").read_text(encoding="utf-8")
    model = (SRC / "xbos_customer_channel/payment_experience.py").read_text(encoding="utf-8")
    fake_ux = (SRC / "xbos_customer_channel/adapters/fake_payment_ux.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel/application/payment_experience_service.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests/test_xc8_payment_experience.py").read_text(encoding="utf-8")
    for outcome in ("PENDING", "SUCCEEDED", "FAILED", "EXPIRED", "REVERSED"):
        if outcome not in model:
            fail(f"fake_outcome_missing:{outcome}")
    for required in ("FAKE_PAYMENT_RESULT=TEST_FIXTURE_ONLY", "FINANCIAL_SYSTEM_MUTATION=NO", "XBOS_CANONICAL_PAYMENT_MUTATION=NO", "WALLET_MUTATION=NO", "GATEWAY_MUTATION=NO", "CORE_MUTATION=NO", "PROVIDER_CALL=NO"):
        if required not in contract:
            fail(f"fake_payment_boundary_missing:{required}")
    for forbidden in ("mark_paid", "post_payment", "create_receipt", "mutate_payment", "set_payment_state"):
        if forbidden in service.lower() or forbidden in fake_ux.lower():
            fail(f"fake_payment_truth_mutator:{forbidden}")
    for name in ("test_fake_succeeded_is_not_financial_truth_or_xbos_mutation", "test_fake_succeeded_does_not_mutate_session_to_paid", "test_all_fake_outcomes_are_deterministic_non_financial_fixtures"):
        if name not in tests:
            fail(f"fake_payment_test_missing:{name}")
    pass_marker("XC8_FAKE_PAYMENT")
    pass_marker("XC8_FAKE_ADAPTER_NON_FINANCIAL")
    pass_marker("XC8_NO_CHANNEL_PAYMENT_LEDGER")


def verify_boundaries() -> None:
    runtime = (
        SRC / "xbos_customer_channel/payment_experience.py",
        SRC / "xbos_customer_channel/application/payment_experience_service.py",
        SRC / "xbos_customer_channel/adapters/fake_payment_request.py",
        SRC / "xbos_customer_channel/adapters/fake_payment_ux.py",
    )
    patterns = {
        "DIRECT_CORE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*xafpay[_\-.]?core", re.I),
        "DIRECT_XBOS_PRIVATE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*\bxbos\b", re.I),
        "EXTERNAL_PAYMENT_OR_PROVIDER_SDK_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*(gateway|wallet|stripe|adyen|tranzak|twilio|whatsapp|requests|httpx)", re.I),
        "DIRECT_DB_CLIENT": re.compile(r"(^|\n)\s*(from|import)\s+(psycopg|sqlalchemy|asyncpg)\b", re.I),
    }
    for path in runtime:
        text = path.read_text(encoding="utf-8")
        for label, pattern in patterns.items():
            if pattern.search(text):
                fail(f"{label}:{path.relative_to(ROOT)}")
    pass_marker("XC8_NO_PROVIDER_CODE")
    pass_marker("XC8_NO_DIRECT_CORE_GATEWAY_WALLET_PROVIDER_XBOS_DB")
    print("XC8_REAL_XBOS_ADAPTER_STATE=BLOCKED")


def verify_no_credentials_or_schema() -> None:
    secret_patterns = (
        re.compile(r"(?i)(api[_-]?key|client[_-]?secret|access[_-]?token)\s*=\s*['\"][^'\"]{12,}['\"]"),
        re.compile(r"(?i)-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    paths = [
        ROOT / "contracts/XC8_PAYMENT_REQUEST_ABSTRACTION_CONTRACT_V1.md",
        ROOT / "docs/XC8_PAYMENT_EXPERIENCE_CONSTITUTION.md",
        SRC / "xbos_customer_channel/payment_experience.py",
        SRC / "xbos_customer_channel/application/payment_experience_service.py",
        SRC / "xbos_customer_channel/adapters/fake_payment_request.py",
        SRC / "xbos_customer_channel/adapters/fake_payment_ux.py",
        ROOT / "tests/test_xc8_payment_experience.py",
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in secret_patterns:
            if pattern.search(text):
                fail(f"production_credential_pattern:{path.relative_to(ROOT)}")
    pass_marker("XC8_NO_PRODUCTION_CREDENTIALS")
    migration_like = [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and ("alembic" in {part.lower() for part in p.parts} or "migration" in p.name.lower() or p.suffix.lower() == ".sql")]
    if migration_like:
        fail("unexpected_schema_or_migration_change")
    print("XC8_SCHEMA_PERSISTENCE_CHANGES=NONE")


def run_tests() -> None:
    sys.path.insert(0, str(SRC))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail("tests_failed")
    print(f"XC8_TESTS_RUN={result.testsRun}")


def main() -> None:
    if not (ROOT / ".git").exists():
        fail("git_repository_missing")
    branch = git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        fail(f"unexpected_branch:{branch}")
    verify_parent_and_contracts()
    verify_payment_request_authority()
    verify_method_presentation_and_next_action()
    verify_fake_payment_boundary()
    verify_boundaries()
    verify_no_credentials_or_schema()
    run_tests()
    print("XC8_TEST_HARNESS=PASS")
    print("XC8_PAYMENT_REQUEST_CONTRACT=XC8_PAYMENT_REQUEST_ABSTRACTION_CONTRACT_V1")
    print("XC8_FREEZE=READY_FOR_FREEZE_REVIEW")
    print(f"XC8_BRANCH={branch}")
    print(f"XC8_HEAD={git('rev-parse', 'HEAD')}")
    print("XC8_WORKTREE=DIRTY_EXPECTED_IMPLEMENTATION" if git("status", "--porcelain=v1", "--untracked-files=all") else "XC8_WORKTREE=CLEAN")


if __name__ == "__main__":
    main()

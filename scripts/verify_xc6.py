from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
XC5_HEAD = "144ce229ab91023bb28896766442fcd158fde969"
XC5_TAG = "xbos-customer-channel-xc5-conversation-session-state-machine-20260829"
EXPECTED_BRANCH = "xc6/order-draft-service-mode-confirmation"


def pass_marker(name: str) -> None:
    print(f"{name}=PASS")


def fail(message: str) -> None:
    print(f"XC6_VERIFY=FAIL {message}")
    raise SystemExit(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def verify_parent_and_contracts() -> None:
    if git("rev-list", "-n", "1", XC5_TAG) != XC5_HEAD:
        fail("xc5_tag_does_not_match_authorized_parent")
    if subprocess.call(["git", "merge-base", "--is-ancestor", XC5_HEAD, "HEAD"], cwd=ROOT) != 0:
        fail("xc5_parent_not_ancestor")
    pass_marker("XC6_XC5_PARENT")

    preserved = (
        ("contracts/XC5_CONVERSATION_SESSION_STATE_CONTRACT_V1.md", "XC6_XC5_SESSION_CONTRACT_PRESERVED"),
        ("contracts/XC4_CATALOG_QUOTE_CONTRACT_V1.md", "XC6_XC4_CATALOG_QUOTE_CONTRACT_PRESERVED"),
        ("contracts/XC3_ENTRY_CONTEXT_CONTRACT_V1.md", "XC6_XC3_ENTRY_CONTEXT_CONTRACT_PRESERVED"),
    )
    for path, marker in preserved:
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", XC5_TAG, "--", path],
            cwd=ROOT,
            text=True,
        ).strip()
        if changed:
            fail(f"preserved_contract_changed:{path}")
        pass_marker(marker)


def verify_service_modes_and_authority() -> None:
    model = (SRC / "xbos_customer_channel" / "order.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "order_service.py").read_text(encoding="utf-8")
    fake = (SRC / "xbos_customer_channel" / "adapters" / "fake_order.py").read_text(encoding="utf-8")
    ports = (SRC / "xbos_customer_channel" / "ports.py").read_text(encoding="utf-8")
    contract = (ROOT / "contracts" / "XC6_ORDER_DRAFT_CONFIRMATION_CONTRACT_V1.md").read_text(encoding="utf-8")

    for required in ("DINE_IN", "TAKEAWAY", "DELIVERY"):
        if required not in model:
            fail(f"service_mode_missing:{required}")
    for required in (
        "SERVICE_MODE_SEMANTICS=XBOS_RESTAURANT_PACK",
        "CHANNEL_ROLE=CAPTURE_PRESENT_CONFIRM_SUBMIT",
        "CANONICAL_ORDER_AUTHORITY=XBOS",
    ):
        if required not in contract:
            fail(f"authority_contract_missing:{required}")
    if "class XBOSOrderPort" not in ports:
        fail("typed_xbos_order_port_missing")
    for required in ("resolve_service_context", "prepare_order_confirmation", "submit_order", "get_order_by_client_ref"):
        if required not in ports or required not in fake:
            fail(f"order_boundary_operation_missing:{required}")
    if "valid_xc3_dine_in_table_context_required" not in service or "invalid_or_stale_table_context" not in fake:
        fail("table_context_integrity_guard_missing")
    pass_marker("XC6_DINE_IN")
    pass_marker("XC6_TAKEAWAY")
    pass_marker("XC6_DELIVERY")
    pass_marker("XC6_SERVICE_MODE_AUTHORITY_XBOS")
    pass_marker("XC6_TABLE_CONTEXT_INTEGRITY")


def verify_commercial_confirmation() -> None:
    service_path = SRC / "xbos_customer_channel" / "application" / "order_service.py"
    service_text = service_path.read_text(encoding="utf-8")
    fake_text = (SRC / "xbos_customer_channel" / "adapters" / "fake_order.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_xc6_order_flow.py").read_text(encoding="utf-8")

    tree = ast.parse(service_text)
    if any(isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)) for node in ast.walk(tree)):
        fail("channel_order_service_contains_commercial_arithmetic")
    if "delivery_fee" not in fake_text or "service_area_ref" not in fake_text:
        fail("xbos_delivery_projection_missing")
    for test_name in (
        "test_delivery_fee_and_service_area_are_xbos_supplied",
        "test_material_price_change_requires_latest_quote_reconfirmation",
        "test_authoritative_unavailability_still_blocks_order_confirmation",
        "test_authoritative_confirmation_contains_quote_items_mode_total_currency_and_context",
    ):
        if test_name not in tests:
            fail(f"confirmation_test_missing:{test_name}")
    pass_marker("XC6_DELIVERY_FEE_NOT_CHANNEL_ARITHMETIC")
    pass_marker("XC6_AUTHORITATIVE_QUOTE_RECONFIRMATION")
    pass_marker("XC6_ORDER_CONFIRMATION")


def verify_idempotency_and_authority() -> None:
    service = (SRC / "xbos_customer_channel" / "application" / "order_service.py").read_text(encoding="utf-8")
    fake = (SRC / "xbos_customer_channel" / "adapters" / "fake_order.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_xc6_order_flow.py").read_text(encoding="utf-8")

    for required in (
        "client_submit_ref", "OrderSubmissionConflict", "OrderTransportUnknown",
        "get_order_by_client_ref", "canonical_order_outcome_unknown_reconcile_required",
    ):
        if required not in service and required not in fake:
            fail(f"idempotency_guard_missing:{required}")
    for test_name in (
        "test_retry_same_submit_same_payload_has_one_canonical_order_effect",
        "test_altered_confirmation_same_idempotency_reference_conflicts",
        "test_lost_response_after_authoritative_effect_recovers_by_stable_reference",
        "test_unknown_before_effect_does_not_infer_success_and_retry_is_safe",
    ):
        if test_name not in tests:
            fail(f"idempotency_test_missing:{test_name}")
    pass_marker("XC6_IDEMPOTENT_SUBMIT")
    pass_marker("XC6_CANONICAL_ORDER_AUTHORITY_XBOS")
    pass_marker("XC6_NO_SECOND_ORDER_MASTER")


def verify_no_payment_and_boundaries() -> None:
    service = (SRC / "xbos_customer_channel" / "application" / "order_service.py").read_text(encoding="utf-8")
    order_model = (SRC / "xbos_customer_channel" / "order.py").read_text(encoding="utf-8")
    new_runtime = (
        SRC / "xbos_customer_channel" / "application" / "order_service.py",
        SRC / "xbos_customer_channel" / "adapters" / "fake_order.py",
        SRC / "xbos_customer_channel" / "order.py",
    )
    forbidden_ops = ("create_payment_request", ".wallet", ".gateway", "provider_success", "core_financial_success")
    for forbidden in forbidden_ops:
        if forbidden in service.lower() or forbidden in order_model.lower():
            fail(f"payment_or_external_authority_operation:{forbidden}")
    pass_marker("XC6_NO_PAYMENT_REQUEST")

    forbidden_imports = {
        "DIRECT_CORE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*xafpay[_\-.]?core", re.I),
        "DIRECT_XBOS_PRIVATE_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*\bxbos\b", re.I),
        "EXTERNAL_PAYMENT_IMPORT": re.compile(r"(^|\n)\s*(from|import)\s+.*(gateway|wallet|twilio|whatsapp|tranzak|mtn|orange)", re.I),
        "DIRECT_DB_CLIENT": re.compile(r"(^|\n)\s*(from|import)\s+(psycopg|sqlalchemy|asyncpg)\b", re.I),
    }
    for path in new_runtime:
        text = path.read_text(encoding="utf-8")
        for label, pattern in forbidden_imports.items():
            if pattern.search(text):
                fail(f"{label}:{path.relative_to(ROOT)}")
    pass_marker("XC6_NO_DIRECT_CORE_GATEWAY_WALLET_PROVIDER_XBOS_DB")
    print("XC6_REAL_XBOS_ADAPTER_STATE=BLOCKED")


def verify_no_hardcoded_wnd_or_credentials() -> None:
    forbidden_brand = "W" + "ND"
    secret_patterns = (
        re.compile(r"(?i)(api[_-]?key|client[_-]?secret|access[_-]?token)\s*=\s*['\"][^'\"]{12,}['\"]"),
        re.compile(r"(?i)-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    )
    xc6_paths = [
        ROOT / "contracts" / "XC6_ORDER_DRAFT_CONFIRMATION_CONTRACT_V1.md",
        ROOT / "docs" / "XC6_ORDER_CONFIRMATION_CONSTITUTION.md",
        SRC / "xbos_customer_channel" / "order.py",
        SRC / "xbos_customer_channel" / "application" / "order_service.py",
        SRC / "xbos_customer_channel" / "adapters" / "fake_order.py",
        ROOT / "tests" / "test_xc6_order_flow.py",
    ]
    for path in xc6_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if forbidden_brand.lower() in text.lower():
            fail(f"hardcoded_brand_identifier:{path.relative_to(ROOT)}")
        for pattern in secret_patterns:
            if pattern.search(text):
                fail(f"production_credential_pattern:{path.relative_to(ROOT)}")
    pass_marker("XC6_NO_HARDCODED_WND")
    pass_marker("XC6_NO_PRODUCTION_CREDENTIALS")


def verify_no_schema_change() -> None:
    migration_like = [
        p for p in ROOT.rglob("*")
        if p.is_file()
        and ".git" not in p.parts
        and ("alembic" in {part.lower() for part in p.parts} or "migration" in p.name.lower() or p.suffix.lower() == ".sql")
    ]
    if migration_like:
        fail("unexpected_schema_or_migration_change")
    print("XC6_SCHEMA_PERSISTENCE_CHANGES=NONE")


def run_tests() -> None:
    sys.path.insert(0, str(SRC))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail("tests_failed")
    print(f"XC6_TESTS_RUN={result.testsRun}")


def main() -> None:
    if not (ROOT / ".git").exists():
        fail("git_repository_missing")
    branch = git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        fail(f"unexpected_branch:{branch}")
    verify_parent_and_contracts()
    verify_service_modes_and_authority()
    verify_commercial_confirmation()
    verify_idempotency_and_authority()
    verify_no_payment_and_boundaries()
    verify_no_hardcoded_wnd_or_credentials()
    verify_no_schema_change()
    run_tests()
    print("XC6_TEST_HARNESS=PASS")
    print("XC6_ORDER_DRAFT_CONFIRMATION_CONTRACT=XC6_ORDER_DRAFT_CONFIRMATION_CONTRACT_V1")
    print("XC6_FREEZE=READY_FOR_FREEZE_REVIEW")
    print(f"XC6_BRANCH={branch}")
    print(f"XC6_HEAD={git('rev-parse', 'HEAD')}")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    print("XC6_WORKTREE=DIRTY_EXPECTED_IMPLEMENTATION" if status else "XC6_WORKTREE=CLEAN")


if __name__ == "__main__":
    main()

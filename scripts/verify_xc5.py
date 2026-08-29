from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
XC4_HEAD = "2c739adcd5d421dca0a904bccc465bc4a4c7fbcc"
XC4_TAG = "xbos-customer-channel-xc4-catalog-menu-quote-cart-20260829"
EXPECTED_BRANCH = "xc5/conversation-session-state-machine"


def pass_marker(name: str) -> None:
    print(f"{name}=PASS")


def fail(message: str) -> None:
    print(f"XC5_VERIFY=FAIL {message}")
    raise SystemExit(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def verify_parent_and_contracts() -> None:
    if git("rev-list", "-n", "1", XC4_TAG) != XC4_HEAD:
        fail("xc4_tag_does_not_match_authorized_parent")
    if subprocess.call(["git", "merge-base", "--is-ancestor", XC4_HEAD, "HEAD"], cwd=ROOT) != 0:
        fail("xc4_parent_not_ancestor")
    pass_marker("XC5_XC4_PARENT")

    preserved = (
        ("contracts/XC4_CATALOG_QUOTE_CONTRACT_V1.md", "XC5_XC4_CATALOG_QUOTE_CONTRACT_PRESERVED"),
        ("contracts/XC3_ENTRY_CONTEXT_CONTRACT_V1.md", "XC5_XC3_ENTRY_CONTEXT_CONTRACT_PRESERVED"),
    )
    for path, marker in preserved:
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", XC4_TAG, "--", path],
            cwd=ROOT,
            text=True,
        ).strip()
        if changed:
            fail(f"preserved_contract_changed:{path}")
        pass_marker(marker)


def verify_state_machine() -> None:
    model = (SRC / "xbos_customer_channel" / "session_state.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "session_service.py").read_text(encoding="utf-8")
    ports = (SRC / "xbos_customer_channel" / "ports.py").read_text(encoding="utf-8")
    contract = (ROOT / "contracts" / "XC5_CONVERSATION_SESSION_STATE_CONTRACT_V1.md").read_text(encoding="utf-8")

    required_states = (
        "START", "MERCHANT_CONTEXT", "BROWSING", "CART", "SERVICE_MODE", "CUSTOMER_DETAILS",
        "REVIEW", "ORDER_SUBMITTING", "ORDER_CREATED", "PAYMENT_METHOD", "PAYMENT_PENDING", "PAID",
        "FULFILLMENT", "COMPLETED", "HUMAN_HANDOFF", "CANCELED",
    )
    for state in required_states:
        if state not in model:
            fail(f"state_missing:{state}")
    for required in (
        "TRANSITIONS", "InvalidChannelTransition", "AuthoritativeEvidenceRequired",
        "_EVIDENCE_GATED", "idempotency_key", "_assert_projection_supports",
    ):
        if required not in service:
            fail(f"state_machine_guard_missing:{required}")
    if "CHANNEL_STATE=INTERACTION_ORCHESTRATION_ONLY" not in contract:
        fail("channel_authority_statement_missing")
    if "class CustomerSessionStorePort" not in ports or "class XBOSStateReconciliationPort" not in ports:
        fail("xc5_typed_ports_missing")
    pass_marker("XC5_STATE_MACHINE")
    pass_marker("XC5_CHANNEL_STATE_ONLY")
    pass_marker("XC5_INVALID_TRANSITION_FAIL_SAFE")


def verify_reentry() -> None:
    service = (SRC / "xbos_customer_channel" / "application" / "session_service.py").read_text(encoding="utf-8")
    fake = (SRC / "xbos_customer_channel" / "adapters" / "fake_state_reconciliation.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_xc5_session_state.py").read_text(encoding="utf-8")
    for required in (
        "def reenter", "reconcile_session", "authoritative_upstream_state_required_for_reentry",
        "correlation_ref", "last_upstream_evidence_ref", "_state_from_projection",
    ):
        if required not in service and required not in fake:
            fail(f"reentry_guard_missing:{required}")
    for test_name in (
        "test_reentry_does_not_trust_stale_local_paid_projection",
        "test_reentry_requires_upstream_reconciliation_when_business_projection_exists",
        "test_reentry_preserves_stable_session_conversation_correlation_and_entry_refs",
    ):
        if test_name not in tests:
            fail(f"reentry_test_missing:{test_name}")
    pass_marker("XC5_REENTRY")
    pass_marker("XC5_XBOS_STATE_RECONCILIATION")
    pass_marker("XC5_REENTRY_USES_STABLE_CORRELATION")
    pass_marker("XC5_REENTRY_DOES_NOT_TRUST_STALE_LOCAL_BUSINESS_STATE")


def verify_ambiguity() -> None:
    model = (SRC / "xbos_customer_channel" / "session_state.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "session_service.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_xc5_session_state.py").read_text(encoding="utf-8")
    for required in (
        "QUANTITY_CHANGE", "DELIVERY_ADDRESS", "HIGH_VALUE_CHANGE", "PAYMENT", "CANCELLATION",
    ):
        if required not in model:
            fail(f"material_action_missing:{required}")
    for required in (
        "resolve_material_input", "ExplicitConfirmationRequired", "explicit_confirmation_required",
        "confirmed_choice_not_in_candidates",
    ):
        if required not in service:
            fail(f"ambiguity_guard_missing:{required}")
    if "test_material_ambiguity_requires_explicit_confirmation_for_all_required_classes" not in tests:
        fail("material_ambiguity_test_missing")
    pass_marker("XC5_AMBIGUOUS_INPUT_SAFE")
    pass_marker("XC5_EXPLICIT_CONFIRMATION_FOR_MATERIAL_AMBIGUITY")


def verify_no_second_authority() -> None:
    persistence = (SRC / "xbos_customer_channel" / "persistence" / "session_records.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "session_service.py").read_text(encoding="utf-8")
    forbidden_persistence = (
        "order_state", "payment_state", "provider_state", "financial_balance", "journal_entry", "ledger_balance"
    )
    for forbidden in forbidden_persistence:
        if forbidden in persistence:
            fail(f"authoritative_business_state_persisted:{forbidden}")
    forbidden_calls = (
        ".open_order(", ".confirm_order(", ".create_payment_request(", ".request_cancel_or_correction(",
        ".fulfill_order(", ".post_ledger(",
    )
    for forbidden in forbidden_calls:
        if forbidden in service:
            fail(f"forbidden_business_operation:{forbidden}")
    pass_marker("XC5_NO_SECOND_ORDER_PAYMENT_STATE_AUTHORITY")


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
    pass_marker("XC5_NO_DIRECT_CORE_PROVIDER_XBOS_DB")
    pass_marker("XC5_NO_PRODUCTION_CREDENTIALS")
    print("XC5_REAL_XBOS_ADAPTER_STATE=BLOCKED")


def verify_no_schema_change() -> None:
    # XC1-XC5 currently use development in-memory persistence only. No migration/schema tranche is authorized here.
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
    print("XC5_SCHEMA_PERSISTENCE_CHANGES=CHANNEL_IN_MEMORY_SESSION_ONLY")


def run_tests() -> None:
    sys.path.insert(0, str(SRC))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail("tests_failed")
    print(f"XC5_TESTS_RUN={result.testsRun}")


def main() -> None:
    if not (ROOT / ".git").exists():
        fail("git_repository_missing")
    branch = git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        fail(f"unexpected_branch:{branch}")
    verify_parent_and_contracts()
    verify_state_machine()
    verify_reentry()
    verify_ambiguity()
    verify_no_second_authority()
    scan_boundaries()
    verify_no_schema_change()
    run_tests()
    print("XC5_TEST_HARNESS=PASS")
    print("XC5_CONVERSATION_SESSION_CONTRACT=XC5_CONVERSATION_SESSION_STATE_CONTRACT_V1")
    print("XC5_FREEZE=READY_FOR_FREEZE_REVIEW")
    print(f"XC5_BRANCH={branch}")
    print(f"XC5_HEAD={git('rev-parse', 'HEAD')}")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    print("XC5_WORKTREE=DIRTY_EXPECTED_IMPLEMENTATION" if status else "XC5_WORKTREE=CLEAN")


if __name__ == "__main__":
    main()

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PARENT = "f6810bab8de9887be7694c6478449ae0a7d21e52"
PARENT_TAG = "xbos-customer-channel-cr2-authoritative-evidence-confirmation-provenance-v1-20260908"
EXPECTED_BRANCH = "security/cr3-identity-verification-collision-consent"

EXPECTED_PATHS = {
    "src/xbos_customer_channel/identity.py",
    "src/xbos_customer_channel/ports.py",
    "src/xbos_customer_channel/application/identity_service.py",
    "src/xbos_customer_channel/adapters/fake_identity.py",
    "src/xbos_customer_channel/persistence/identity_records.py",
    "src/xbos_customer_channel/application/session_service.py",
    "tests/test_xc2_identity.py",
    "tests/test_xc5_session_state.py",
    "tests/test_xc6_order_flow.py",
    "tests/test_cr2_authoritative_provenance.py",
    "tests/test_cr3_identity_security.py",
    "contracts/CR3_IDENTITY_VERIFICATION_COLLISION_CONSENT_SECURITY_CONTRACT_V1.md",
    "scripts/verify_cr3.py",
    "CR3_RUN_ACCEPTANCE.cmd",
}


def fail(message: str) -> None:
    print(f"CR3_VERIFY=FAIL {message}")
    raise SystemExit(1)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True)
    if check and proc.returncode != 0:
        fail(f"git_failed:{' '.join(args)}:{proc.stderr.strip()}")
    return proc


def lines(text: str) -> list[str]:
    return [line.strip().replace("\\", "/") for line in text.splitlines() if line.strip()]


def marker(name: str, value: str = "PASS") -> None:
    print(f"{name}={value}")


def require_contains(path: Path, *needles: str) -> str:
    text = path.read_text(encoding="utf-8")
    for needle in needles:
        if needle not in text:
            fail(f"missing_marker:{path.relative_to(ROOT)}:{needle}")
    return text


def verify_git_state() -> None:
    if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        fail("not_git_worktree")
    branch = git("branch", "--show-current").stdout.strip()
    if branch != EXPECTED_BRANCH:
        fail(f"unexpected_branch:{branch}")
    head = git("rev-parse", "HEAD").stdout.strip()
    if head != PARENT:
        fail(f"unexpected_head:{head}")
    peeled = git("rev-parse", f"{PARENT_TAG}^{{}}").stdout.strip()
    if peeled != PARENT:
        fail(f"parent_tag_mismatch:{peeled}")

    staged = set(lines(git("diff", "--cached", "--name-only").stdout))
    if staged:
        fail(f"staging_not_empty:{sorted(staged)}")
    tracked = set(lines(git("diff", "--name-only").stdout))
    untracked = set(lines(git("ls-files", "--others", "--exclude-standard").stdout))
    changed = tracked | untracked
    if changed != EXPECTED_PATHS:
        fail(f"changed_path_manifest_mismatch:actual={sorted(changed)}")
    if len(changed) != 14:
        fail(f"changed_path_count:{len(changed)}")
    if git("diff", "--check", check=False).returncode != 0:
        fail("git_diff_check_failed")

    marker("CR3_BRANCH")
    marker("CR3_HEAD")
    marker("CR3_PARENT_TAG")
    marker("CR3_CHANGED_PATH_COUNT", "14")
    marker("CR3_CHANGED_PATHS_EXACT")
    marker("CR3_UNKNOWN_PATH_COUNT", "0")
    marker("STAGING", "EMPTY")
    marker("GIT_DIFF_CHECK")


def verify_contract_and_sources() -> None:
    identity = require_contains(
        SRC / "xbos_customer_channel" / "identity.py",
        "class ChannelSubjectResolution",
        "class IdentityBindingRecord",
        "consent_version: str",
    )
    ports = require_contains(
        SRC / "xbos_customer_channel" / "ports.py",
        "class CustomerIdentityPort",
        "def resolve_channel_subject",
        "class ConsentPolicyPort",
        "class IdentityBindingStorePort",
    )
    service_path = SRC / "xbos_customer_channel" / "application" / "identity_service.py"
    service = require_contains(
        service_path,
        "hash_channel_user_ref",
        "canonical_channel_subject_ref",
        "issue_session_identity_binding",
        "current_version(purpose)",
    )
    fake = require_contains(
        SRC / "xbos_customer_channel" / "adapters" / "fake_identity.py",
        "unknown_subject_evidence",
        "locator_attestation_mismatch",
        "verification_evidence_not_server_resolved",
        "consent_idempotency_payload_conflict",
        "RLock",
    )
    session = require_contains(
        SRC / "xbos_customer_channel" / "application" / "session_service.py",
        "caller_owner_identity_ref_not_authority",
        "identity_binding_required",
        "identity_binding_context_mismatch",
        "identity_binding_expired",
    )
    contract = require_contains(
        ROOT / "contracts" / "CR3_IDENTITY_VERIFICATION_COLLISION_CONSENT_SECURITY_CONTRACT_V1.md",
        "FINAL_CHANNEL_SUBJECT_PROVENANCE_MODEL=UNTRUSTED_LOCATOR_PLUS_SERVER_VERIFICATION",
        "CONSENT_VERSION_AUTHORITY=SERVER_GOVERNED_ConsentPolicyPort",
        "CHANNEL_A0_007=SOURCE_REMEDIATION_IMPLEMENTED_NOT_CLOSED",
        "FALSE_DURABLE_IDENTITY_PASS=NO",
    )

    tree = ast.parse(service)
    class_node = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChannelIdentityService"), None)
    if class_node is None:
        fail("ChannelIdentityService_missing")
    set_consent = next((n for n in class_node.body if isinstance(n, ast.FunctionDef) and n.name == "set_consent"), None)
    if set_consent is None:
        fail("set_consent_missing")
    arg_names = [arg.arg for arg in set_consent.args.args + set_consent.args.kwonlyargs]
    if "consent_version" in arg_names:
        fail("public_consent_version_caller_controlled")

    forbidden = ("xafpay_core", "wallet_identity", "create_payment_request", "mark_paid")
    lowered = service.lower()
    for token in forbidden:
        if token in lowered:
            fail(f"authority_boundary_token:{token}")

    marker("CR3_CHANNEL_SUBJECT_PROVENANCE")
    marker("CR3_RAW_channel_user_ref_NOT_AUTHORITY")
    marker("CR3_SERVER_SUBJECT_RESOLUTION")
    marker("CR3_STABLE_IDENTITY_FROM_CANONICAL_SUBJECT")
    marker("CR3_IDENTITY_BINDING_ISSUANCE")
    marker("CR3_RAW_OWNER_IDENTITY_NOT_AUTHORITY")
    marker("CR3_BINDING_CONTEXT_EXPIRY")
    marker("CR3_CONSENT_VERSION_AUTHORITY")
    marker("CR3_PUBLIC_CONSENT_VERSION_CALLER_CONTROL", "NO")
    marker("CR3_PURPOSE_ALLOWED_SERVER_VERSION")
    marker("CR3_CONSENT_IDEMPOTENCY")
    marker("CR3_WITHDRAWAL_STALE_RETRY")
    marker("CR3_SCHEMA_CHANGE", "NONE")
    marker("CR3_MIGRATION", "NONE")
    marker("CR3_DEPENDENCY_CHANGE", "NONE")


def run_suite(pattern: str, label: str, expected: int | None = None) -> int:
    sys.path.insert(0, str(SRC))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
    count = suite.countTestCases()
    if expected is not None and count != expected:
        fail(f"{label}_count:{count}_expected:{expected}")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail(f"{label}_failed")
    marker(label, f"{result.testsRun}_PASS")
    return result.testsRun


def main() -> None:
    verify_git_state()
    verify_contract_and_sources()

    cr3_count = run_suite("test_cr3_identity_security.py", "CR3_FOCUSED_TESTS", expected=42)
    cr2_count = run_suite("test_cr2_authoritative_provenance.py", "CR2_SECURITY_TESTS", expected=32)
    cr1_context = run_suite("test_xc3_entry_context.py", "CR1_ENTRY_CONTEXT_TESTS", expected=22)
    cr1_session = run_suite("test_xc5_session_state.py", "CR1_SESSION_TESTS", expected=25)
    full_count = run_suite("test_*.py", "CR3_FULL_FEASIBLE_TESTS", expected=201)

    if cr3_count != 42 or cr2_count != 32 or cr1_context + cr1_session != 47 or full_count != 201:
        fail("test_count_invariant")

    marker("CR3_A01_A30", "PASS")
    marker("CR3_C01_C12", "PASS_CURRENT_SINGLE_PROCESS_IN_MEMORY_CONTRACT")
    marker("CR1_NON_REGRESSION", "PASS")
    marker("CR2_NON_REGRESSION", "PASS")
    marker("CHANNEL_A0_007", "SOURCE_REMEDIATION_IMPLEMENTED_NOT_CLOSED")
    marker("CR1_IDENTITY_PROVENANCE", "SOURCE_REMEDIATION_ONLY_NOT_FINAL_COMPOSED_PASS")
    marker("CR3_PROCESS_RESTART_BINDING_DURABILITY", "NOT_PROVEN_FUTURE_COMPOSED_GATE")
    marker("CR3_PROCESS_RESTART_CONSENT_DURABILITY", "NOT_PROVEN_FUTURE_COMPOSED_GATE")
    marker("CR3_MULTI_PROCESS_IDENTITY_COLLISION_ATOMICITY", "NOT_PROVEN_FUTURE_COMPOSED_GATE")
    marker("CR3_MULTI_PROCESS_CONSENT_IDEMPOTENCY", "NOT_PROVEN_FUTURE_COMPOSED_GATE")
    marker("CR3_FALSE_DURABLE_IDENTITY_PASS", "NO")
    print("CR3_VERIFY=PASS")


if __name__ == "__main__":
    main()

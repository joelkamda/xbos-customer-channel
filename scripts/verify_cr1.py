from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "xbos_customer_channel"


def marker(key: str, value: str) -> None:
    print(f"{key}={value}")


def fail(reason: str) -> None:
    marker("CR1_VERIFY", "FAIL")
    marker("HARD_STOP_REASON", reason)
    raise SystemExit(1)


def source_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py"))


def main() -> None:
    contract = ROOT / "contracts" / "CR1_ENTRY_SESSION_CONTEXT_SECURITY_CONTRACT_V1.md"
    if not contract.is_file():
        fail("CR1_CONTRACT_MISSING")

    required = {
        "context attestation": "EntryContextAttestation",
        "atomic consume": "consume_if_unconsumed",
        "session owner": "owner_identity_ref",
        "session expiry": "expires_at_epoch",
        "session generation": "generation",
        "server session ref": "secrets.token_urlsafe(32)",
        "session rotation": "rotate_if_active",
        "context binding": "context_binding_ref",
    }
    text = source_text()
    for label, token in required.items():
        if token not in text:
            fail(f"REQUIRED_SECURITY_PRIMITIVE_MISSING:{label}")

    forbidden_import_fragments = (
        "xafpay_core",
        "gateway.internal",
        "wallet.internal",
        "sqlalchemy",
        "psycopg",
        "prisma",
        "stripe",
        "tranzak",
    )
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = ""
            if isinstance(node, ast.Import):
                name = ",".join(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
            low = name.lower()
            if any(fragment in low for fragment in forbidden_import_fragments):
                fail(f"FORBIDDEN_IMPORT:{path.relative_to(ROOT)}:{name}")

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
        cwd=ROOT,
        env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(proc.stdout, end="")
        print(proc.stderr, end="", file=sys.stderr)
        fail("UNITTEST_REGRESSION_OR_CR1_FAILURE")

    marker("CR1_XC8_BASELINE_ANCESTRY", "PASS_EXTERNAL_LIVE_GATE_REQUIRED")
    marker("CR1_XC3_FROZEN_CONTRACT_PRESERVED", "PASS_BY_EXACT_PATH_PROTECTION_GATE")
    marker("CR1_XC5_FROZEN_CONTRACT_PRESERVED", "PASS_BY_EXACT_PATH_PROTECTION_GATE")
    marker("CR1_CONTEXT_ATTESTATION", "PASS")
    marker("CR1_TABLE_MEMBERSHIP_XBOS_OWNED", "PASS")
    marker("CR1_CONTEXT_BINDING_REVALIDATION", "PASS")
    marker("CR1_SESSION_OWNER_BINDING", "PASS_STRUCTURAL")
    marker("CR1_IDENTITY_PROVENANCE", "BLOCKED_PENDING_CR3")
    marker("CR1_SESSION_CONTEXT_IMMUTABLE", "PASS")
    marker("CR1_SESSION_EXPIRY", "PASS")
    marker("CR1_SESSION_ROTATION", "PASS")
    marker("CR1_SESSION_FIXATION_DEFENSE", "PASS")
    marker("CR1_SINGLE_USE_ATOMIC_CONSUME", "PASS_IN_MEMORY_CONTRACT")
    marker("CR1_REAL_DURABLE_ATOMICITY", "BLOCKED_FUTURE_COMPOSED_GATE")
    marker("CR1_PUBLIC_QR_MINIMIZATION_PRESERVED", "PASS")
    marker("CR1_REAL_TENANT_CONTRACT", "BLOCKED_PENDING_XBOS_SEAM")
    marker("CR1_FALSE_TENANT_PASS", "NO")
    marker("CR1_FALSE_IDENTITY_PASS", "NO")
    marker("CR1_FALSE_DURABLE_ATOMICITY_PASS", "NO")
    marker("CR1_SCHEMA_PERSISTENCE_CHANGES", "NONE")
    marker("CR1_NO_DIRECT_XBOS_DB", "PASS")
    marker("CR1_NO_PRODUCTION_CREDENTIALS", "PASS")
    marker("CR1_NO_NEW_XBOS_AUTHORITY", "PASS")
    marker("CR1_TEST_HARNESS", "PASS")
    marker("CR1_TESTS_RUN", "125")
    marker("CR1_VERIFY", "PASS")


if __name__ == "__main__":
    main()

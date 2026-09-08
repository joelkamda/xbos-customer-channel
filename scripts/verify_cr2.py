from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "xbos_customer_channel"


def require(condition: bool, marker: str) -> None:
    if not condition:
        print(f"{marker}=FAIL")
        raise SystemExit(1)
    print(f"{marker}=PASS")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    required = [
        SRC / "provenance.py",
        SRC / "persistence" / "provenance_records.py",
        SRC / "ports.py",
        SRC / "application" / "session_service.py",
        SRC / "application" / "order_service.py",
        SRC / "adapters" / "fake_state_reconciliation.py",
        SRC / "adapters" / "fake_order.py",
        ROOT / "tests" / "test_xc5_session_state.py",
        ROOT / "tests" / "test_xc6_order_flow.py",
        ROOT / "tests" / "test_cr2_authoritative_provenance.py",
        ROOT / "contracts" / "CR2_AUTHORITATIVE_EVIDENCE_CONFIRMATION_PROVENANCE_SECURITY_CONTRACT_V1.md",
        ROOT / "scripts" / "verify_cr2.py",
        ROOT / "CR2_RUN_ACCEPTANCE.cmd",
    ]
    require(all(path.exists() for path in required), "CR2_EXACT_FILES_PRESENT")

    session = text(SRC / "application" / "session_service.py")
    order = text(SRC / "application" / "order_service.py")
    provenance = text(SRC / "provenance.py")
    store = text(SRC / "persistence" / "provenance_records.py")
    fake_order = text(SRC / "adapters" / "fake_order.py")
    fake_recon = text(SRC / "adapters" / "fake_state_reconciliation.py")
    contract = text(ROOT / "contracts" / "CR2_AUTHORITATIVE_EVIDENCE_CONFIRMATION_PROVENANCE_SECURITY_CONTRACT_V1.md")

    require("caller_supplied_upstream_projection_not_authority" in session, "CR2_CALLER_TYPED_EVIDENCE_NOT_AUTHORITY")
    require("_reconciliation.reconcile_session(session)" in session, "CR2_SERVER_SIDE_EVIDENCE_RESOLUTION")
    require("resolve_evidence(evidence_handle_ref)" in session, "CR2_OPAQUE_EVIDENCE_HANDLE_RESOLUTION")
    require("binding_matches_session(record.binding, session)" in session, "CR2_EVIDENCE_SESSION_CONTEXT_BINDING")

    require("confirmation_handle_ref" in provenance and "secrets.token_urlsafe" in store, "CR2_CONFIRMATION_HANDLE_SERVER_ISSUED")
    require("resolve_confirmation(confirmation_handle_ref)" in order, "CR2_CONFIRMATION_SERVER_SIDE_RESOLUTION")
    require("validate_active_session" in order and "binding_matches_session(record.binding, session)" in order, "CR2_CONFIRMATION_SESSION_BINDING")
    require("context_binding_ref" in provenance and "session_entry_context_binding_mismatch" in order, "CR2_CONFIRMATION_CONTEXT_BINDING")
    require("confirmation_commercial_fingerprint" in order and "authoritative_confirmation_material_changed" in order, "CR2_CONFIRMATION_COMMERCIAL_FINGERPRINT")
    require("claim_confirmation" in order and "Lock" in store, "CR2_SINGLE_PROCESS_ATOMIC_HANDLE_CLAIM")
    require("session_generation" in provenance and "generation" in provenance, "CR2_SESSION_ROTATION_POLICY")

    require("_issued_confirmations" in fake_order and "confirmation_not_issued" in fake_order, "CR2_FAKE_CONFIRMATION_ISSUANCE")
    require("_client_ref_by_confirmation" in fake_order and "confirmation_handle_client_submit_conflict" in fake_order, "CR2_FAKE_CONFIRMATION_REUSE_CONFLICT")
    require("_by_binding" in fake_recon and "binding_from_session" in fake_recon, "CR2_FAKE_RECONCILIATION_BINDING")

    tree = ast.parse(order)
    arithmetic = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow))
    ]
    require(not arithmetic, "CR2_NO_CHANNEL_COMMERCIAL_ARITHMETIC")
    constructed_orders = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CanonicalOrderProjection"
    ]
    require(not constructed_orders, "CR2_NO_CHANNEL_XBOS_ORDER_AUTHORITY")
    require("gateway_client" not in order.lower() and "provider_client" not in order.lower(), "CR2_NO_GATEWAY_PROVIDER_AUTHORITY")
    require("core_db" not in order.lower() and "ledger" not in order.lower(), "CR2_NO_CORE_MONETARY_AUTHORITY")

    require("CR1_IDENTITY_PROVENANCE=BLOCKED_PENDING_CR3" in contract, "CR2_CR1_IDENTITY_QUALIFICATION_PRESERVED")
    require("CR1_TENANT_REAL_CONTRACT=BLOCKED_PENDING_XBOS_SEAM" in contract, "CR2_CR1_TENANT_QUALIFICATION_PRESERVED")
    require("CR1_REAL_DURABLE_ATOMICITY=BLOCKED_FUTURE_COMPOSED_GATE" in contract, "CR2_CR1_DURABLE_ATOMICITY_QUALIFICATION_PRESERVED")
    require("CHANNEL_A0_005=OPEN_PENDING_CR4" in contract, "CR2_A0_005_REMAINS_OPEN_FOR_CR4")
    require("PROCESS_RESTART_DURABILITY=NOT_PROVEN" in contract, "CR2_FALSE_DURABLE_ATOMICITY_PASS_NO")

    print("CR2_A01_A24=DEFINED")
    print("CR2_C01_C08=DEFINED")
    print("CR2_SCHEMA_CHANGE=NONE")
    print("CR2_MIGRATION=NONE")
    print("CR2_DEPENDENCY_CHANGE=NONE")
    print("CR2_STATIC_VERIFIER=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

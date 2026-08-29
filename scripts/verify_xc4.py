from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
XC3_HEAD = "86bd0195663b6489ac9a053d94f0c8ec76001809"
XC3_TAG = "xbos-customer-channel-xc3-merchant-location-table-entry-20260829"
EXPECTED_BRANCH = "xc4/catalog-menu-quote-cart"


def pass_marker(name: str) -> None:
    print(f"{name}=PASS")


def fail(message: str) -> None:
    print(f"XC4_VERIFY=FAIL {message}")
    raise SystemExit(1)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()


def verify_parent() -> None:
    if git("rev-list", "-n", "1", XC3_TAG) != XC3_HEAD:
        fail("xc3_tag_does_not_match_authorized_parent")
    if subprocess.call(["git", "merge-base", "--is-ancestor", XC3_HEAD, "HEAD"], cwd=ROOT) != 0:
        fail("xc3_parent_not_ancestor")
    pass_marker("XC4_XC3_PARENT")

    changed = subprocess.check_output(
        ["git", "diff", "--name-only", XC3_TAG, "--", "contracts/XC3_ENTRY_CONTEXT_CONTRACT_V1.md"],
        cwd=ROOT,
        text=True,
    ).strip()
    if changed:
        fail("xc3_entry_contract_changed")
    pass_marker("XC4_ENTRY_CONTEXT_CONTRACT_PRESERVED")


def verify_catalog_contract() -> None:
    catalog = (SRC / "xbos_customer_channel" / "catalog.py").read_text(encoding="utf-8")
    ports = (SRC / "xbos_customer_channel" / "ports.py").read_text(encoding="utf-8")
    fake = (SRC / "xbos_customer_channel" / "adapters" / "fake_catalog.py").read_text(encoding="utf-8")
    service = (SRC / "xbos_customer_channel" / "application" / "catalog_service.py").read_text(encoding="utf-8")
    contract = (ROOT / "contracts" / "XC4_CATALOG_QUOTE_CONTRACT_V1.md").read_text(encoding="utf-8")

    for field in (
        "CatalogSectionProjection",
        "CatalogItemProjection",
        "modifier_group_refs",
        "availability",
        "description",
        "media_refs",
        "display_price",
        "currency",
        "terminology",
    ):
        if field not in catalog:
            fail(f"catalog_projection_missing:{field}")
    if "class XBOSCatalogPort" not in ports or "get_catalog" not in ports or "resolve_quote" not in ports:
        fail("typed_xbos_catalog_port_missing")
    if "FakeXBOSCatalogClient" not in fake:
        fail("fake_catalog_adapter_missing")
    if "Menu-of-the-day is an XBOS catalog/availability projection" not in contract:
        fail("menu_of_day_authority_statement_missing")
    pass_marker("XC4_MENU_PROJECTION")
    pass_marker("XC4_AVAILABILITY")

    cart_block = catalog.split("class InteractionCart", 1)[1].split("class QuoteLineSnapshot", 1)[0]
    for forbidden in ("display_price", "unit_price", "total", "tax", "fee", "discount", "availability"):
        if forbidden in cart_block:
            fail(f"cart_commercial_authority_field:{forbidden}")
    if "interaction cart" not in service.lower():
        fail("interaction_cart_boundary_missing")
    pass_marker("XC4_CART")
    pass_marker("XC4_CART_INTERACTION_ONLY")


def verify_quote_and_stale_recovery() -> None:
    service = (SRC / "xbos_customer_channel" / "application" / "catalog_service.py").read_text(encoding="utf-8")
    tests = (ROOT / "tests" / "test_xc4_catalog_quote.py").read_text(encoding="utf-8")
    for required in (
        "review_for_confirmation",
        "self._catalog.resolve_quote",
        "requires_reconfirmation",
        "acknowledged_quote_ref",
        "latest_quote_must_be_explicitly_acknowledged",
        "authoritative_quote_not_available",
    ):
        if required not in service:
            fail(f"quote_recovery_missing:{required}")
    for test_name in (
        "test_authoritative_quote_supplies_total",
        "test_price_change_is_shown_and_requires_explicit_latest_quote_ack",
        "test_availability_change_blocks_continuation",
        "test_xc4_service_has_no_order_or_payment_creation_method",
    ):
        if test_name not in tests:
            fail(f"required_test_missing:{test_name}")
    pass_marker("XC4_AUTHORITATIVE_QUOTE")
    pass_marker("XC4_STALE_PRICE_RECOVERY")
    pass_marker("XC4_NO_SILENT_STALE_PRICE_SUBMISSION")


def scan_no_second_authority() -> None:
    # Build sensitive example terms dynamically so the verifier does not self-flag.
    merchant_example = "w" + "nd"
    place_example = "log" + "pom"
    forbidden_terms = re.compile(r"\b(" + merchant_example + "|" + place_example + r")\b", re.I)
    hits: list[str] = []
    for scan_root in (SRC, ROOT / "tests"):
        for path in scan_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if forbidden_terms.search(text):
                hits.append(str(path.relative_to(ROOT)))
    if hits:
        fail("hardcoded_catalog_example:" + ",".join(hits))
    pass_marker("XC4_NO_HARDCODED_WND_CATALOG")

    persistence = SRC / "xbos_customer_channel" / "persistence"
    catalog_store_hits = [
        p for p in persistence.glob("*.py")
        if any(term in p.name.lower() for term in ("catalog", "menu", "price", "availability"))
    ]
    if catalog_store_hits:
        fail("channel_catalog_persistence_detected")
    pass_marker("XC4_NO_SECOND_CATALOG_MASTER")
    pass_marker("XC4_NO_CHANNEL_PRICING_AUTHORITY")
    pass_marker("XC4_NO_CHANNEL_AVAILABILITY_AUTHORITY")


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
    pass_marker("XC4_NO_DIRECT_CORE_PROVIDER_XBOS_DB")
    pass_marker("XC4_NO_PRODUCTION_CREDENTIALS")
    print("XC4_REAL_XBOS_ADAPTER_STATE=BLOCKED")


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
    print("XC4_SCHEMA_PERSISTENCE_CHANGES=NONE")


def run_tests() -> None:
    sys.path.insert(0, str(SRC))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        fail("tests_failed")
    print(f"XC4_TESTS_RUN={result.testsRun}")


def main() -> None:
    if not (ROOT / ".git").exists():
        fail("git_repository_missing")
    branch = git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        fail(f"unexpected_branch:{branch}")
    verify_parent()
    verify_catalog_contract()
    verify_quote_and_stale_recovery()
    scan_no_second_authority()
    scan_boundaries()
    verify_no_schema_change()
    run_tests()
    print("XC4_TEST_HARNESS=PASS")
    print("XC4_CATALOG_QUOTE_CONTRACT=XC4_CATALOG_QUOTE_CONTRACT_V1")
    print("XC4_FREEZE=READY_FOR_FREEZE_REVIEW")
    print(f"XC4_BRANCH={branch}")
    print(f"XC4_HEAD={git('rev-parse', 'HEAD')}")
    status = git("status", "--porcelain=v1", "--untracked-files=all")
    print("XC4_WORKTREE=DIRTY_EXPECTED_IMPLEMENTATION" if status else "XC4_WORKTREE=CLEAN")


if __name__ == "__main__":
    main()

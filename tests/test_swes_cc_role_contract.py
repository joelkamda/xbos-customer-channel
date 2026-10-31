"""Non-hosted, source-level qualification of minimal receipt SQL role plan."""
from pathlib import Path
import re

def test_role_plan_is_narrow_and_reversible():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "swes_cc_receipt_role_plan.sql").read_text()
    up, down = script.split("-- DOWN / ROLLBACK --")
    for x in ("NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE", "NOBYPASSRLS",
              "NOREPLICATION", "CONNECT ON DATABASE", "USAGE ON SCHEMA",
              "SELECT, INSERT, UPDATE ON TABLE public.provider_message_receipt"):
        assert x in up
    assert "DROP ROLE" in down
    assert "REVOKE" in down
    assert "GRANT ALL" not in up
    assert "ALTER DEFAULT PRIVILEGES" not in up
    assert "public.provider_message_receipt" in down
    assert "GRANT" not in down
    assert re.search(r"cc_swes_receipt_", up)

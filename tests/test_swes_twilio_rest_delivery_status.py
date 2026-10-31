"""No-network status reconciliation; strictly one approved transport message."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from xbos_customer_channel.transports.swes_twilio_rest_delivery_status import (
    SANDBOX_TRANSPORT_BODY, SWESTwilioStatusNotProven,
    reconcile_sandbox_transport_status,
)

SID = "SM" + "a"*32
ACCOUNT = "AC" + "a"*32
SENDER = "whatsapp:+14155238886"
TESTER = "whatsapp:+237600000001"
START = datetime(2026, 10, 31, 14, 10, tzinfo=timezone.utc)
END = datetime(2026, 10, 31, 14, 25, tzinfo=timezone.utc)

def row(status="delivered", **updates):
    record = {
        "account_sid": ACCOUNT, "from": SENDER, "to": TESTER,
        "body": SANDBOX_TRANSPORT_BODY, "direction": "outbound-api",
        "sid": SID, "status": status,
        "date_created": "Sat, 31 Oct 2026 14:16:00 +0000",
    }
    record.update(updates)
    return record

def reconcile(rows):
    return reconcile_sandbox_transport_status(
        messages=rows, account_sid=ACCOUNT, sender=SENDER,
        recipient=TESTER, created_after_utc=START, created_before_utc=END
    )

@pytest.mark.parametrize(("status", "delivered", "failed"), [
    ("queued", False, False), ("sending", False, False),
    ("sent", False, False), ("delivered", True, False),
    ("read", True, False), ("failed", False, True),
    ("undelivered", False, True),
])
def test_provider_status_not_confused_with_delivery(status, delivered, failed):
    result = reconcile([row(status)])
    assert result.provider_status == status
    assert result.delivery_confirmed == delivered
    assert result.terminal_failure == failed
    assert result.match_count == 1

@pytest.mark.parametrize("bad", [
    {"to":"whatsapp:+237600000099"},
    {"from":"whatsapp:+14150000000"},
    {"account_sid":"AC"+"b"*32},
    {"direction":"inbound"},
    {"body":"Your payment succeeded!"},
    {"date_created":"Sat, 31 Oct 2026 12:00:00 +0000"},
])
def test_mismatched_message_never_accepted(bad):
    with pytest.raises(SWESTwilioStatusNotProven, match="zero_or_multiple"):
        reconcile([row(**bad)])

def test_duplicate_exact_send_is_ambiguous_and_fail_closed():
    with pytest.raises(SWESTwilioStatusNotProven, match="zero_or_multiple"):
        reconcile([row(),row(sid="SM"+"b"*32)])

@pytest.mark.parametrize("bad", [
    {"sid":"invalid"},
    {"date_created":"invalid-date"},
    {"status":"paid"},
    {"date_created":"Sat, 31 Oct 2026 14:16:00"},
])
def test_malformed_matched_result_fails_closed(bad):
    with pytest.raises(SWESTwilioStatusNotProven):
        reconcile([row(**bad)])

def test_mixed_unrelated_records_ignore_but_never_print_sensitive_data():
    result = reconcile([row(to="whatsapp:+237600000099"), row()])
    assert result.delivery_confirmed
    assert not hasattr(result,"sid")
    assert not hasattr(result,"recipient")

def test_large_or_nonbounded_result_set_refused():
    with pytest.raises(SWESTwilioStatusNotProven,match="scope_or_window"):
        reconcile([row()]*31)

def test_bad_query_scope_refused_before_any_match():
    with pytest.raises(SWESTwilioStatusNotProven,match="scope_or_window"):
        reconcile_sandbox_transport_status(
            messages=[row()], account_sid=ACCOUNT, sender="whatsapp:+14150000000",
            recipient=TESTER, created_after_utc=START, created_before_utc=END)

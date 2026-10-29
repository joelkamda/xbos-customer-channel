"""M1 dormant nonsecret synthetic-only readiness builder: offline safety gates."""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.application.msar_readiness import (
    AUDITOR_CAPABILITY, CHECKOUT_PURPOSE, CURRENCY, SCHEMA,
    STAGING_WND_AUDIENCE, DormantSyntheticReadiness,
    CredentialReferenceProof, OwnerReaderProof, ReadinessDenied,
    SyntheticSelector,
)

NOW = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)
SHA = "49d9ba2346ff9a54f70c390851c499f79f85b499"
REF = "wnd-channel-service:isolated-test-only"
NONCE = "ab" * 16
MERCHANT = UUID("10000000-0000-4000-8000-000000000003")
CONTEXT = UUID("10000000-0000-4000-8000-000000000008")
SERVICE = UUID("10000000-0000-4000-8000-000000000010")


def fixture():
    expected = SyntheticSelector(
        tenant_id=910001, merchant_id=MERCHANT, location_id=9,
        branch_id=3, context_id=CONTEXT, service_id=SERVICE,
    )
    owner = OwnerReaderProof(
        owner_authenticated=True,
        approved_capability=AUDITOR_CAPABILITY,
        effective_reader_acl=True, deployed_schema_attested=True,
        release_pinned=True, release_sha=SHA,
        checkout_effective=True, service_active=True,
        context_active=True, purpose=CHECKOUT_PURPOSE,
        audience=STAGING_WND_AUDIENCE, currency=CURRENCY,
        provider_egress_off=True, r5c_off=True, r5h_off=True,
        context_expires_at=NOW + timedelta(minutes=10),
    )
    credential = CredentialReferenceProof(
        reference=REF, reference_bound=True,
        current_present=True, current_matched=True,
        next_present=False, next_matched=False, revoked=False,
        expires_at=NOW + timedelta(minutes=9),
    )
    builder = DormantSyntheticReadiness(
        enabled=True, server_owned_expected=expected,
        expected_release_sha=SHA, expected_credential_reference=REF,
    )
    return builder, expected, owner, credential


def run(builder=None, actual=None, owner=None, credential=None, nonce=NONCE, at=NOW):
    default_builder, default_actual, default_owner, default_credential = fixture()
    return (builder or default_builder).attest(
        actual=actual or default_actual,
        owner=owner or default_owner,
        credential=credential or default_credential,
        at=at, request_nonce=nonce,
    )


def test_default_off_does_not_attest():
    _, actual, owner, credential = fixture()
    denied = DormantSyntheticReadiness(
        server_owned_expected=actual, expected_release_sha=SHA,
        expected_credential_reference=REF,
    )
    with pytest.raises(ReadinessDenied, match="disabled"):
        run(builder=denied, actual=actual, owner=owner, credential=credential)


def test_valid_synthetic_attestation_short_ttl_strict_projection():
    output = run().as_nonsecret_dict()
    assert output["schema"] == SCHEMA
    assert output["tenant_class"] == "isolated_synthetic"
    assert output["slot_status"] == "current"
    assert output["checkout_effective"] is True
    assert output["reader_authorized"] is True
    assert len(UUID(str(output["audit_receipt"])).hex) == 32
    expiry = datetime.fromisoformat(str(output["expires_at"]))
    assert expiry - NOW == timedelta(minutes=5)
    assert set(output) == {
        "schema", "tenant_class", "reader_authorized", "checkout_effective",
        "binding_active", "merchant_location_branch_bound", "credential_ref_bound",
        "slot_status", "service_active", "release_pinned",
        "attested_at", "expires_at", "audit_receipt",
    }
    text = repr(output).lower()
    for sensitive in ("bearer", "token", "digest", "secret", "hash", "dsn",
                      str(MERCHANT).lower(), REF, "phone", "customer"):
        assert sensitive not in text


@pytest.mark.parametrize("field,value", [
    ("owner_authenticated", False),
    ("approved_capability", "restaurant.menu.read"),
    ("effective_reader_acl", False),
    ("deployed_schema_attested", False),
    ("release_pinned", False),
    ("release_sha", "0" * 40),
    ("checkout_effective", False),
    ("service_active", False),
    ("context_active", False),
    ("purpose", "restaurant.menu.read"),
    ("audience", "https://another-service.example"),
    ("currency", "USD"),
    ("provider_egress_off", False),
    ("r5c_off", False),
    ("r5h_off", False),
])
def test_owner_scope_release_and_provider_fail_closed(field, value):
    _, actual, owner, credential = fixture()
    changed = replace(owner, **{field: value})
    with pytest.raises(ReadinessDenied):
        run(actual=actual, owner=changed, credential=credential)


@pytest.mark.parametrize("field,value", [
    ("tenant_id", 2),
    ("tenant_id", 910002),
    ("merchant_id", UUID("10000000-0000-4000-8000-000000000004")),
    ("location_id", 12),
    ("branch_id", 4),
    ("context_id", UUID("10000000-0000-4000-8000-000000000009")),
    ("service_id", UUID("10000000-0000-4000-8000-000000000011")),
])
def test_wrong_tenant_merchant_branch_context_service_denied(field, value):
    _, actual, owner, credential = fixture()
    with pytest.raises(ReadinessDenied):
        run(actual=replace(actual, **{field: value}), owner=owner, credential=credential)


@pytest.mark.parametrize("field,value", [
    ("reference", "wnd-channel-service:other"),
    ("reference_bound", False),
    ("revoked", True),
    ("current_present", False),
    ("current_matched", False),
    ("expires_at", NOW - timedelta(seconds=1)),
])
def test_unknown_reversed_revoked_expired_or_unmatched_slot_denied(field, value):
    _, actual, owner, credential = fixture()
    with pytest.raises(ReadinessDenied):
        run(actual=actual, owner=owner,
            credential=replace(credential, **{field: value}))


def test_next_slot_matches_without_current_and_ttl_is_shortened():
    _, actual, owner, credential = fixture()
    slot = replace(credential, current_present=False, current_matched=False,
                   next_present=True, next_matched=True,
                   expires_at=NOW + timedelta(seconds=45))
    output = run(actual=actual, owner=owner, credential=slot)
    assert output.slot_status == "next"
    assert datetime.fromisoformat(output.expires_at) == NOW + timedelta(seconds=45)


def test_no_slot_matched_denied():
    _, actual, owner, credential = fixture()
    slot = replace(credential, current_matched=False, next_present=True,
                   next_matched=False)
    with pytest.raises(ReadinessDenied):
        run(actual=actual, owner=owner, credential=slot)


def test_expired_context_and_naive_clock_denied():
    _, actual, owner, credential = fixture()
    with pytest.raises(ReadinessDenied):
        run(actual=actual, owner=replace(owner, context_expires_at=NOW),
            credential=credential)
    with pytest.raises(ReadinessDenied):
        run(actual=actual, owner=owner, credential=credential,
            at=NOW.replace(tzinfo=None))


def test_replay_nonce_rejected_and_capacity_deny_by_default():
    builder, actual, owner, credential = fixture()
    assert run(builder, actual, owner, credential).slot_status == "current"
    with pytest.raises(ReadinessDenied, match="replayed"):
        run(builder, actual, owner, credential)
    for i in range(1, 128):
        run(builder, actual, owner, credential, nonce=f"{i:032x}")
    with pytest.raises(ReadinessDenied, match="capacity"):
        run(builder, actual, owner, credential, nonce=f"{129:032x}")


@pytest.mark.parametrize("nonce", ["not_hex", "ab"*15, "AB"*16, "x"*32])
def test_invalid_nonce_denied(nonce):
    with pytest.raises(ReadinessDenied):
        run(nonce=nonce)


def test_untrusted_server_owned_selector_constructor_rejected():
    actual = SyntheticSelector(tenant_id=2, merchant_id=MERCHANT,
        location_id=9, branch_id=3, context_id=CONTEXT, service_id=SERVICE)
    with pytest.raises(ReadinessDenied):
        DormantSyntheticReadiness(enabled=True, server_owned_expected=actual,
            expected_release_sha=SHA, expected_credential_reference=REF)
    _, accepted, _, _ = fixture()
    with pytest.raises(ReadinessDenied):
        DormantSyntheticReadiness(enabled=True, server_owned_expected=accepted,
            expected_release_sha=SHA, expected_credential_reference="menu-reader")


def test_no_runtime_route_startup_or_network_dependency():
    from xbos_customer_channel.runtime.app import app
    assert not any("msar" in route.path for route in app.routes)
    assert "httpx" not in sys.modules[
        "xbos_customer_channel.application.msar_readiness"
    ].__dict__
    import xbos_customer_channel.application.msar_readiness as source
    assert not hasattr(source, "get_db")
    assert not hasattr(source, "read_token")

# Customer Channel CR2 Authoritative Evidence / Confirmation Provenance Security Contract V1

## Status

`CR2_SOURCE_SECURITY_CONTRACT=FORWARD_ONLY`

This contract strengthens Customer Channel orchestration without creating XBOS,
Gateway, Core, provider, fulfillment, pricing, or wallet authority.

## Governing rule

```text
TYPE_CORRECT_OBJECT != TRUSTED_PROVENANCE
CALLER_KNOWS_REFERENCE != AUTHORITY
CORRELATION_REF_MATCH != AUTHORITATIVE_EVIDENCE
```

Caller/transport code SHALL NOT make an `UpstreamStateProjection` authoritative
by constructing or deserializing it. Evidence-gated presentation transitions
MUST resolve evidence server-side through an approved upstream port or a
server-issued opaque provenance handle.

## Upstream evidence contract

```text
CALLER_SUPPLIED_UpstreamStateProjection_IS_AUTHORITY=NO
UpstreamStateProjection=INTERNAL_SERVER_RESOLVED_RESULT_TYPE_ONLY
CALLER_MAY_REFERENCE_EVIDENCE=YES_OPAQUE_LOCATOR_ONLY
CALLER_MAY_SUPPLY_AUTHORITATIVE_FIELDS=NO
SERVER_SIDE_RESOLUTION=REQUIRED
SERVER_SIDE_SESSION_BINDING=REQUIRED
SERVER_SIDE_CONTEXT_BINDING=REQUIRED
```

A server-issued evidence handle MUST be bound to the exact CR1 session/context
fingerprint, including session reference/generation, owner identity reference
(structural only), entry token, tenant semantic, merchant, location, table,
dining area, context binding, and correlation reference.

## Confirmation handle contract

`prepare_confirmation()` MUST fetch an authoritative XBOS confirmation and
register server-owned provenance. It returns a customer-safe commercial
presentation plus a high-entropy Channel-issued opaque handle.

`submit()` MUST accept the opaque handle only. Before XBOS submission it MUST:

1. revalidate the active CR1 session/context;
2. resolve the Channel confirmation provenance record;
3. require exact session + generation + context binding;
4. re-resolve the authoritative XBOS confirmation;
5. compare the immutable commercial fingerprint;
6. atomically claim the handle for `client_submit_ref` in the current
   single-process in-memory contract;
7. submit through `XBOSOrderPort` only after every prior check passes.

The binding includes:

```text
confirmation_handle_ref
authoritative_xbos_confirmation_ref
session_ref
session_generation
owner_identity_ref
entry_token_ref
tenant_ref_or_none
merchant_ref
location_ref
table_ref_or_none
dining_area_ref_or_none
context_binding_ref
correlation_ref
quote_ref
quote_version
service_context_ref
service_mode
immutable_commercial_fingerprint
client_submit_ref_claim
```

The immutable commercial fingerprint covers authoritative material actually
present in the current model: line item references, quantities, options, unit
prices, line totals, delivery fee, taxes/charges, total, currency, and expiry.
No absent upstream fields are invented.

## Session rotation

```text
CONFIRMATION_BOUND_TO=EXACT_SESSION_REF_PLUS_SESSION_GENERATION
VALID_HANDLE_AFTER_SESSION_ROTATION=DENY_STALE_HANDLE
SAFE_REENTRY=REPREPARE_CONFIRMATION_ON_ACTIVE_NEW_SESSION_GENERATION
PREDECESSOR_SESSION_HANDLE=NOT_AUTHORITY
```

## Fake contract parity

Fake fixtures SHALL enforce the same source-level trust contract expected of
future real adapters:

- state reconciliation is bound to intended session/context/correlation;
- unknown or never-issued XBOS confirmation references are rejected;
- Channel confirmation handles are server-issued and resolved server-side;
- same handle + same `client_submit_ref` is idempotent;
- same handle + different `client_submit_ref` conflicts;
- changed authoritative confirmation material requires denial/reconfirmation.

This does NOT close production fake containment:

```text
CHANNEL_A0_005=OPEN_PENDING_CR4
CR2_PRODUCTION_FAKE_CONTAINMENT=NO
CR4_PRODUCTION_FAKE_CONTAINMENT_REMAINS_OPEN=YES
```

## Persistence qualification

```text
CR2_SCHEMA_CHANGE=NONE
CR2_MIGRATION=NONE
CR2_DEPENDENCY_CHANGE=NONE
CR2_CURRENT_PROVENANCE_STORE=IN_MEMORY_SECURITY_CONTRACT_STORE
PROCESS_RESTART_DURABILITY=NOT_PROVEN
MULTI_PROCESS_HANDLE_ATOMICITY=NOT_PROVEN
DISTRIBUTED_HANDLE_ATOMICITY=NOT_PROVEN
REAL_DURABLE_PROVENANCE=BLOCKED_FUTURE_REALISTIC_COMPOSITION_GATE
```

Loss of the in-memory store makes handles unknown and fails closed. This is
source-remediation proof, not production durability proof.

## CR1 qualifications preserved

```text
CR1_CONTEXT_ATTESTATION=PASS_REQUIRED
CR1_SESSION_OWNER_BINDING=PASS_STRUCTURAL_REQUIRED
CR1_SESSION_CONTEXT_IMMUTABLE=PASS_REQUIRED
CR1_SESSION_EXPIRY=PASS_REQUIRED
CR1_SESSION_ROTATION=PASS_REQUIRED
CR1_SESSION_FIXATION_DEFENSE=PASS_REQUIRED
CR1_SINGLE_USE_ATOMIC_CONSUME=PASS_IN_MEMORY_CONTRACT_REQUIRED
CR1_IDENTITY_PROVENANCE=BLOCKED_PENDING_CR3
CR1_TENANT_REAL_CONTRACT=BLOCKED_PENDING_XBOS_SEAM
CR1_REAL_DURABLE_ATOMICITY=BLOCKED_FUTURE_COMPOSED_GATE
```

## Finding discipline

Successful CR2 source implementation yields only:

```text
CHANNEL_A0_003=SOURCE_REMEDIATION_IMPLEMENTED_NOT_CLOSED
CHANNEL_A0_004=SOURCE_REMEDIATION_IMPLEMENTED_NOT_CLOSED
```

Final closure requires realistic XBOS composition and defensive negative retest.

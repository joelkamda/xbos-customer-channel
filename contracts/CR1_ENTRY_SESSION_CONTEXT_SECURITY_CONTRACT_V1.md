# CR1 Entry / Session / Context / Single-Use Security Contract V1

Status: implementation candidate; freeze not authorized.
Parent succession head: `faab016a4b76221ef3f99f35d386aeb76900dcf1`.
Immutable XC8 domain baseline: `76b5dff4a37426bcebb6919e6c58b479a3afc635`.

## Authority boundary

Customer Channel owns transport/session/presentation/orchestration only. XBOS owns merchant,
location, table/resource membership and context truth. This contract creates no order, pricing,
payment, provider, Core monetary, Wallet balance, or second merchant authority.

## Context attestation

`XBOSContextPort.attest_context(...)` returns the only server-side `EntryContextAttestation`
accepted by CR1 entry/session security. A caller-constructed attestation is not authority.

The attested chain contains:

- `tenant_ref` or `None`;
- `merchant_ref`;
- `location_ref`;
- `table_ref` or `None`;
- `dining_area_ref` or `None`;
- `purpose`;
- opaque XBOS-owned `context_binding_ref`;
- customer-safe merchant projection.

At issue and resolution, Customer Channel compares the entire chain and never invents the binding.
Real tenant isolation remains blocked until the real XBOS customer-safe context seam attests tenant
scope. Fake multi-tenant denial tests are fixture proof only.

## Single-use semantics

Single-use entry resolution is:

1. verify signed token;
2. load stored channel entry record;
3. re-attest current XBOS context;
4. compare exact bound chain and `context_binding_ref`;
5. atomically `consume_if_unconsumed`;
6. return resolved context.

The current in-memory store uses one process lock around compare-and-set. This proves only
single-process fixture atomicity. Durable/distributed atomicity remains a future composed gate.

## Session binding

Secure sessions bind immutably to:

- internal resolved `owner_identity_ref` (provenance remains open under A0-007 / CR3);
- entry token reference;
- tenant/merchant/location/table/dining-area context;
- entry purpose;
- `context_binding_ref`;
- correlation reference;
- creation/expiry epochs;
- generation.

Actual session refs are CSPRNG server-issued values. A historical caller-supplied fixture value may
exist only as a non-resumable compatibility alias and is never the actual session ref.

Successful secure resume requires owner match, non-expiry, active predecessor, current XBOS
re-attestation, exact bound context match, and atomic predecessor invalidation plus rotation.

## Explicit qualifications

- `CR1_REAL_TENANT_ISOLATION=BLOCKED_PENDING_REAL_XBOS_TENANT_SCOPE_ATTESTATION`.
- `CR1_IDENTITY_PROVENANCE=BLOCKED_PENDING_CR3`.
- `CR1_REAL_DURABLE_ATOMICITY=BLOCKED_FUTURE_COMPOSED_GATE`.
- CR1 does not close A0-001/A0-002/A0-006 merely by source implementation.

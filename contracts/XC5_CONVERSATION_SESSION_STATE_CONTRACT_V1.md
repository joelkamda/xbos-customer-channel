# XC5_CONVERSATION_SESSION_STATE_CONTRACT_V1

## Purpose

Freeze the Customer Channel conversation/session orchestration contract without creating a competing order, payment, fulfillment, provider, or financial state authority.

## Authority separation

```text
CHANNEL_STATE=INTERACTION_ORCHESTRATION_ONLY
XBOS_ORDER_STATE=AUTHORITATIVE
XBOS_PAYMENT_COMMERCIAL_STATE=AUTHORITATIVE
CORE_FINANCIAL_STATE=AUTHORITATIVE_IN_CORE
GATEWAY_PROVIDER_EXECUTION_STATE=AUTHORITATIVE_IN_GATEWAY
```

Labels such as `ORDER_CREATED`, `PAYMENT_PENDING`, `PAID`, `FULFILLMENT`, and `COMPLETED` are Channel projections only. Evidence-gated labels require typed upstream evidence and do not manufacture the underlying fact.

## State set

```text
START
MERCHANT_CONTEXT
BROWSING
CART
SERVICE_MODE
CUSTOMER_DETAILS
REVIEW
ORDER_SUBMITTING
ORDER_CREATED
PAYMENT_METHOD
PAYMENT_PENDING
PAID
FULFILLMENT
COMPLETED
HUMAN_HANDOFF
CANCELED
```

## Deterministic transition rules

Transitions are defined in a closed matrix in `CustomerSessionService.TRANSITIONS`. Invalid transitions fail without mutation. Transition retries are idempotent by `(session_ref, idempotency_key)` and cannot create business truth because XC5 invokes no order or payment command.

## Evidence-gated projection states

The following Channel states require compatible typed upstream evidence when entered through the state machine:

```text
ORDER_CREATED
PAYMENT_PENDING
PAID
FULFILLMENT
COMPLETED
```

Correlation, order reference, and payment reference mismatches fail closed.

## Re-entry/recovery

Stable Channel references are:

```text
session_ref
conversation_ref
correlation_ref
entry_token_ref
cart_ref
quote_ref
order_ref (correlation only)
payment_ref (correlation only)
```

When local state depends on order/payment/fulfillment facts, re-entry MUST reconcile through `XBOSStateReconciliationPort`. A stale local snapshot cannot assert current business/payment truth. The real adapter remains blocked until the customer-safe XBOS IA0/F facade is frozen; deterministic fake/typed fixtures are development evidence only.

## Material ambiguity

For material ambiguity involving quantity, delivery address, high-value change, payment, or cancellation, multiple candidate actions require explicit confirmation. The Channel fails to clarification rather than guessing or silently mutating confirmed facts.

## Persistence boundary

XC5 may persist Channel session state, idempotency results, correlation references, handoff references, and last evidence references. It MUST NOT persist a replacement authoritative order/payment ledger or financial/provider state store.

## Preserved parents

- `XC3_ENTRY_CONTEXT_CONTRACT_V1`
- `XC4_CATALOG_QUOTE_CONTRACT_V1`

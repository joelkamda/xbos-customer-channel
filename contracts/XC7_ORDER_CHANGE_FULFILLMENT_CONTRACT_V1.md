# XC7_ORDER_CHANGE_FULFILLMENT_CONTRACT_V1

## Authority
```text
CHANNEL_ROLE=REQUEST_PRESENT_CONFIRM
XBOS_ORDER_STATE=AUTHORITATIVE
XBOS_CHANGE_POLICY=AUTHORITATIVE
XBOS_FULFILLMENT_STATE=AUTHORITATIVE
CHANNEL_FINANCIAL_CORRECTION=FORBIDDEN
```

Customer Channel may submit a confirmed change or cancellation intent and present XBOS decisions.
It does not decide mutability, cancellation acceptance, refund/reversal consequence, or fulfillment progression.

## Cancellation cases
```text
CANCEL_BEFORE_PAYMENT
CANCEL_AFTER_PAYMENT
CANCEL_AFTER_PREPARATION_BEGAN
MERCHANT_INITIATED_CANCELLATION
```

A post-payment cancellation may project `financial_correction_required=true`; this is evidence that another
authorized financial/correction path is required. XC7 does not perform that correction.

## Fulfillment projection
```text
KITCHEN_BAR_TICKET
PREPARATION
READY
SERVED
PICKED_UP
DISPATCHED
DELIVERED
```

These values are authoritative XBOS projections. Channel code cannot advance them.

## Re-entry
Re-entry queries the typed lifecycle boundary by stable `order_ref` + `correlation_ref`.
Local Channel session state is never sufficient to assert current canonical order or fulfillment truth.

## Adapter state
```text
REAL_XBOS_ORDER_CHANGE_FULFILLMENT_ADAPTER=BLOCKED
FAKE_OR_TYPED_CONTRACT_ADAPTER=AUTHORIZED
```

## Explicitly deferred
No refunds, reversals, financial corrections, payment requests, Wallet/Gateway/Core/provider calls, or XC8 work.

# XC8_PAYMENT_REQUEST_ABSTRACTION_CONTRACT_V1

## Authority
```text
CANONICAL_ORDER_AUTHORITY=XBOS
CANONICAL_AMOUNT_AUTHORITY=XBOS
PAYMENT_METHOD_POLICY=XBOS
CHANNEL_PROVIDER_ROUTING_AUTHORITY=NO
```

Customer Channel requests a typed XBOS payment projection for a canonical order and presents only methods
permitted by XBOS/merchant policy. It does not calculate the payable amount or select provider routing.

## Method presentation
```text
XAFPAY_WALLET
MTN_MOBILE_MONEY
ORANGE_MONEY
CARD
PAY_AT_COUNTER
```
`PAY_AT_COUNTER` appears only when the authoritative policy permits it.

## Unified nextAction
```text
NONE
OPEN_URL
DISPLAY_QR
AWAIT_PUSH
OPEN_WALLET
WAIT
```
A `nextAction` is navigation/presentation only and is never payment-success evidence.

## Fake payment fixture
```text
PENDING
SUCCEEDED
FAILED
EXPIRED
REVERSED
```

Mandatory semantics:
```text
FAKE_PAYMENT_RESULT=TEST_FIXTURE_ONLY
FINANCIAL_SYSTEM_MUTATION=NO
XBOS_CANONICAL_PAYMENT_MUTATION=NO
WALLET_MUTATION=NO
GATEWAY_MUTATION=NO
CORE_MUTATION=NO
PROVIDER_CALL=NO
```

Fake `SUCCEEDED` is UX/E2E fixture state only. It cannot mutate the customer session into authoritative paid state,
cannot create a receipt, and cannot represent production truth.

## Real adapter state
```text
REAL_XBOS_PAYMENT_REQUEST_ADAPTER=BLOCKED
FAKE_OR_TYPED_XBOS_PAYMENT_REQUEST_ADAPTER=AUTHORIZED
```

## Deferred
No real Wallet/Gateway/Core/provider integration, provider SDK/webhook/session/routing logic, Channel payment ledger,
or XC9 work is authorized in XC8.

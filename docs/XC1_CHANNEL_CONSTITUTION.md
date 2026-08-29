# XC1 Channel Constitution

## Mission

Create a transport-agnostic customer-channel core that executes customer journeys through explicit application ports without recreating XBOS or XafPay authority.

## Authority

The Customer Channel may own:
- transport;
- conversation/session orchestration;
- customer web/QR shell;
- presentation state;
- delivery jobs;
- correlation references;
- human-handoff state.

It may not own:
- menu price truth;
- order truth;
- inventory truth;
- payment truth;
- financial balance;
- merchant receipt truth;
- provider execution.

## Hard prohibitions

- NO direct Core calls.
- NO direct provider calls from application core.
- NO direct XBOS database access.
- NO private XBOS implementation imports as integration.
- NO authoritative order/payment ledger in this repository.
- NO WhatsApp-provider lock-in in application core.
- NO production credentials in repository.

## Dependency posture

`XBOS_CUSTOMER_SAFE_INTERACTION_FACADE` is owned by XBOS IA0/F and is `REQUIRED_NOT_YET_FROZEN`. It blocks the real XBOS channel adapter but does not block XC1 fake-adapter foundation.

Real Wallet/Gateway payment adapters remain blocked until their corresponding frozen contracts/program gates authorize them.

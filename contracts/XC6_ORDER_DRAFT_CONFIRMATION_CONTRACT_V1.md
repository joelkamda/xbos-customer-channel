# XC6_ORDER_DRAFT_CONFIRMATION_CONTRACT_V1

## Authority

- `SERVICE_MODE_SEMANTICS=XBOS_RESTAURANT_PACK`
- `CHANNEL_ROLE=CAPTURE_PRESENT_CONFIRM_SUBMIT`
- `CANONICAL_ORDER_AUTHORITY=XBOS`
- Customer Channel owns only interaction/orchestration state and stable submission correlation.
- XC4 authoritative quote safeguards remain mandatory.
- Payment request creation is outside XC6.

## Service modes

Initial restaurant proof supports:

- `DINE_IN`
- `TAKEAWAY`
- `DELIVERY`

Dine-in requires a valid XC3 table context matching merchant/location.
Takeaway contact/pickup semantics come from the XBOS order boundary.
Delivery availability, service area and delivery fee are XBOS commercial semantics.

## Authoritative confirmation snapshot

Before canonical order submission the Channel presents an XBOS-supplied snapshot containing:

- items, quantities and modifiers from the accepted quote;
- service mode;
- delivery fee where applicable;
- taxes/charges where supplied;
- total and currency;
- merchant/location;
- quote and version references.

The Channel does not calculate authoritative delivery fee, tax, charges or final total.

## Stale quote rule

`DO_NOT_SUBMIT_STALE_QUOTE`

A material price/availability change must be shown and the latest authoritative quote explicitly acknowledged before confirmation.

## Idempotent canonical submission

- stable `client_submit_ref` is required;
- same submit reference + same confirmation => one canonical XBOS order effect;
- same submit reference + altered confirmation => conflict/rejection;
- lost response => query/reconcile by stable client reference;
- timeout/transport return alone never proves success.

## Adapter state

`REAL_XBOS_ORDER_ADAPTER=BLOCKED`

Until the customer-safe IA0/F facade freezes, only fake/typed contract adapters are authorized.

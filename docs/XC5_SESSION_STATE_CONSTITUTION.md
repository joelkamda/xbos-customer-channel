# XC5 Session State Constitution

1. Customer Channel state is interaction orchestration only.
2. XBOS remains authoritative for order, commercial payment and fulfillment state.
3. Core remains authoritative for financial state; Gateway remains authoritative for provider execution state.
4. Evidence-gated Channel labels never create the business or financial fact they display.
5. Invalid transitions fail without silent mutation.
6. Transition retries are idempotent Channel operations and cannot duplicate business truth.
7. Re-entry uses stable session/conversation/correlation references and reconciles upstream-dependent state.
8. A local session snapshot is insufficient to assert current order/payment truth after re-entry.
9. Material ambiguity affecting quantity, delivery address, high-value change, payment or cancellation requires explicit confirmation when multiple actions remain possible.
10. XC5 creates no canonical order, payment request, fulfillment transition, provider success or financial success.
11. Real XBOS state reconciliation remains blocked until the customer-safe IA0/F facade freezes.
12. XC6 is outside this tranche.

# XC8 PAYMENT EXPERIENCE CONSTITUTION

1. XBOS owns canonical order, payable amount, currency and permitted payment-method policy.
2. Customer Channel presents approved methods and product-neutral nextAction instructions only.
3. A redirect, QR display, push wait, wallet-open action or browser return is never evidence of payment success.
4. Fake outcomes are deterministic UX/E2E fixtures only and mutate no financial or canonical XBOS state.
5. No Channel payment ledger is introduced.
6. No provider SDK, provider webhook, provider session creation or provider routing engine is introduced.
7. The real XBOS payment-request adapter remains blocked until the customer-safe IA0/F facade is frozen.
8. XC8 performs no real Wallet, Gateway, Core or provider call and does not start XC9.

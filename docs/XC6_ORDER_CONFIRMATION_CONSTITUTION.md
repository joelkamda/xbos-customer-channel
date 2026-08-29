# XC6 Order Draft / Service Mode / Confirmation Constitution

1. Customer Channel captures, presents, confirms and submits; XBOS owns service-mode policy and canonical order state.
2. Dine-in table context must originate from valid XC3 entry context and match merchant/location.
3. Takeaway and delivery context are resolved through the typed XBOS order boundary.
4. Delivery fee, service area, tax/charges and final commercial total are never Channel arithmetic.
5. XC4 quote freshness and availability controls remain mandatory immediately before confirmation.
6. A stable client submission reference is required for idempotent order creation and lost-response recovery.
7. Transport timeout is an unknown outcome, not success.
8. The Channel persists no second order master and creates no payment request in XC6.
9. Real XBOS order networking remains blocked until the customer-safe IA0/F facade is accepted.
10. XC7 must not begin in this tranche.

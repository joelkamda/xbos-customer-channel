# XC7 ORDER CHANGE / CANCELLATION / FULFILLMENT CONSTITUTION

1. Channel requests and presents; XBOS decides canonical change/cancellation policy.
2. Cancellation cases remain distinct: before payment, after payment, after preparation began, merchant initiated.
3. `financial_correction_required` is a projection only. XC7 performs no refund, reversal, or financial correction.
4. Fulfillment stages are XBOS-owned facts and are display/communication projections in Channel.
5. Re-entry reconciles by stable canonical order + correlation references; local Channel state is not business truth.
6. The real XBOS lifecycle adapter remains blocked until the customer-safe IA0/F facade is frozen.
7. XC7 creates no payment request and does not start XC8.

# XC3 Merchant / Location / Table / QR Entry Constitution

1. Customer Channel is not merchant, location or table authority.
2. There are no hard-coded WND/tenant/location/table identifiers in the XC3 live source.
3. Canonical merchant context is projected through `XBOSContextPort`.
4. Real XBOS context integration remains blocked pending the frozen customer-safe IA0/F facade.
5. Public QR/deep-link tokens expose no internal database IDs.
6. Public tokens contain no price, payment credential, provider credential or Core financial data.
7. Public entry references are opaque, signed, versioned and expiring.
8. Table-entry replay policy is explicit: reusable where safe, single-use where required.
9. Tampering, expiry, unknown token, cross-tenant substitution and unsafe replay fail closed.
10. Rejection never falls back to an arbitrary merchant, location or table.
11. WHATSAPP and CUSTOMER_WEB can transport the same logical entry context.
12. Callers cannot supply arbitrary redirect destinations.
13. Entry-token persistence is channel infrastructure only and cannot become a second merchant master.
14. XC3 creates no payment, order or financial truth.
15. XC3 does not begin XC4.

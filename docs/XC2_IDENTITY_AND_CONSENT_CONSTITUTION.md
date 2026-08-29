# XC2 — Channel Identity, Customer/Party Resolution and Consent Constitution

## Frozen parent

- XC1 HEAD: `dbf0d7c12eb906ede001227a810c317e18f3b549`
- XC1 TAG: `xbos-customer-channel-xc1-constitution-foundation-20260829`
- Program authorization: `xafpay-program-wp15-pg5-xc1-concurrent-sync-20260829`

## Identity states

`ANONYMOUS -> RECOGNIZED -> VERIFIED -> LINKED`

`BLOCKED_OR_RESTRICTED` may interrupt progression.

## Laws

1. A channel address or WhatsApp/phone identifier is a channel identifier, not universal/legal identity.
2. A lookup hit may produce `RECOGNIZED`; it never proves `VERIFIED`.
3. Verification evidence is required before an XBOS Party reference may be linked.
4. Verification does not create consent.
5. Party/customer truth remains XBOS authority. The channel stores only opaque references and channel metadata.
6. The fake identity adapter is a deterministic contract fixture, never a second Party/customer master.
7. Real XBOS identity integration remains blocked until the customer-safe IA0/F facade is frozen.
8. Consent is purpose-specific and independently recorded for transactional messages, marketing, support, order updates and receipt delivery.
9. Persistent channel PII must be minimized. Raw phone/address values are not part of the durable identity record.
10. Logs/observability must not unnecessarily contain message bodies, phone numbers, contact addresses, names, email addresses or credentials.
11. No direct XBOS DB/private-source access, Core call or provider call is authorized by XC2.
12. XC2 does not begin XC3 merchant/location/table/QR implementation.

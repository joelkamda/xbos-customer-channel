# XC3 Entry Context Contract v1

Contract identity: `XC3_ENTRY_CONTEXT_CONTRACT_V1`

## Authority

Customer Channel owns entry-token/session infrastructure and transport-neutral launch semantics.
XBOS owns canonical merchant, location, service availability, branding/terminology, currency,
fulfillment availability and table/resource business semantics.

The real XBOS context adapter remains blocked until the customer-safe IA0/F facade is frozen.

## Public token

The public token carries only:

- XC3 token version;
- opaque random `entry_token_ref`;
- expiry;
- random nonce;
- HMAC signature.

The public token does not carry merchant/location/table identifiers, internal database IDs, catalog
prices, payment credentials, provider credentials, Core financial data or redirect destinations.

## Server-side channel entry record

An entry record may reference opaque:

- merchant ref;
- location ref;
- dining-area/table ref;
- entry purpose;
- expiry/version;
- replay policy.

This record is channel entry/session infrastructure. It is not a merchant/location/table master.

## Resolution

Token verification precedes record lookup. Canonical context projection then comes through
`XBOSContextPort`. Rejected, expired, tampered, unknown, cross-tenant and unsafe-replay inputs fail
closed and never fall back to another merchant/location/table.

## Channel-neutral launch

The same public entry token can be transported through WHATSAPP or CUSTOMER_WEB. Transport URL
shape may differ; the resolved commercial context cannot.

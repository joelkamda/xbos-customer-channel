# XC4_CATALOG_QUOTE_CONTRACT_V1

Status: XC4 typed/fake development contract only. Real XBOS adapter remains blocked until the customer-safe IA0/F facade is frozen.

## Authority

XBOS remains authoritative for catalog, item availability, currency, tenant terminology, commercial price, tax/fee/discount consequence, inventory consequence and quote/snapshot truth.

Customer Channel may project those values for customer interaction. Projection never becomes a second catalog or pricing master.

Menu-of-the-day is an XBOS catalog/availability projection, not a Channel-owned menu table.

## Interaction cart

The Channel cart stores only interaction intent:

- item reference;
- quantity;
- selected option/modifier references.

It does not store authoritative unit price, total, tax, fee, discount, inventory, availability, order acceptance or payment amount.

## Quote boundary

Before confirmation, Channel calls the typed XBOS catalog/quote port and receives a versioned commercial quote/snapshot containing the latest amount/currency and line availability.

Development fixtures are deterministic simulations of that future contract and are not production commercial truth.

## Stale state

If displayed price or availability differs from the authoritative quote/snapshot:

- do not silently submit the old amount;
- show the change;
- require explicit acknowledgement/reconfirmation when continuation remains possible;
- block continuation when the authoritative snapshot says the selection is unavailable.

XC4 does not create an order, payment request or financial effect.

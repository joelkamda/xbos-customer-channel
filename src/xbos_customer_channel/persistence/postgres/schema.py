from __future__ import annotations

STATE_SCHEMA_VERSION = 1

ACCEPTED_TABLES: tuple[str, ...] = (
    "channel_conversation",
    "channel_session",
    "w1_runtime_state",
    "channel_identity_binding",
    "provider_message_receipt",
    "idempotency_result",
    "provenance_handle",
    "transport_delivery",
    "entry_token_claim",
)

LOCATOR_HMAC_CURRENT_KEY_ENV = "XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_KEY_CURRENT"
LOCATOR_HMAC_PREVIOUS_KEY_ENV = "XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_KEY_PREVIOUS"
LOCATOR_HMAC_CURRENT_VERSION_ENV = "XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_VERSION_CURRENT"
LOCATOR_HMAC_PREVIOUS_VERSION_ENV = "XAFPAY_CUSTOMER_CHANNEL_LOCATOR_HMAC_VERSION_PREVIOUS"
DATABASE_URL_ENV = "XAFPAY_CUSTOMER_CHANNEL_DATABASE_URL"

PROHIBITED_AUTHORITY_TERMS: tuple[str, ...] = (
    "settlement",
    "accounting",
    "treasury",
    "merchant_ledger",
    "provider_account",
)

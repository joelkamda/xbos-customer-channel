# R48 A1 — CC → WND authenticated signed-Twilio event contract v1

Date: 2026-11-02. Authority: existing CEO R48 Track A. Proposed implementation contract **CC PRODUCER READY; WND RECEIVER ACCEPTANCE AND HOSTED ADMISSION STILL REQUIRED**. Source and prior genuine signed ingress evidence back the CC fields; this document is NOT an assertion that the WND writer or hosted event delivery already exists.

## Exact transport and trust boundary

- Trigger: a **new real** Twilio WhatsApp Sandbox POST to the configured canonical HTTPS CC webhook. Validate Twilio's HMAC signature over the original form and the configured URL; same approved Twilio account, enrolled sender, Sandbox recipient, media=0, active admission. CC may not construct this event from a stored receipt, message text, environment identity flag, or user-provided fake JSON.
- Require **one first accepted signed claim** and subsequently committed restricted hosted PostgreSQL receipt. The new CC producer method `HostedTwilioReceiptLedger.read_processed_first` confirms same provider hash and full signed-form digest, `processing_state='processed'`, `processing_attempt=1`, `row_version>=2`, and recorded server time. No event from duplicate/collision/failed receipt.
- Delivery: **proposed** `POST https://wnd-xafpay-demo-austin-backend.onrender.com/internal/swes/track-a/test-only/ia0-events`. This path is NOT YET DEPLOYED. Use HTTPS exact approved staging host, no redirects, a **WND-issued, source-independently verified** short-lived service bearer in `Authorization: Bearer <secret>`, audience `wnd-austin-track-a-ia0`, exact tenant 910001, merchant `4b196022-55a1-548c-b122-f1f9e4b78bea`, location 5, ingest privilege registered and resolved from real WND permission/role tables, fixed CC service principal, expiry and revocation. No bearer reuse from Tranzak, payment execution, or Twilio auth token.
- Headers: `X-Correlation-Ref`, `X-CC-Event-Id`, `X-Idempotency-Key` (same event ID), `X-CC-Event-Schema: xafpay.cc.signed_twilio_ia0_ingress.v1`, `Content-Type: application/json`. **All header/body claims are UNTRUSTED until WND independently verifies its bearer, audience, tenant, merchant, location, role and purpose.** WND must not infer a service identity from headers or booleans.
- The provider message ID is private provenance, never an authentication bearer; CC never writes raw tester phone, message body or Twilio secret to evidence or logs. WND must redact private provider IDs and sender references from application logs. No payload field can declare paid status, price, merchant operational status or customer authenticated-external status.

## Exact JSON body emitted by CC producer (source-owned values, NOT a fake example)

`schema`: `xafpay.cc.signed_twilio_ia0_ingress.v1` (literal).
`tenant_id`: integer `910001` and `merchant_public_id`: UUID string `4b196022-55a1-548c-b122-f1f9e4b78bea` (fixed staging scope).
`audience`: `wnd-austin-track-a-ia0`; `context_purpose`: `restaurant.customer_channel.checkout`; `channel`: `twilio_whatsapp`.
`event_id`: SHA-256 hex of fixed version prefix + validated receipt key; deterministic per genuinely signed provider message.
`correlation_ref`: UUIDv5 from a fixed CC namespace and the same validated receipt key, repeat-stable.
`provider_message_id`: **exact** `MessageSid` from signature-verified Twilio POST; private and redacted in reports.
`provider_account_ref`: exact signed `AccountSid`; `provider_endpoint_ref`: canonical normalized Sandbox recipient (digits, without `whatsapp:`).
`sender_reference`: `{scheme: "cc_locator_hmac_sha256_v1", version: <CC approved HMAC key version>, value: <HMAC-SHA256 of canonical channel/provider endpoint/real WhatsApp sender>}`. No raw phone; sender digest derives from CC's runtime `LocatorKeyRing`, NOT plain SHA-256 of a phone.
`server_received_at_utc`: actual CC host UTC receipt time, explicitly **not Twilio send time**. `delivered_before_utc`: this time + 5 minutes; WND must refuse new writes after expiry.
`receipt`: `{authority: "xafpay.cc.hosted_pg17_signed_twilio_receipt", reference: <exact SHA-256 hashed provider-message receipt key>, payload_sha256: <exact canonical Twilio signed-form digest>, processing_state: "processed", processing_attempt: 1}` independently read from restricted CC PostgreSQL (not a caller's boolean).
`verification`: `{source: "twilio_signed_webhook_verified_by_cc", provider_signature_verified: true, sender_enrolled_test_only: true, identity_resolution_state: "channel_identified", verification_state: "channel_verified", authentication_authority: null, authentication_reference: null, timestamp_source: "cc_server_received"}`.

## WND canonical IA0 application contract (receiver ownership)

Use `core.platform.interaction.sql_repository.SQLInteractionRepository` under one protected TENANT-910001-only authenticated staging transaction, not raw inserts. Exact source method mapping:

1. `create_external_identity` using stable opaque `sender_reference.value`, channel `twilio_whatsapp`, source `xafpay.customer_channel.signed_twilio`, `resolution_state=channel_identified`; no PC2 party linkage guessed.
2. `insert_conversation(Conversation)` using a **WND-issued** real conversation UUID, tenant 910001, opened time/correlation from event. Reuse the existing linked active conversation if properly proven; never invent a customer/session UUID as a claim in the CC event.
3. `insert_participant(ConversationParticipant)` of kind `external_channel_identity`, source-authority reference to the genuinely persisted external identity, same conversation+tenant.
4. `insert_session(InteractionSession)` using WND-issued real session UUID, linked conversation+participant, `identity_resolution_state=channel_identified`, **`verification_state=channel_verified`** and `authentication_reference=None`. Signed provider ingress is channel verification, **NOT customer login/PC5 authenticated_external**. If WND policy requires stronger login, fail closed until independent authenticated evidence exists.
5. `insert_message(InteractionMessage)` and/or `insert_event(InteractionEvent)` with accepted CC event/receipt source reference, provider idempotency and WND-issued correlation, if and only if same event was genuinely delivered/authenticated.
6. WND alone may later `insert_context_binding(InteractionContextBinding)` with `purpose_code=restaurant.customer_channel.checkout`, exact real `interaction_session_public_id`, source authority `xafpay.customer_channel.signed_twilio`/reference `event_id`, provenance authority `xafpay.cc.hosted_pg17_signed_twilio_receipt`/reference `receipt.reference`, actual effective/expiry timestamps. Revalidate location 5, role, authorization and merchant registered TEST-only exception independently.

Atomic/idempotent result: WND rejects old65a1, tenant2, untrusted audience/scope/bearer, expired event, wrong sender mapping, forged verification, altered digest, conflicting duplicate; uses real `ia0_commands`/unique inbound transport event reference and locks so identical retries resolve the **same** conversation/participant/session result, not a second write. Same event ID with different fingerprint must fail. Delayed delivery after expiry must never create a new session. No optional bypass based on an env flag or `private_service_identity_qualified` Boolean.

**Proposed response (WND to implement and accept):** authenticated 202 on first committed write, 200 on a replay of the same committed event with the same identity; both return bounded JSON with received `event_id`, `correlation_ref`, `disposition` (`accepted` or `duplicate`), real source-owned `ia0_conversation_public_id`, `ia0_participant_public_id`, `ia0_session_public_id`, `verification_state=channel_verified` and expiry. No raw phone, secret or payment status.

## Deployment and evidence

- CC source: `src/xbos_customer_channel/transports/swes_r48_verified_event.py`; restricted receipt readback in `src/xbos_customer_channel/persistence/postgres/hosted_twilio_receipt.py`; tests `tests/test_swes_r48_verified_event_contract.py`.
- Existing real proof: `46_SWES_R30_CC_REAL_TWILIO_ORIGIN_INBOUND_DURABLE_RECEIPT_HOSTED_PASS_R0.md`, SHA-256 `93f8a05e66d52aab848b483c5584c378590802b90b6d0cb9bbd9f22044bf669c` (actual provider-origin message -> restricted hosted PG processed receipt); previous genuine Twilio HTTPS signature and tested operator-signed host duplicate suppression. Not an IA0 session proof.
- Actual live CC source was `ba95c4ea277efac1c42bbf3bc14fe006e8d8eceb` and WND `72d4cbd23454db2aa7387da989043d7ce38835a4`; neither contains these new event producer/receiver paths. Sandbox tester expiration last safely set `2026-11-04T02:30:00Z`.
- No WND bearer, service role, endpoint or IA0 session write has been admitted by this document. WND must integrate/accept receiver contract and prove live restricted service scope and idempotency. CC must then deploy the real event adapter and process ONE newly consented real signed WhatsApp message; independent database readback is the acceptance proof. No forged replay or raw SQL session/order creation.

**CC producer offer: SOURCE-TESTED, NOT HOSTED. Bilateral receiver agreement: PENDING WND engineering acceptance; do not label bilateral PASS before WND validates, implements and returns its native acceptance.**

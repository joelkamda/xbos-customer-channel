# CUSTOMER CHANNEL DECISION HISTORY

FORMAT=APPEND_ONLY_CONSEQUENTIAL_DECISIONS_ONLY
LANE=CUSTOMER_CHANNEL

DECISION_ID=CHANNEL-GOV-001
DATE=2026-09-05
LANE=CUSTOMER_CHANNEL
TRANCHE=SUCCESSION_GOVERNANCE
QUESTION=What is the permanent Customer Channel domain-authority boundary?
DECISION=Customer Channel owns SESSION_CONTEXT, CUSTOMER_ENTRY_ORCHESTRATION, CUSTOMER_PRESENTATION, and CHANNEL_TRANSPORT. It does not own MERCHANT_ORDER_TRUTH, XBOS_CATALOG_TRUTH, XBOS_PRICING_TRUTH, XBOS_FULFILLMENT_TRUTH, PROVIDER_EXECUTION_TRUTH, PROVIDER_EVIDENCE_TRUTH, CORE_MONETARY_TRUTH, CORE_POSTING_AUTHORITY, or WALLET_BALANCE_TRUTH. CHANNEL_ORCHESTRATION != DOMAIN_AUTHORITY.
WHY=Customer-facing orchestration must not create a second merchant, provider, payment, or financial authority.
SUPERSEDES=NONE
AFFECTS=all_customer_channel_tranches;CHANNEL_START_HERE.md;governance/handoff/LIVING_HANDOFF.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-002
DATE=2026-09-05
LANE=CUSTOMER_CHANNEL
TRANCHE=SUCCESSION_GOVERNANCE
QUESTION=Which immutable repository state is the foundation parent for Customer Channel succession governance?
DECISION=Ratify 76b5dff4a37426bcebb6919e6c58b479a3afc635, tagged xbos-customer-channel-xc8-payment-request-fake-adapter-20260829, as FOUNDATION_PARENT. XC3 remains a historical verified ancestor and is not selected as the foundation parent.
WHY=Live read-only repository evidence showed a clean canonical checkout at the immutable XC8 freeze, with XC3 present as an ancestor and the lineage advanced cleanly through XC8.
SUPERSEDES=NONE
AFFECTS=governance/channel-handoff-bootstrap;governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md;governance/handoff/DECISION_HISTORY.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-003
DATE=2026-09-05
LANE=CUSTOMER_CHANNEL
TRANCHE=SUCCESSION_GOVERNANCE
QUESTION=Does the XC8 freeze authorize XC9 or any new Customer Channel implementation?
DECISION=NO. ACTIVE_TRANCHE=PCR_HOLD_NO_ACTIVE_IMPLEMENTATION_TRANCHE and IMPLEMENTATION_AUTHORIZED=NO until newer PCR authority explicitly authorizes new channel work.
WHY=An immutable freeze proves baseline state but does not grant future implementation authority.
SUPERSEDES=NONE
AFFECTS=all_post_XC8_customer_channel_work;governance/handoff/LIVING_HANDOFF.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-004
DATE=2026-09-05
LANE=CUSTOMER_CHANNEL
TRANCHE=SUCCESSION_GOVERNANCE
QUESTION=What succession pattern must a future Customer Channel instance follow?
DECISION=CHANNEL_START_HERE.md, when later authorized, is only a stable front door. It must direct the successor to governance/handoff/LIVING_HANDOFF.md first, then EVIDENCE_INDEX.md and DECISION_HISTORY.md only as needed, require live Git verification before mutation, reject chat memory as primary authority, and fail closed if live repository verification is unavailable. CHANNEL_START_HERE.md is not domain authority and must not become a second living handoff.
WHY=Repository evidence and the living handoff are the institutional memory; the front door must remain compact and non-duplicative.
SUPERSEDES=NONE
AFFECTS=CHANNEL_START_HERE.md;governance/handoff/LIVING_HANDOFF.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-005
DATE=2026-09-05
LANE=CUSTOMER_CHANNEL
TRANCHE=SUCCESSION_GOVERNANCE
QUESTION=What mutation authority exists while the three-file handoff foundation is under PCR content review?
DECISION=No repository mutation is authorized. The exact three-file content package must be prepared outside the repository and returned to PCR with path, SHA256, and line count for each file.
WHY=PCR accepted the CASE_B preflight and ratified the parent but withheld branch, worktree, file creation, staging, commit, tag, push, and CHANNEL_START_HERE.md creation until content review passes.
SUPERSEDES=PHASE_1_PREPARE_ONLY_FOR_THIS_BOUNDED_PHASE_2_CONTENT_REVIEW
AFFECTS=governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md;governance/handoff/DECISION_HISTORY.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-006
DATE=2026-09-05
LANE=CUSTOMER_CHANNEL
TRANCHE=SUCCESSION_GOVERNANCE
QUESTION=What authority exists after PCR passes the three-file foundation content review?
DECISION=PCR accepted the three-file handoff foundation content and authorized creation of governance/channel-handoff-bootstrap from exact XC8 parent 76b5dff4a37426bcebb6919e6c58b479a3afc635 plus installation of exactly LIVING_HANDOFF.md, EVIDENCE_INDEX.md, and DECISION_HISTORY.md. Staging and commit remain withheld pending final installed-file hash review. No Customer Channel domain implementation tranche is authorized.
WHY=The institutional-memory package passed content review, but PCR requires the installed living handoff to reflect the latest authority decision before it becomes immutable repository evidence.
SUPERSEDES=CHANNEL-GOV-005_ONLY_AS_TO_POST_CONTENT_REVIEW_GOVERNANCE_INSTALLATION_AUTHORITY
AFFECTS=governance/channel-handoff-bootstrap;governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md;governance/handoff/DECISION_HISTORY.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-007
DATE=2026-09-05
LANE=CUSTOMER_CHANNEL
TRANCHE=SUCCESSION_GOVERNANCE
QUESTION=What authority exists after PCR accepts the final installed hashes?
DECISION=PCR accepted the installed three-file Customer Channel handoff foundation and authorized staging exactly those three governance paths and creating exactly one docs-only foundation commit. No tag, push, domain implementation, XC9 work, or CHANNEL_START_HERE creation is authorized.
WHY=The installed content and hashes passed PCR review and the living handoff must reflect commit authority before becoming immutable repository evidence.
SUPERSEDES=CHANNEL-GOV-006_ONLY_AS_TO_POST_HASH_REVIEW_STAGE_AND_COMMIT_AUTHORITY
AFFECTS=governance/channel-handoff-bootstrap;governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md;governance/handoff/DECISION_HISTORY.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-008
DATE=2026-09-05
LANE=CUSTOMER_CHANNEL
TRANCHE=SUCCESSION_GOVERNANCE
DECISION=PCR accepted immutable Customer Channel handoff foundation commit 0ac40d8e7b32e4147dd2ebe02746311bdcf5802a. The handoff foundation is FROZEN_ACCEPTED. No Customer Channel implementation tranche or XC9 is authorized. CHANNEL_START_HERE.md remains a separate PCR gate.
WHY=The repository living handoff must reflect completion and acceptance of its own foundation before becoming the authority target of the stable succession front door.
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-009
DATE=2026-09-05
TRANCHE=SUCCESSION_GOVERNANCE
DECISION=
PCR accepted the verified CHANNEL_START_HERE installation and required a final living-handoff synchronization before succession could pass.
The first cold-start test correctly failed closed because the stored living handoff predated CHANNEL_START_HERE installation.
The durable state is now START_HERE installed with cold-start succession retest pending.
No Customer Channel domain implementation or XC9 is authorized.
WHY=
A stable front door may remain static, but the living handoff must remain current to durable succession milestones.
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-010
DATE=2026-09-22
LANE=CUSTOMER_CHANNEL
TRANCHE=XC9_HS0
QUESTION=WHAT_IS_THE_CURRENT_FORWARD_CUSTOMER_CHANNEL_SOURCE_AND_WHAT_MUST_HAPPEN_BEFORE_XC9_R1
DECISION=COM_W1_CC_AT_df09fdf32e7fd7e87f9f925751a1baf204687abd_IS_THE_CURRENT_ACCEPTED_FORWARD_CUSTOMER_CHANNEL_SOURCE;XC9_R0_IS_PASS_ACCEPTED;THE_OLD_XC8_FAKE_PAYMENT_REQUEST_RUNTIME_IS_TO_BE_REPLACED_BY_ACCEPTED_XBOS_C3;XC9_R1_REMAINS_NOT_AUTHORIZED_UNTIL_CUSTOMER_CHANNEL_CONTINUITY_AND_XBOS_C3_CONTINUITY_ARE_CLOSED
WHY=THE_LIVE_DOMAIN_LINEAGE_ADVANCED_BEYOND_THE_STORED_XC8_POINTERS_AND_IMPLEMENTATION_MUST_NOT_BEGIN_FROM_STALE_SUCCESSION_STATE
SUPERSEDES=NONE_HISTORICAL_CHANNEL_GOV_001_THROUGH_009_PRESERVED
AFFECTS=governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md;governance/handoff/DECISION_HISTORY.md;future_XC9_R1_authorization
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-011
DATE=2026-10-09
LANE=CUSTOMER_CHANNEL
TRANCHE=CC-WA-ORDER-GOV-SYNC-R1
QUESTION=What is the Customer Channel program state after R1/R2 acceptance while XBOS hosted WND compatibility remains blocked?
DECISION=Install PROGRAM_STATE=CC_WA_ORDER_R1_R2_PASS_ACCEPTED_WAITING_ON_XBOS_HOSTED_COMPATIBILITY and ACTIVE_TRANCHE=DEPENDENCY_HOLD. CC-WA-ORDER-R1 and CC-WA-ORDER-R2 are PASS_ACCEPTED. CC-WA-ORDER-R3A and CC-WA-ORDER-R3B remain HOLD. WND-DEMO-CC-COMPAT-R3 is BLOCKED_ACCEPTED on HOSTED_SOURCE_STATE_SCHEMA_EQUIVALENCE_MISMATCH and WND-DEMO-CC-COMPAT-R3-R1 is the active XBOS read-only forensic child. Expected hosted WND context references are for future validation only and are not authorized for Customer Channel session use until WND-DEMO-CC-COMPAT-R3=PASS_ACCEPTED.
WHY=Customer Channel had no defect requiring implementation at that edge. Recording the dependency hold prevented stale governance from incorrectly authorizing implementation or using expected hosted references before XBOS acceptance.
SUPERSEDES=CHANNEL-GOV-010_AS_CURRENT_MOVING_PROGRAM_STATE_ONLY
PRESERVES=CHANNEL-GOV-001_THROUGH_010;IMMUTABLE_FOUNDATION_HISTORY
AFFECTS=governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md;governance/handoff/DECISION_HISTORY.md
IMPLEMENTATION_AUTHORIZED=NO_AT_THAT_EDGE
RUNTIME_MUTATION_AUTHORIZED=NO_AT_THAT_EDGE
NEXT_ELIGIBLE_TRANCHE=CC-WA-ORDER-R3A
RELEASE_CONDITION=WND-DEMO-CC-COMPAT-R3_PASS_ACCEPTED
RETURN_TO=PCR
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-012
DATE=2026-10-10
LANE=CUSTOMER_CHANNEL
TRANCHE=CC-WA-ORDER-R3A-R1-R2-C2
QUESTION=How must primary Customer Channel runtime configuration treat Meta WhatsApp provider configuration?
DECISION=PCR accepted source successor 65cb548cabc41a41230b23186f95fab75bdfe0e8 / tree fc917799b83db80259f5e95445bccb647ad28361. Primary RuntimeConfig is provider-independent for process boot, health, base Customer Channel configuration, and private XBOS composition. MetaWhatsAppConfig remains mandatory only at Meta-specific boundaries and fails closed when absent. Durable Meta W1 receives the exact explicit Meta phone_number_id rather than reading Meta config implicitly.
WHY=The prior blocker was caused by transport-specific Meta configuration being coupled to primary runtime configuration. The bounded C2 repair removed that coupling without weakening Meta fail-closed behavior or real-XBOS composition.
SUPERSEDES=CHANNEL-GOV-011_AS_CURRENT_ACCEPTED_SOURCE_HEAD_ONLY
PRESERVES=CHANNEL-GOV-001_THROUGH_011
AFFECTS=governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-013
DATE=2026-10-10
LANE=CUSTOMER_CHANNEL
TRANCHE=CC-WA-ORDER-R3A-R1-R2-C3
QUESTION=Is the accepted C2 source actually aligned and live on Customer Channel staging?
DECISION=YES. PCR accepted runtime alignment of xafpay-customer-channel-staging to branch cc/wa-order-r2-twilio-demo-adapter and exact SHA 65cb548cabc41a41230b23186f95fab75bdfe0e8. Exactly one deployment was consumed, health passed, idle stability passed, auto-deploy remained off, and no commerce/provider/financial effect occurred.
WHY=R3A could not proceed until the accepted source existed as actual staging runtime rather than only as published Git state.
SUPERSEDES=NONE
AFFECTS=governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-014
DATE=2026-10-11
LANE=CUSTOMER_CHANNEL
TRANCHE=WND-DEMO-CC-COMPAT-R4
QUESTION=What is the accepted cross-lane context-attestation contract for the frozen 65cb548 Customer Channel client?
DECISION=PCR accepted WND rich context-attestation compatibility while preserving the WND minimal contract. Customer Channel continues to send schema xafpay.customer-channel.xbos-context-attestation.v1 and parse the rich response. For the controlled R3A fixture, EntryPurpose=merchant_discovery is explicit PCR authority and is not derived from XBOS purpose_code restaurant.customer_channel.checkout. Current WND live deploy is dep-db5mrmvlk1mc73ahv280 on source 4b88e58e79b19b4379386b2f2ab206a2d8956a2b.
WHY=The two purpose values represent distinct semantic dimensions and must not be collapsed. Compatibility belongs at the cross-lane contract seam, not in an invented Customer Channel mapping.
SUPERSEDES=NONE
AFFECTS=governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-015
DATE=2026-10-11
LANE=CUSTOMER_CHANNEL
TRANCHE=CC-WA-ORDER-R3A-O1
QUESTION=Did the controlled durable Customer Channel bootstrap succeed after WND rich compatibility became live?
DECISION=YES. PCR accepted the exact recovered O1 session sess_GViBCu9hvADwPTYxu7zxpXQkBuq4pNpOi5N3Kgz4wZs as PASS_ACCEPTED. The session is generation 0, security-bound, purpose merchant_discovery, and bound to the accepted merchant/location/context. Exactly one conversation, one identity binding, and one session were created. No second bootstrap is permitted and the fixture must not be deleted merely to replay O1.
WHY=The interrupted one-off execution had already crossed the authorized write boundary and completed the O1 postwrite proof. Creating another fixture would violate the one-session law.
SUPERSEDES=NONE
AFFECTS=governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-016
DATE=2026-10-11
LANE=CUSTOMER_CHANNEL
TRANCHE=CC-WA-ORDER-R3A
QUESTION=Which R3A portions are closed, and where is the remaining blocker?
DECISION=Trusted Customer Channel session, live rich reattestation, and SHOW_CATALOG are PASS_ACCEPTED. The authoritative catalog is one section with Chicken, Ndole, and Eru at 2500 XAF each, currency XAF, price_code retail. R3A is BLOCKED_ACCEPTED before draft creation because the checkout service credential binding and an explicit accepted fulfillment context were not yet closed. Draft route call count remains zero and no business effect occurred.
WHY=The accepted WND facade has a separate checkout service-auth boundary and requires an explicit service_mode. Catalog-reader authority cannot substitute for checkout authority and fulfillment cannot be inferred from merchant_discovery or XBOS purpose_code.
SUPERSEDES=NONE
AFFECTS=governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md
REOPEN_AUTHORIZED=NO

DECISION_ID=CHANNEL-GOV-017
DATE=2026-10-11
LANE=CUSTOMER_CHANNEL
TRANCHE=CC-WA-ORDER-R3A-D1
QUESTION=How will PCR close the R3A draft preconditions without redesigning the accepted facade?
DECISION=D1 is authorized. It may reconcile only an existing checkout service credential for the accepted customer-channel service identity and permission restaurant.customer_channel.checkout, never generate or rotate a token, never collapse catalog and checkout credentials, and never expose raw credential/digest material. PCR explicitly selects service_mode=dine_in and authorizes read-only discovery of one existing active table at tenant 2 / CM001 and the accepted location. If multiple eligible tables exist, choose the lowest canonical resource ID. D1 itself creates no draft or business effect. D1 execution remains held until CUSTOMER-CHANNEL-GOV-CONTINUITY-R2 is PCR-accepted.
WHY=The accepted WND facade has only dine_in, takeaway, and delivery. Dine-in is the lowest-impact existing contract because it can use an existing restaurant resource without manufacturing customer contact, address, provider identity, or Party.
SUPERSEDES=NONE
AFFECTS=governance/handoff/LIVING_HANDOFF.md;governance/handoff/EVIDENCE_INDEX.md
REOPEN_AUTHORIZED=NO

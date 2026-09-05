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

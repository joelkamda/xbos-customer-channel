# XafPay Customer Channel — Stable Bootstrap Front Door

LANE=CUSTOMER_CHANNEL

CHANNEL_START_HERE_IS_DOMAIN_AUTHORITY=NO
DOMAIN_AUTHORITY=governance/handoff/LIVING_HANDOFF.md

CHAT_MEMORY_PRIMARY_SOURCE=NO
REPOSITORY_EVIDENCE_PRIMARY_SOURCE=YES

LIVE_REPOSITORY_ACCESS_UNAVAILABLE=BOOTSTRAP_PARTIAL_ONLY
FALSE_GIT_VERIFICATION=FORBIDDEN
MUTATION_WITHOUT_LIVE_GIT_VERIFICATION=NO
CROSS_LANE_MUTATION=NO

---

## Purpose

This file is the stable succession entry point for the XafPay Customer Channel lane.

It does not contain moving Customer Channel state.
It does not authorize implementation.
It does not replace the living handoff.
It does not grant cross-lane authority.

A successor must use this file only to locate the current lane authority, verify live repository state, and return the required bootstrap report before taking any mutating action.

---

## Required bootstrap chain

Follow this sequence in order:

1. Read this file completely.
2. Read `governance/handoff/LIVING_HANDOFF.md` completely.
3. Read `governance/handoff/EVIDENCE_INDEX.md` only as needed to verify immutable evidence, hashes, commits, tags, or lineage.
4. Read `governance/handoff/DECISION_HISTORY.md` only as needed to understand consequential governance or architecture decisions.
5. Verify the live Git repository before any mutation.
6. Compare live repository evidence with the living handoff.
7. Return the required bootstrap report.
8. Take no mutating action unless the living handoff and live repository evidence together establish that the action is authorized.
9. Fail closed if live repository access is unavailable or verification cannot be completed.

Bootstrap chain:

`CHANNEL_START_HERE.md`
→ `governance/handoff/LIVING_HANDOFF.md`
→ `EVIDENCE_INDEX.md` only as needed
→ `DECISION_HISTORY.md` only as needed
→ live Git verification before mutation
→ bootstrap report
→ fail closed if live repository access is unavailable

---

## Source-of-truth rules

The primary source for current Customer Channel authority is:

`governance/handoff/LIVING_HANDOFF.md`

Repository evidence is the primary source for repository facts.

Chat memory, prior conversations, summaries, screenshots, copied terminal output, or remembered state must not be treated as primary authority when live repository evidence is available.

If chat memory conflicts with the living handoff or live immutable repository evidence, do not resolve the conflict by assumption. Report the inconsistency and return to the authority named by the living handoff.

Do not claim a Git branch, commit, tag, status, worktree, file, hash, or lineage was verified unless it was actually verified from live repository evidence.

---

## Live Git verification requirement

Before any repository mutation, verify at minimum:

- repository root / top-level
- current branch
- current HEAD
- tracked working-tree status
- staging status
- untracked paths
- active worktree path
- worktree topology when relevant to the proposed action

Use additional read-only Git evidence when needed to verify ancestry, tags, prior freezes, or governing commits.

Do not reset, clean, restore, rebase, merge, move, delete, repurpose, stage, commit, tag, push, create a branch, create a worktree, or change architecture merely to make the repository match an expected state.

The script, prompt, or operator must agree with the repository; the repository must not be altered to satisfy a failed verifier.

---

## Customer Channel authority boundary

CUSTOMER_CHANNEL_OWNS=
SESSION_CONTEXT;
CUSTOMER_ENTRY_ORCHESTRATION;
CUSTOMER_PRESENTATION;
CHANNEL_TRANSPORT

CUSTOMER_CHANNEL_DOES_NOT_OWN=
MERCHANT_ORDER_TRUTH;
XBOS_CATALOG_TRUTH;
XBOS_PRICING_TRUTH;
XBOS_FULFILLMENT_TRUTH;
PROVIDER_EXECUTION_TRUTH;
PROVIDER_EVIDENCE_TRUTH;
CORE_MONETARY_TRUTH;
CORE_POSTING_AUTHORITY;
WALLET_BALANCE_TRUTH

CHANNEL_ORCHESTRATION!=DOMAIN_AUTHORITY

NO_SECOND_MERCHANT_MASTER=YES
NO_PAYMENT_AUTHORITY=YES
NO_PROVIDER_AUTHORITY=YES
NO_CORE_FINANCIAL_AUTHORITY=YES
NO_CROSS_LANE_SOURCE_MUTATION=YES

If a requested action crosses one of these boundaries, stop and return to the governing coordination authority rather than mutating another lane.

---

## Moving state belongs only in the living handoff

Do not duplicate or maintain moving state in this file.

In particular, do not hard-code here:

- current XC tranche
- current implementation authority
- current blockers
- current next authorized action
- current worktree HEAD
- current freeze state
- current branch selected for implementation
- temporary PCR execution authority

Those values belong in `governance/handoff/LIVING_HANDOFF.md` and must be read fresh on every bootstrap.

This front door should remain stable across future Customer Channel tranches.

---

## Required successor bootstrap report

Before taking any action, return exactly these markers with values derived from the living handoff and live repository evidence:

HANDOFF_READ=
LANE=
CURRENT_BASELINE=
CURRENT_REPOSITORY_HEAD=
ACTIVE_TRANCHE=
CURRENT_STATE=
IMPLEMENTATION_AUTHORIZED=
FREEZE_AUTHORIZED=
CURRENT_BRANCH=
CURRENT_WORKTREE=
BLOCKERS=
NEXT_AUTHORIZED_ACTION=
RETURN_TO=
REPOSITORY_EVIDENCE_INTERNALLY_CONSISTENT_WITH_LIVING_HANDOFF=

Allowed consistency values:

`YES | NO | VERIFY_REQUIRED`

The report must distinguish recorded handoff state from live repository facts when they differ.

---

## Fail-closed behavior

If `governance/handoff/LIVING_HANDOFF.md` cannot be read completely:

- do not infer moving authority from this file;
- do not infer authority from chat history;
- do not mutate the repository;
- return a blocked bootstrap result.

If live repository access is unavailable:

LIVE_REPOSITORY_ACCESS_UNAVAILABLE=BOOTSTRAP_PARTIAL_ONLY

In that state:

- reading and reporting from the living handoff is allowed;
- false Git verification is forbidden;
- repository mutation is forbidden;
- the bootstrap report must use `VERIFY_REQUIRED` for repository facts that were not actually verified;
- return to the authority named by the living handoff.

If repository evidence conflicts materially with the living handoff:

- preserve the repository state;
- report the inconsistency;
- do not repair, reset, clean, restore, rebase, merge, or otherwise normalize the repository without explicit authority;
- return to the authority named by the living handoff.

---

## Mutation gate

Reading this file grants no mutation authority.

A mutating action is allowed only when all of the following are true:

1. `governance/handoff/LIVING_HANDOFF.md` authorizes the action or names an authority that has explicitly authorized it.
2. Live repository verification has been completed.
3. The proposed action is within the Customer Channel authority boundary.
4. The action does not introduce cross-lane source mutation.
5. Any branch, worktree, changed-path, staging, commit, tag, or push constraints governing the action are satisfied exactly.
6. No newer immutable repository evidence contradicts the governing handoff state.

When any condition is not satisfied, fail closed.

---

## Stable succession rule

A new Customer Channel builder should be able to begin with only a short instruction to start from `CHANNEL_START_HERE.md`.

The builder must reconstruct current state from repository evidence and the living handoff rather than from prior chat history.

This file must therefore remain a stable pointer and bootstrap protocol, not a second living handoff.

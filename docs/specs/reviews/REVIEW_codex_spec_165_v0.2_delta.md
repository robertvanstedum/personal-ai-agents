# Codex delta review — Spec 165 v0.2

**Verdict: substantial improvement; changes required before v0.3 is locked.** Most earlier findings are adopted in the written contract. The cross-tool action key still permits the original duplicate effect, and the new CoS storage/projection rules need recovery corrections. This is a specification review, not approval of A1, persona adoption or release authorization.

28 October 2026. Pinned spec: `729d29c9efc4582784d2203a176fb29769c74cca`, `docs/specs/spec_165_minimoi_1_2_voice_standard_mc_cos_2026-10-28.md` (all spec line references below refer to this revision). Compared with v0.1 `64b1896f` and Codex review `89edb852`. Checked service contracts against code `93bf00c6`; the separate CC_21 review checks A1 at `93f4f583`. No claim about later builder commits.

This review is written on `claude/spec-165-voice-1-2` in an isolated local clone rooted at `/private/tmp/codex-spec165-v02-review-729d29c9`, with HEAD still the requested pin. No source-worktree checkout, reset, branch mutation, commit or push. A matching copy and add-file patch are placed in the voice handoff folder for the sole branch writer.

## Findings in the delta

### V2-1 — P1: remove the tool name from the single-effect identity, or make consult non-effectful

Evidence: spec §5.1, **lines 252–261**; code `93bf00c6:minimoi_portal/guild_ui/mc_voice_direct.py` typed-turn dispatch, and `domains/cos/confer_service.py:257–282`.

`(session, owner_item_id, tool)` gives Check/consult and provider/save_note different identities for one utterance. Both typed-turn paths can save a note. The proposed CoS UUIDs then differ, so operation-level dedup also cannot help. This reproduces the original B1 design error even if all same-tool retry tests pass. A1's cross-tool admission is the better rule for current MC/CoS behavior.

Change to one effectful dispatch per `(host, session, stable owner item)` across both tools, deriving the platform request ID from that same key. Alternatively explicitly make consult non-effectful before retaining per-tool identities. Preserve deliberate repeated utterances as separate items. Add manual-consult→model-save_note and the reverse to the stated acceptance cases. Bind a provider response to its originating owner item, not whatever transcript completed most recently (CC_21 A1-C2).

### V2-2 — P1: a projection file's existence cannot be the final checkpoint for a mutable session

Evidence: spec §6.5, **lines 427–435**, together with late results **294–295**, unknown reconciliation **273–275**, and summary decisions **443–444**.

The proposed projection skips a session after its first atomic rename. Yet “settled” only means ended/dropped with no pending call: an unknown call can later reconcile, and summaries/decisions can arrive after End. These later records will never reach recall if existence is the checkpoint. There is also no projection trigger on worker completion: End can decline projection because a call is pending, then the worker finishes while Robert leaves the page; projection waits for an unrelated future list/bootstrap.

Define a durable source revision/high-water mark and an idempotent replacement or per-event projection. Under the session lock, re-project when the committed revision advances; serialize writers so an older snapshot cannot overwrite a newer one. Trigger on late outcome, summary and decision commits as well as End/recovery; retain retry on read/bootstrap. Fsync the parent directory after a new file/rename where crash durability is promised. Test crash/retry and late updates after the first projection exists, not only a crash before its first rename. This is code/file-triggered work, not a request for new background automation.

### V2-3 — P2: the truncated-tail rule turns a recoverable tail into unspecified interior corruption

Evidence: spec §6.5, **lines 417–421**.

Ignoring an invalid final JSON fragment and starting the next append on a new line leaves the invalid fragment in the middle of the file. On the following restart it is no longer a final line, so the stated recovery rule no longer applies. The spec does not say how the index recovers later valid records or whether the earlier incomplete marker persists. A valid JSON object without its final newline is another ambiguity: ignoring it and then appending a newline can make an unacknowledged operation reappear as accepted on the next restart.

Choose one exact repair: under the cross-process lock, preserve/quarantine and truncate to the last committed boundary before appending, or persist a corruption/commit-boundary record that the parser always honors. Specify durable incomplete state and whether a complete JSON object without newline is committed. Test crash→read→append→second restart, including duplicate/conflicting IDs and a valid-but-unterminated last object. Do not acknowledge later writes while the restart parser's behavior is undefined.

### V2-4 — P2: pagehide needs a distinct close protocol, not a five-second awaited barrier

Evidence: spec §5.2, **lines 284–293**; regression line **298**.

The common barrier says to wait up to five seconds and then close, but a leaving page cannot depend on those awaited callbacks. “Batches in order … End last” does not establish arrival/commit order for separate keepalive requests. The declared incomplete count also switches units from turns to unconfirmed batches. The existing A1 single End carrying a bounded batch avoids the concurrent-End race, while honestly marking overflow incomplete.

Specify a synchronous unload dispatch: one bounded close envelope carrying owed events, or an explicit server protocol that can accept/sequence late pre-close batches. Count unique event IDs, not batches, within the aggregate keepalive allowance. If End itself never arrives, durable start/recovery must expose capture as unconfirmed instead of inferring complete. Keep normal End/Sum up's acknowledged barrier separate, and require its timeout result to terminate finalization rather than re-awaiting the same abandoned request (CC_21 A1-C3).

### V2-5 — P2: summary timeout must distinguish unavailable from still running

Evidence: spec §5.3, **lines 309–310**; gateway boundary **325–329**; portal timeouts **456**.

The draft says “Summary unavailable” after 45 seconds, while also allowing a summary to land after the voice ends. A UI deadline alone does not establish that the gateway request stopped. Offering another summary request at that point can start concurrent generations and conflicting first-write-wins proposals. A1's explicit “still being written” is more honest for a request still executing, although it needs bounded finalization.

Give summary generation its own durable attempt identity/state: either the request is conclusively cancelled/failed and a retry is permitted, or it remains pending/unknown and is reconciled without another generation. Define what the 45-second clock bounds (client wait, worker or gateway), the server timeout and the written late result. Do not equate a client timeout with failure. Preserve one summary proposal and one decision across two-device retries.

## Earlier spec findings: adoption status

“Adopted” below assesses the specification, not completed implementation.

| Prior finding | Status | Assessment |
|---|---|---|
| S1 CoS request/receipt contract | **Adopted with a problem** | UUID5 and real `note_save`/`reminder_set` fields correct the original API mismatch (§6.4). Tool-key cardinality remains V2-1. Explicitly reject `/new` before dispatch: the actual typed service resets its runtime at `confer_service.py:234–254`, so mapping the returned reset to `not_done` would be false. |
| S2 enforced no-tool summary | **Adopted** | §5.4 names a plain gateway completion, no agent runtime/tools and no fallback; this resolves the earlier unenforced `tool_policy` promise. Test outgoing envelope plus no effect-capable runtime invocation. Keys/reachability remain activation prerequisites, not implementation facts. |
| S3 durable CoS store/projection | **Adopted with a problem** | Separate storage, schema, locks, fsync, conflicts, attention/detail/download and recovery tests are substantial. V2-2/V2-3 remain. |
| S4 off/admission/rollback | **Adopted** | S0–S5 distinguishes presentation, mint permission, continued record access and old-image disk-only preservation. S5 explicitly does not claim CoS UI access. Actual old-image MC rendering remains unverified and must be checked before relying on it. |
| S5 corrected core contract | **Adopted with a problem** | §5 and A1 require fixes and allow replacing defect assertions; V2-1/V2-4/V2-5 remain. CC_21 is separate implementation evidence. |
| S6 read identity/write guard | **Adopted** | §4.4/§6.7 separate owner GET from verified-write POST; unconditional portal CSRF/prefix handling and stripped client identity are explicit. Verify backend owner/session matching too. |
| S7 Part A handover | **Adopted** | D23 and §14.1 explicitly scope the mirrored listener/cache change to A3, contingent on Robert's decision; rest of Part B stays deferred. Connecting/ending states must also be covered, not just active speech. |
| S8 latency/no-regression | **Adopted** | Transcript comparisons are like-for-like, sample count/statistic/tolerance named, audible parity not claimed; Part B captures fresh per-language baselines. Acceptance of the proposed tolerance remains Robert's decision. |

## B1–B7 in the proposed shared contract

| Finding | Status | Assessment |
|---|---|---|
| B1 | **Adopted with a problem** | V2-1: per-tool identities still admit two effectful paths. Add source-response binding and duplicate completed-item tests. |
| B2 | **Adopted with a problem** | Acknowledged barrier, partial capture and late receipt cutoff are explicit; V2-4 must settle unload and terminal timeout behavior. |
| B3 | **Adopted with a problem** | Summarizing state stops heartbeats; V2-5 must make timeout/retry semantics truthful. |
| B4 | **Adopted with a problem** | Acknowledged/failed states and durable incomplete counts are specified; V2-4 must fix units and missing-close recovery. |
| B5 | **Adopted** | Event-id replay and later-turn confirmation, including server-side Keep Talking, are explicit. |
| B6 | **Adopted** | §5.3 requires one tracked response and its audio drain; this is the right contract. Define callback outcomes for cancelled/cleared/no-audio and timeout separately from “heard.” OpenAI's signal is correctly unverified; A1's word heuristic is not compliance. |
| B7 | **Adopted** | Explicit post-dispatch exceptions/timeouts are unknown, durable pre-dispatch admission and read-only reconciliation survive restart, no automatic redispatch. CC_21 finds the implementation still returning failure on one such path. |

No earlier finding is wholly unadopted; the “with a problem” entries are not closure.

## Code cross-checks and implementation clarifications

- **Four consult outcomes:** the enum is useful. A backend's generic exception class alone cannot prove `not_done`; classify by the dispatch/effect boundary and authoritative receipt. `ConferOperationFailed` has both pre-operation and post-operation-check sites (`confer_service.py:260–264,288–301`). Never translate an uncertain write to definite failure solely by class name.
- **CoS service:** `normalize_request_id` accepts UUIDs (`:188–195`); `note_save` has `operation_id`, status `saved`/`deduplicated` (`:277–281`); reminder output includes `reminder_id` and `due_utc` (`:319–327`). `_process_confer_turn` passes the request ID and save/reset/reminder callbacks (`domains/cos/chief_of_staff.py:811–841`). The spec's corrected mapping is grounded in these paths.
- **Reminder and reset exclusion:** D20 must be enforced before dispatch, not only by the realtime prompt. `/remind` is already parsed by the typed service; the unrestricted runtime may also attempt scheduling from natural language. Define the supported refusal boundary and test no dispatch/effect for excluded commands; do not imply prompt wording disables runtime authority.
- **Owner attribution:** §5.1 improves to capture-item identity; require it to survive the controller and provider response mapping. Opaque model arguments must not choose a different owner text or invent authority.
- **State matrix:** record/end/reconcile/decision routes must remain installed and authorized in S3/S4 even when new mint permission is off. Test provider failure while S2 stays selected separately from deliberate S3 rollback. S5 is reduced access with data preservation, not feature parity.
- **No-copy:** §12.1's import/definition check plus adapter contract suite is the right requirement. A1's symbol/regex scan alone is weaker; keep both structural review and behavior tests.
- **Holding-line timing:** §12.4 promises no more than ten seconds of silence, but a single line at ten seconds followed by pending at twenty-five leaves a fifteen-second interval. Specify another truthful progress line or relax the numeric gate. This is a measurable contract clarification, not a reason to add an agent “continue” nudge.
- **Handover:** wait for actual microphone/transport release, including a still-connecting session; do not assume calling a void `endSession()` or finishing transcript persistence proves the microphone is released. Cover both language pages with the pinned-controller acceptance tests.

## Updated Codex decisions (recommendations, not Robert's approvals)

| Decision | Codex recommendation |
|---|---|
| D18 CoS voice trust | **(a), conditional on Robert's explicit adoption.** Same owner authority as typed Confer is coherent; bind the originating utterance, enforce excluded commands/ambiguity rules, preserve receipts and unknown outcomes. Listing authority is useful; it does not mean every model-inferred action is authorized. |
| D19 bilingual transcription | **(a).** Test the actual retained German/English transcript and canonical action text on both providers. If xAI cannot meet it, report the provider limitation for a decision; no claim that omitting a hint proves fidelity. |
| D20 CoS voice reminders | **(a)** for this release, if Robert accepts the scope limit. Enforce it in the dispatch contract as above; adding a parser is separately reviewed scope. |
| D21 explicit saved notes visible live | **(a)** is reasonable for a deliberately requested durable note with a receipt. Keep ordinary conversation transcripts hidden; state the exception clearly and test Record/Track/memory visibility. This changes no other document or shared user intent until adopted. |
| D22 summary execution | **(a).** Plain gateway completion is the clearest enforced boundary. Reject (b): a no-tools prompt in an effect-capable runtime is insufficient. (c) is acceptable only with an independently proven enforced profile; not a fallback. |
| D23 Part A language handover | **(a)** with Robert's scope decision; mirrored listener/cache bump only. Include connecting and asynchronous release. If declined, **(b)** gives real mutual exclusion; **(c)** must not satisfy the handover safety gate. |
| D1 shared persona | **(a)** under the corrected shared-persona framing. A different manner for CoS is a persona adoption; card edits do not independently authorize it. |
| D2 provider/voice | Keep **(a)**; the xAI briefing/data flow is now explicit. Robert chooses the voice and accepts provider use; no silent default. |
| D5 storage | **Change earlier recommendation from (a) to (b).** Keep raw live records outside the agent's recall mounts; expose only the deliberate settled projection. Close V2-2/V2-3 and verify mounts/backups before activation. |
| D6 recall | Keep **(a)**, now revision-aware and recoverable rather than one permanent existence checkpoint. |
| D8/D9/D11 lifecycle | Support the clarified S1/S3 legacy behavior until A6, then unavailable with record access retained. A refused direct start in S2 still cannot silently fall back. Cleanup needs actual rollback/read evidence and Robert's decision. |
| D10 shared allowance | Keep **(a)**; prove one ledger and concurrent reservation accounting. “Sessions/day” is theoretical capacity after other consumers, not a promise when Dictate shares the pool. |
| D14 combined release | Keep **(a)**: hold the bundle when CoS staging fails; any partial production continuation needs Robert's explicit direction. |
| D15 review sequence | Codex delta, then Grok and Claude Chat is fine; code review and listening remain independent gates. |
| D16 bare confirmations | **(a)** for consequential ambiguity. Local End/Stop and summary confirmation still work; the refusal must not disable stopping audio. The configured plugin inventory is evidence, not proof of every runtime conversational behavior. |
| D3/D4/D7/D12/D13/D17 | Earlier recommendations remain: audition; explicit standalone-gate replacement; retained owner-only download; one writer; unchanged language budgets; shared rules tested on both providers. |

## Handoff

Claude should reconcile V2-1 through V2-5 into v0.3, update the adoption table and retain the distinction between proposed behavior and A1's actual pinned implementation. Keep original contributions and Robert's decisions attributed separately. The A1 code findings are already in `planning-studio/initiatives/INIT-2026-0010-grok-dev-onboarding/minimoi-1-2-voice/REVIEW_codex_A1.md`; they should feed the builder's next available fix round. CC_22 remains a later delta/test-gate request against its own new pin.

This delta used git-object reads and an isolated clone; no extra provider call, microphone, server, flag, implementation build, test suite or deployment. CC_21's focused offline tests are reported in its own review and do not certify this unimplemented spec. The canonical spec and earlier reviews were not edited.

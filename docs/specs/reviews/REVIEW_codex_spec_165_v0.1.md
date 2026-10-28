# Codex review of Spec 165 draft v0.1

**Verdict: direction endorsed; changes required before this is a buildable final spec.** Reuse the German-derived controller and adapters, extract one host core, deliver MC and CoS together, and specify German/Portuguese now for a later paired release. Do not create another voice stack. The CoS service mapping, summary execution boundary, storage/recovery contract and rollout states need correction before Claude locks the design.

28 October 2026. Review requested by Robert and CC_18. This file reviews the 848-line spec introduced at `64b1896fd02eb9ded3c4560b96b3a4222ab791de`, still unchanged at PR #432 head `5f2ebdcefe5c4a5bf915f744bd8ee2eab04a43e9` when fetched. **Robert subsequently referred to v0.2; that revision was not present in the fetched PR or this checkout. This is not a v0.2 approval.** A delta review against the actual v0.2 is required before carrying forward any findings it may already fix.

Code checked: PR #426 `93bf00c627a611d35e6e901a0c8b0ce4b9f4a17d`; relevant main baseline `4954ad9f`; Spec 164 v0.3 at `437b7f8c`. Source references below are repository-relative. Spec line numbers refer to the reviewed v0.1; implementation line numbers refer to the pinned Chunk B checkout unless stated otherwise.

No independent Spec 165 review was opened before writing this review. I had previously read the Chunk B UI review and author records; the separate Chunk B report discloses that limit. This is design/code-path review, not listening acceptance, implementation or release authorization.

## Required changes

### S1 — P1 — Fix the CoS request and receipt contract

**Sections:** §6.4–6.5, spec lines 358–387.

The proposed `request_id=<vc-session-n>` is not accepted by CoS's operation service. `domains/cos/confer_service.py:188–195` requires a UUID; note saves call that normalizer at line 261, and reminders also use it. The spec maps `operation.type == "note"`, but the real result at lines 277–281 is `note_save`, with `status` of `saved` or `deduplicated` and an `operation_id`. These contracts are also present on the pinned main baseline.

**To lock:** define a stable UUID derived from session plus originating owner action (including a namespace/host boundary), not a fresh random UUID on retry. Map `note_save`, `reminder` and their real fields/statuses explicitly. Do not reuse MC's result keys without translation. Include same-action retry and two legitimately repeated owner actions in adapter tests. The attached offline reproduction confirms that the proposed ID is rejected.

### S2 — P1 — Specify a tool-disabled summary execution route, not only a prompt

**Sections:** §4.4, §6.7, §8; spec lines 229, 420–424.

The draft promises an isolated session and no tools, then leaves the session mechanism unverified. The inspected CoS backend already derives its runtime user from `context['confer']['conversation_id']`: `domains/cos/backends/openclaw_backend.py:132–135,376–409`. That resolves where to put a separate summary identity. However, this HTTP adapter does **not enforce `tool_policy`** (explicitly documented at lines 343–344, and unused in the outbound call at line 409). Calling `call_backend(..., tool_policy=...)` alone cannot establish the no-tool property.

MC's current summary path similarly supplies a prompt saying not to use tools (`mc_voice_direct.py:492–498`) and calls its normal model adapter with a separate session (`:564–565`); `minimoi_portal/guild_ui/mc/openclaw.py:200–208` serializes context as text and does not enforce a no-tools policy. This is not evidence that an unwanted action happened; it is evidence that the specified guarantee is not implemented by those call arguments.

**To lock:** name the supported summary backend/runtime permission boundary and its adapter contract for both hosts. Require a distinct summary session, no actionable owner-turn binding, and enforced denial of tool/effect execution. Test a synthetic summary attempting a tool call: no effect, explicit unavailable/refusal if isolation cannot be established. Do not silently fall back to the live CoS conversation. Inventory CoS approval behavior before finalizing D16, rather than leaving an authority decision to the implementer.

### S3 — P1 — Define the CoS durable store and recall projection sufficiently to implement

**Sections:** §4.4, §6.6–6.7; spec lines 227, 395–424.

An append-only JSONL file and `O_NOFOLLOW` do not specify atomic idempotency, concurrent decisions, partial writes, restart reconstruction or recovery of failed projection. The existing `domains/cos/conversation_log.py:27–41` is a best-effort append: it does not deduplicate a `turn_id`, does not report a successful append to its caller, and logs rather than fails a write. Blindly replaying End into this log can duplicate recall; a crash between ending the voice store and projecting recall can omit it.

The proposed sessions GET lists only recovering sessions and undecided summaries. A dropped session, or an ended session whose summary failed, can disappear from the next-page recovery view. “At End” projection also leaves abnormal endings and late consult receipts unspecified.

**To lock:** define record schema/version, stable event IDs, atomic first-write-wins/conflict semantics, durable acknowledgment, process concurrency/locking, truncated-tail recovery, canonical session path validation, access and retention. Define idempotent recall projection with a recoverable checkpoint keyed by session/event; include abnormal ends and late receipts. A list/detail contract must let the owner recover ended/dropped/unavailable sessions without turning Confer into a full history product. Spell out which data is returned while live and what becomes visible only after End. Give the core an exact storage interface and refusal/result types.

**Acceptance:** duplicate End and decision, simultaneous decisions from two devices, crash between append/projection/checkpoint, failed disk write, truncated final line, restart and a receipt arriving after End. Each effect once, incomplete state visible, no transcript lost merely because the summary is unavailable.

### S4 — P1 — Resolve feature-off, admission-off and rollback behavior

**Sections:** §6.8–6.10, §11.1, §11.6, §12.2; spec lines 443–458, 542–550, 607–611, 650.

Feature off is said to restore today's Confer byte-for-byte, including its old modes. Production admission off drops those feature switches, yet the rollback and release check require “Voice unavailable” and no managed/chained script. Those are different states requiring different behavior. Also, redeploying an older image does not establish the promise that no new-format session is hidden, especially for CoS's new file store and renderer.

**To lock:** publish one state table for pre-adoption, new code installed/unadmitted, adopted/enabled, emergency admission-off and full image rollback. Separate adoption/presentation policy from permission to mint sessions. State exactly which routes stay available to read records, end/reconcile an active session and decide a summary when new starts are off. Define compatible reader/export access after image rollback; otherwise qualify the promise to preservation on disk, not visibility in the old UI. Test real portal/backend flag combinations, not a page string alone.

### S5 — P1 — Do not extract known Chunk B lifecycle defects as an unchanged contract

**Sections:** §4.2–4.3, §5, §8, §12.1, §19 A1; spec lines 189, 211, 260–262, 629, 824.

The separate `REVIEW_codex_chunk_B.md` reports reproducible duplicate consults, summary/capture races, heartbeat termination during summary, unreported capture loss, replayed end intent and premature audio termination. An unchanged passing Chunk B suite is necessary regression coverage but not proof these paths work. Current tests pass despite the new reproductions.

**To lock:** reference the pinned corrected Chunk B head and define the intended state/action contract before extraction. Include stable owner-action identity, acknowledged capture barrier, a distinct summarizing state, playback completion and truthful unknown/incomplete outcomes. Preserve behavior except for reviewed fixes; “every test unchanged” must allow replacing assertions that encode a demonstrated defect. One shared implementation must supply these corrections to both hosts. Do not implement the same fixes separately in MC and CoS.

### S6 — P2 — Separate CoS read identity from its verified-write guard

**Sections:** §4.4, §6.6, §6.9; spec lines 224–225, 406–408, 448–453.

The proposed `owner()` reuses the managed write guard, which requires `X-Minimoi-Voice-Write-Verified` (`domains/cos/managed_realtime.py:20–23`). The portal only adds that marker on verified POSTs (`minimoi_portal/proxy.py:303–306`). The new sessions GET cannot use this same guard unchanged. Relaxing it globally would weaken write protection.

**To lock:** define authenticated owner resolution for reads, separate verified-write/CSRF checks for mutations, exact route prefixes and owner/session checks on every route. Strip browser-supplied identity/verification headers as today. Test list/detail/download as an owner, non-owner and forged-header request, along with every POST through the portal. Add suitable private/no-store responses for transcript reads.

### S7 — P1 — Make the Part A handover gate achievable without quietly starting Part B

**Sections:** §12.3, §14.2; spec lines 656–660, 691–692.

Part A requires German voice → floating MC → German. The draft correctly observes German/Portuguese have no companion-opening listener, but places those listeners in deferred Part B. The companion opens after only the promises registered by listeners (`minimoi_portal/static/companion.js:45–56`); no listener means it has no evidence that the language session ended.

**To lock:** explicitly assign the minimal mutual-exclusion integration to Part A for both language pages, with no changes to their voice behavior, if Robert accepts that small safety integration now. Alternatively, disable/refuse the floating voice transition on those surfaces until Part B and write that limitation into the acceptance script. Do not claim the cross-surface handover passed or silently expand the deferred language build. Main Part B integration and paired language promotion remain after MC/CoS production and Robert's go.

### S8 — P2 — Compare like-for-like latency and define the language no-regression gate

**Sections:** §12.4, §14.4; spec lines 666–669, 706–715.

The Part A gate compares first audible playback against German Phase 0 numbers measured at first transcript. Those are different events, especially when transcript delivery trails audio. Recording both on the candidate does not reconstruct the old reference's audio latency. Part B's “each number met or improved” also lacks a sample count, comparison statistic and treatment of variable network/provider latency.

**To lock:** use transcript-to-transcript comparison only where comparable, and obtain an instrumented audible reference before asserting audible parity. Define per-reply timing (not just first audio of the whole session), normal versus consult replies, device/provider/network conditions, repeated samples and a stated tolerance/statistic. Boolean correctness gates—turn completeness, interruption, identity, transcript delivery—must still all pass. Keep Robert's listening judgment as a separate gate. Part B must establish both languages' baselines on the actual then-deployed revision before modifying them.

## Decisions D1–D17

These are Codex's recommendations, not a record of Robert approving them.

| Decision | Codex view | Condition for lock |
|---|---|---|
| D1 CoS manner | **(a)** preserve her current effective manner deliberately in her card. | Robert hears and adopts the final card; do not describe copied MC identity as an independent CoS choice. |
| D2 CoS voices | **(a)** preserve OpenAI `cedar`; select Grok by ear. | No silent fallback to unset defaults during adoption. |
| D3 MC voices | **(a)** audition before switching; include today's `cedar` as a reference. | Robert chooses; no agent chooses timbre for him. |
| D4 Standalone gate | **(a)** is reasonable if Robert explicitly accepts replacing his earlier standalone-first gate. | Keep the bench and its comparable measurements. Until that decision, retain the prior separate gate. |
| D5 CoS store | **(a)** under `COS_CONVERSATIONS_DIR/voice/`. | Close S3; existence of a directory is not sufficient durability/recovery. |
| D6 Recall | **(a)** project revealed records as DATA. | Idempotent, recoverable projection; no replay as commands, no duplicate turns. |
| D7 Transcript download | **(a)** keep it. | Build from retained records, owner-authenticated; label missing/partial capture and unverified playback. |
| D8 Chained mode | **(a)** remove its UI after adoption. | Preserve Dictate and settle S4; no silent fallback when direct admission is disabled. |
| D9 Deletion | **(a)** a later cleanup after a week of use and Robert's go. | Evidence and rollback/read compatibility are prerequisites, not elapsed time alone. |
| D10 Allowance | **(a)** one reservation value and one shared ledger. | Prove portal and CoS address the same ledger and concurrent reservations aggregate; a reservation is not measured billing. |
| D11 Production admission | **(a)** explicit flags, held off by default. | S4's state matrix must distinguish unavailable from reverting to an old voice mode. |
| D12 Confer writer | **(a)** Claude Code, Codex reviewing. | One writer owns core and both adapters; avoid concurrent edits. |
| D13 Language budgets | **(a)** preserve current behavior in this deferred integration. | State that this does not introduce a new hard spend cap; a later budget change is separate. |
| D14 MC/CoS release | **(a)** same release, sequential admission checks. | If CoS fails, hold the promised combined release. Do not silently take option (b) against “both from the start.” |
| D15 Third reviewer | **(a)** Claude Chat is appropriate. | An additional design review does not replace code checks or Robert's listening acceptance. |
| D16 Bare approvals | **(a)** refuse ambiguous consequential confirmations. | Inventory actual CoS approval flows now; distinguish harmless conversation/summary confirmation from granting action authority. |
| D17 Shared manner rules | **(a)** share brevity, interruption and truthful reporting. | Keep each person's identity/card separate; explicitly test this deliberate prompt change on both hosts/providers. |

## Build order and language specification completion

1. Correct the service/runtime/store/rollout contracts above and record owner decisions separately from recommendations.
2. Fix the reviewed Chunk B defects and pin the corrected evidence. Extract that corrected implementation into the single German-derived core; keep provider transports shared.
3. Build CoS's thin adapter and UI against the exact same contracts. Finish MC and CoS acceptance and release gates first.
4. Keep German/Portuguese Part B queued. Their spec should already name the paired change inventory, unchanged personas/scenes/prompts/save behavior, reference commits to capture at B0, test matrix, measurable gate and paired rollback. No language tools, MC personality or summary machinery is implied.
5. Address handover's Part A dependency explicitly as S7, without using it to start the rest of Part B.

A no-copy test should inspect meaningful architecture/dependencies and behavior, not only forbid selected symbol names; otherwise a renamed duplicate passes while a legitimate thin adapter can fail. The French allow-list note (§4.5) should call its required edits a code change with tests, even if small; it is not a card-only supported locale today. Neither point warrants a new voice implementation.

The shared runtime should not assume that “dev” means a Mac forever. §11.2 can name the current test deployment while keeping environment and host configuration separate, consistent with Robert's portable-machine requirement.

## Verification and next action

The companion Chunk B review records 104 passing focused Python tests, 55 passing existing Node scenarios, and new offline defect reproductions. The CoS UUID mismatch was reproduced without initializing its live backend. No provider, microphone, page server, production configuration, flag or deployment was used.

Claude: revise the spec as sole author, publish the actual v0.2 path/commit, and map these findings to fixes or explicit decisions. Codex then checks that delta. This review neither adopts a persona nor changes the voice branch or authorizes the deferred language build.

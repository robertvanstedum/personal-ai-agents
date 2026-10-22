# Review: the MC/CoS redirection and build spec v1.0

**Version 0.1, 22 Oct 2026.** Reviewer: Claude Code (the session that built the journeys and the tool path, and wrote the advisor brief). Read-only; nothing was built, changed or deployed.

**Reviewed:**
- the redirection: `SPEC_MC_REDIRECT_v0.2` (Claude chat, with Grok R07 and Claude chat's R07 answers), decisions D1–D6;
- the build spec: `SPEC_MC_REDIRECT_v1.0_IMPLEMENTATION` (Codex) and its code map;
- the domain APIs: `SPEC_DOMAIN_API_SHARED/WOERTER/PORTUGUESE/CURATOR v0.1` (Codex);
- the review packet and `SPEC_MC_PARTNER_v1.0` (Codex, superseded).

**How:** I checked the spec's factual claims against the code it cites (checkouts T `ebca5557`, J `013d9729`, R `4a5eab1d`, S `claude-mc12`), and against what dev is actually running this morning. I did not re-run T's suites today; I ran them yesterday at `c8b950f7`, one commit behind T.

---

## 1. Verdict

**The redirection is right, and I endorse D1–D6.** The spec v1.0 is accurate where I could check it (table in §2), honest about what it hasn't verified, and buildable. It also resolves the real contradiction in v0.2 ("tier 2 uses C06 only" isn't possible; the receipts core requires a grant) in the right way.

**Two things stand between this spec and the partner Robert wants:**

1. **The partner proof is buried under infrastructure.** As sliced, Robert sees no partner behaviour until five slices and ten new durable-state structures land. The six trial exchanges can be passed much earlier. §5 proposes a "first light" milestone.
2. **The topology is decided but not true anywhere yet.** Dev still answers "Master Craftsman" with the Chief of Staff; the CoS prompt still calls itself "shared CoS and Master Craftsman"; production has no MC runtime at all; and CoS's own backend has no real tool dispatch. These are the facts the slices must start from, and two of them are decisions for Robert (§7).

**For CoS as a partner** (the packet's new direction: CoS uses working-domain APIs directly): I agree, and it's the right correction to the earlier "CoS only reads" rule. But it has a prerequisite the slices don't carry: CoS's OpenClaw backend can't dispatch tools today (§4.3).

---

## 2. What I verified against the code

| Spec claim | Where | Result |
|---|---|---|
| The plugin ignores the runtime's `_toolCallId` | T `plugins/minimoi-tools/index.mjs:153` | ✔ |
| Plugin offers no tools unless `fsPolicy.workspaceOnly` and the token is outside the workspace; dev override `allowTokenReadableByAgent` | T `index.mjs:127-148` | ✔ |
| GitHub taint now persists for the conversation | T `proposals.py:171`; commit `ebca5557` fixed my re-review R-1 | ✔ |
| Adapter grant expiry differs from the proposal and C07 clocks | T `effects.py:108` (proposal expiry + 5 min); J `grants.py:194-195` (approval must fall within 15 min) | ✔, but the spec's "5-minute expiry" wording is off: it is *proposal expiry plus five minutes* |
| C06 execution requires a typed `GrantExecution` (so "tier 2 uses C06 only" is not implementable) | J `receipts.py:146-152` | ✔ |
| C07 refuses paid capabilities and requires a proposal only when the rule says exact approval | J `grants.py:157-160` | ✔; a standing (non-exact) grant is already expressible |
| `docker/mc-agent/relay.mjs` hard-codes `openclaw/mc-agent` | T `relay.mjs:12,54` | ✔ |
| The native launcher (`services/mc_operations/relay.py`, `native-relay.mjs`) exists only in R and requires `mc-agent` | R `relay.py:24,34`; absent from T | ✔ |
| T lacks J's later fixes | `git log ebca5557..013d9729`: 4 commits, 15 files (ledger anchor, delegation results, Codex refusal, wording) | ✔; J lacks 16 T commits |
| German phrasebook: the lock is on the temp file; read-modify-write isn't serialised; Telegram also writes | T `german_domain.py:336-350, 453-459`; `core/telegram/telegram_bot.py` | ✔ |
| Portuguese save overwrites the translation on conflict and translates implicitly | T `portuguese/html_server.py:1000-1008` | ✔ |
| CoS prompt says "shared CoS and Master Craftsman" | T `domains/cos/chief_of_staff.py:209` | ✔ |
| CoS's OpenClaw HTTP adapter does not enforce `tool_policy`; the Grok backend dispatches tools | T `backends/openclaw_backend.py:338`; `grok_backend.py:142-182` | ✔ |
| Standing worker runs at 05:40 Chicago; finding import is deferred | S `scripts/mc_standing/run_cycle.py:4`; J `standing_store.py:50,85` | ✔ |
| `JobManager.start` derives the job id from the note id | J `jobs_api.py` (`job_id_for(note_id)`) | ✔ |
| Reuse targets exist in T (`work_registers.py`, `mc_learning/statements.py`, `library_intake_transport.py`, `library_intake_http.py`) | `git ls-tree ebca5557` | ✔ (none of them are in the root checkout R) |

**Live facts the spec asked for (§2.1 "not treated as a live configuration observation"):**
- Dev portal, 22 Oct 06:xx CDT: `MINIMOI_GUILD_MC=cos`. The "Master Craftsman" conversation on dev is answered by the Chief of Staff. The v0.2 header's "dev already runs CoS and MC separately" is not true yet.
- Dev CoS: `COS_BACKEND_TYPE=openclaw`, agent `cos-agent-a` (OpenClaw in Docker on the Mac).
- Dev portal has no `MINIMOI_MC_TOOLS*` variables: the tool path is not deployed.
- The native OpenClaw on the Mac has agents `main`, `mc-agent`, `mc-chat`, `claude-code`; `mc-chat` has no session store yet (only `agents/mc-chat/agent`).
- Production: per `mc/cos.py:3`, "there is no Master Craftsman relay"; the July container inventory has no MC runtime. I could not inspect production from here; treat as "to verify" in B0.

---

## 3. The redirection (v0.2, D1–D6)

**Endorsed.** Direction B (model understands, platform verifies) with C (one identity) and D (declarative policy) matches both the mission and the authority model. The three-tier split is the single change that lets MC be "the boss day to day" without weakening "Robert's yes is the authority". The corrected invariant in v1.0 §4 ("model output may *request* an effect; the platform executes only under verified authority") is the right wording and should replace v0.2 principle 5.

**Corrections of fact for v0.3:**
- D1 is decided, not done (dev `MINIMOI_GUILD_MC=cos`).
- D6 "MC lives on prod" means **a new production service**: an agent runtime (OpenClaw or other) for `mc-chat` on EC2, under its own OS identity or container, with model credentials, sizing and cost. v1.0 §7.3 specifies the isolation; B7 "controlled cutover" understates that this is new infrastructure with its own change record. It is not needed for the dev proof.
- "Tier 2 uses C06 only" (v0.2 §5): not possible; v1.0 §3.4 is right.
- The lethal-trifecta rule (v0.2 principle 9) is already true of the catalogue: `minimoi_github_issues` reads untrusted text, `minimoi_issue_comment` and `minimoi_data_repair_ticket` send outward, and the guest list is private data. v1.0 handles it with the code requirement and exact outbound bytes; keep that.

---

## 4. Findings on the build spec v1.0

Ranked. Each names the section, the code it touches, the failing scenario and a fix.

### P1

**P1-1: The partner proof comes too late.**
- **Where:** §16 (slices B0–B7, D1–D2), §17 (22 acceptance cases), §15 (10 new or evolved state structures).
- **Scenario:** B0 integrates four checkouts; B1 adds `mc_dispatches` and fresh-session dispatch; B2 adds `mc_tool_invocations`, the proposal outbox and batch cards; B3 the lesson event v2; B4 manifests and the context pack. Only then does A01 (the guest exchange) run as a user would see it. Weeks of infrastructure precede the first "does it feel like a partner?" answer, and Robert's own words were "I don't want to over code on this trivial example."
- **Fix:** define a milestone **M1 "first light"** (§5 below) that passes the six exchanges plus §7a with the smallest set: route, policy tier 1/3 with exact yes, tier-2 preferences, read-only manifests. Defer the invocation table, the generalised outbox, the context-pack byte budgets, provider-swap export and standing assessment to after M1. Nothing in M1 weakens authority; it reuses the proposal/approval code that already passed two reviews.

**P1-2: CoS can't be a partner on its current backend.**
- **Where:** §11.3; slices D1/D1b list "thin MC and CoS adapters" as if CoS could dispatch tools.
- **Code:** T `domains/cos/backends/openclaw_backend.py:338`: "`tool_policy` is NOT enforced by this HTTP adapter." Only `grok_backend.py` dispatches tools (`_dispatch_tool`). Dev CoS runs `COS_BACKEND_TYPE=openclaw`.
- **Scenario:** Robert says to CoS "Save 'Das kriegen wir hin' in Wörter." On the OpenClaw backend nothing can reach the domain API; CoS either refuses or, worse, says it saved it. The Wörter acceptance (A10, A11) cannot pass for CoS.
- **Fix:** add a slice **D0 "CoS tool bridge"** before D1 can claim CoS: either a CoS-scoped native plugin (the `minimoi-tools` pattern, own credential, own allowed-tool list, no MC tools) on `cos-agent-a`, or route CoS to the Grok backend's host-dispatched tools. This is also a decision for Robert (§7).

**P1-3: The same sentence must never take two paths.**
- **Where:** §4 "Retain deterministic authentication…", §16 "retire parsers after equivalent tools pass and a bounded rollback period".
- **Scenario:** this morning's symptom in reverse. With the regex guest parser still on *and* the guest tool on, "reject the guest requests" is answered by the parser before the model ever sees it (`_platform_command` runs first); a paraphrase the parser misses goes to the model and the tool. Two behaviours for one intent, and the bridge period makes acceptance ambiguous.
- **Fix:** one switch per capability: enabling a tool capability disables its regex front end in the same deploy (`_owner_guest_command`, `_owner_lesson_command`, `delegation_command`). Keep the code for rollback, but never both live. Add to A15: "the same sentence never takes two paths."

### P2

**P2-1: Fresh runtime session per dispatch trades away conversation memory.**
- **Where:** §4 "Session binding", §7.1 (`runtime_user` fresh per dispatch), §12.1 (24 KiB recent conversation in the context pack).
- **Scenario:** turn 10 refers to what MC offered in turn 3. With a fresh OpenClaw session each turn, MC knows only what the context pack carries. If the pack summarises, MC "forgets" its own offer; if it carries 24 KiB verbatim, long conversations truncate silently. This morning's lesson exchange worked only because the session remembered.
- **Why it's there:** it is the workaround for the binding ambiguity (a delayed tool call from the previous runtime turn resolving against the next live turn). That is a real problem; the fix is right in principle.
- **Fix:** keep the design, but (a) make the pack carry the last N owner and MC notes *verbatim*, including tool results, before anything else fills the budget; (b) add to the B1 exit a ten-turn conversation whose turn 10 depends on turn 3; (c) measure per-turn tokens and cost against the current stable session before activation (§18 Q1 already asks for this: make it a gate); (d) add a retention task for OpenClaw session files, since there will be one per turn.

**P2-2: Standing grants need their own field, not `owner_turn_ids`.**
- **Where:** §8.3 "`owner_turn_ids` for tier 2 includes the real request note plus the standing-authority source".
- **Code:** J `grants.py:49-91`: `owner_turn_ids` is a tuple of turn ids (max 32); the exact-approval triple (`proposal_id`, `approved_operation_id`, `approval_expires_at`) must be all-set or all-None.
- **Scenario:** a mandate id placed in `owner_turn_ids` passes validation but means nothing to the grant checks; an auditor can't tell a turn from a mandate.
- **Fix:** extend `GrantDefinition` with an explicit `mandate_ref` (nullable) for standing grants, validated against the mandate table; keep `owner_turn_ids` for turns only. Small, reviewed C07 change, consistent with "extend contracts only where required".

**P2-3: Three clocks on one approval.**
- **Where:** §3.4, §9.2.
- **Code:** proposal 10 min (`service.py`); adapter grant = proposal expiry + 5 min (T `effects.py:108`); C07 requires approval within 15 min of now (`grants.py:194`).
- **Scenario:** a yes at minute 9 executes at minute 14 under the adapter but must also satisfy C07's window; a yes at minute 11 fails one check and passes another depending on which runs first. A02 "expired yes" tests only one boundary.
- **Fix:** one named constant for the proposal window and one for execution grace, with the C07 window derived; test both boundaries.

**P2-4: The shared domain-API protocol is heavier than its first use.**
- **Where:** `SPEC_DOMAIN_API_SHARED_v0.1` §3–4: HMAC over purpose/key/audience/env/method/route/timestamp/nonce/idempotency/body, nonce cache, response signing, per-key profiles, rate limits, 30-second freshness.
- **Scenario:** the first three calls are portal→domain over the internal Docker network on one host. The protocol's justification is "remote headless use" and the existing `library_intake_transport` pattern. The risk isn't that it's wrong; it's that v0.1 spends its budget on transport instead of on the three business operations.
- **Fix:** v0.1 = internal network only, the existing signed pattern generalised into one shared library, no tunnel or remote headless; put remote headless in its own later spec. Keep the idempotency and receipt contract exactly as written; that part is the valuable one.

**P2-5: File locks across containers on the Mac need proof, not assumption.**
- **Where:** `WOERTER v0.1` §4 "one stable `.phrasebook.lock` … across all processes on the owning host; cross-host bind-mount locks are not assumed safe."
- **Scenario:** on dev, the German service and the Telegram bot are separate containers sharing a bind-mounted state directory from macOS. Under Colima/Docker Desktop, `flock` on a virtiofs/9p bind mount is not guaranteed to serialise across containers. Two writers each think they hold the lock; the lost-update the pilot is meant to end comes back.
- **Fix:** test it on dev (two containers, same file, concurrent `flock`) before relying on it; if it fails, route Telegram's writes through the domain service (which the spec already wants) and make the German container the only writer.

**P2-6: Auto-kept preferences need a negative acceptance case.**
- **Where:** §10.2 "No second confirmation is required for an ordinary supported preference"; A08.
- **Scenario:** "Let's do this one in German" or "keep it short today" is kept as a standing preference. Robert didn't ask for it to be standing; now every reply is affected until he notices and undoes it.
- **Fix:** add **A08b**: a situational request is not kept; MC may ask one line ("for this conversation, or from now on?") when scope is unclear. The spec's §7 already says this; make it a test.

**P2-7: The German pilot migrates every phrasebook writer, and that is a German-domain change.**
- **Where:** `WOERTER v0.1` §2 and §4: web route, Telegram handlers and the repair script all move to `PhrasebookStore`; the file gains a format version, revisions, an operation map and tombstones.
- **Scenario:** a regression in `_handle_phrase_practice_answer` (Telegram) or in drill pools after the format change breaks Mein Deutsch for Robert's daily use, in order to add a chat-save feature.
- **Fix:** split the pilot: (1) the store and writer migration as its own change record with the full German regression on dev; (2) the API and agent adapters after. Robert's deferral of German/Portuguese standardisation supports keeping (1) minimal.

### P3

- **P3-1 (§3.4 wording):** the adapter grant isn't "5-minute expiry"; it is proposal expiry + 5 minutes (`effects.py:108`).
- **P3-2 (§2, B0):** state the exact integration delta: T lacks J's `8fd828f3`, `94fbf431`, `f1ed5e96`, `013d9729` (15 files); J lacks 16 T commits; R contributes only `services/mc_operations/{relay.py,native-relay.mjs,profile.py,portal_environment.py}` plus the overlay. Pin the result before B1.
- **P3-3 (§11.1):** the GitHub comments adapter reads the last 8 of the first 100: agreed, fix or label.
- **P3-4 (§13.2):** `_main_head` resolves public `main`; research on an unmerged branch needs the ref chosen by the host. The mirror already fetches all heads, so this is a small change.
- **P3-5 (§12.4, A19):** the different-provider continuity test is right but expensive; schedule it after M1, not as a gate for it.
- **P3-6 (D5):** the persona is still unwritten and is the cheapest partner improvement. Until D1 lands, the line that actually governs Robert's "MC" is `chief_of_staff.py:209` ("You are Robert's shared CoS and Master Craftsman"). Write the persona this week; it is needed by every slice.
- **P3-7 (§9.1):** "do not cancel unrelated pending proposals automatically" conflicts with T's current behaviour (`_pending_tool_proposal` cancels pending proposals in the conversation) and with my re-review finding that one-proposal-per-turn is what makes a plain yes safe. Resolve explicitly: up to 10 pending offers requires codes for every approval; one pending offer allows plain yes. Say which M1 uses (I recommend one).

---

## 5. Recommended sequencing: milestone M1 "first light"

**Goal:** Robert runs the six trial exchanges and §7a on dev and judges whether MC feels like a partner, before the heavier infrastructure is built.

| Step | From the spec | Minimum content |
|---|---|---|
| M1.0 | B0 | One integration branch: T + J's four commits + R's launcher. Pin. Deploy inventory (nonsecret). |
| M1.1 | B1 | `MINIMOI_GUILD_MC=openclaw`, chat model `openclaw/mc-chat`, launcher validates the chat agent (not `mc-agent`). Fresh-session dispatch **or** the existing stable session with the current code-required approval, decided by the P2-1 measurement. CoS detached. Read-only tools: health, guest list, issues, `describe_house`/`describe_domain` from manifests with honest `not_implemented`. |
| M1.2 | B2 | Policy file v0 (tier per capability, approval kind, verifier id), one admission path. Tier 3 proposals rendered by the platform (already built), plain yes/`ja` when one offer is pending, batch guest card. Regex guest parser off in the same deploy (P1-3). |
| M1.3 | B3 | `minimoi_keep_preference` as tier 2 with undo, on J's anchored ledger; regex lesson parser off. A08 and A08b. |
| M1.4 | acceptance | The six exchanges + §7a(a) (CoS hands over) + A02/A03/A05/A13 from the matrix, on dev, with receipts. Record pass/fail in the advisor brief v0.2. |

**Explicitly after M1:** `mc_tool_invocations`, the generalised outbox, context-pack byte budgets, D0/D1 (CoS bridge and Wörter), B5 delegation hardening, B6 proactivity, A19 provider swap, B7 production (which needs the prod runtime decision).

**What M1 does not weaken:** every effect still goes through C06/C07; every tier-3 effect still needs Robert's yes on a platform-rendered proposal; the ITIL standard still applies to production. M1 only changes *who understands* Robert.

---

## 6. CoS as a partner

The packet's direction is right: CoS becomes useful in daily life by calling working-domain APIs itself, with its own permissions, not by handing everything to MC. What it needs, in order:

1. **Split from MC** (D1): CoS stops answering as MC; its prompt stops saying it is MC (`chief_of_staff.py:209`).
2. **A real tool dispatch** (P1-2): a CoS-scoped bridge on its backend, with its own credential and allowed-tool list, and MC's administrative tools absent (A11).
3. **Read before write:** Curator article content and Library reads, Wörter reads, before saves.
4. **Hand-off to MC** (§7a): "That's MC's; want me to hand it over?" carrying Robert's words with provenance, never a proposal of its own.
5. **Its own persona**, in Robert's words, alongside MC's.

---

## 7. Decisions for Robert

1. **M1 "first light"** as the first milestone, in the order above (§5), before the rest of v1.0's state and slices. *My recommendation: yes.*
2. **The production MC runtime:** where MC runs on prod (an OpenClaw container on EC2 under its own identity, or another runtime), its sizing and cost. Not needed for M1; needed before B7.
3. **CoS's backend for tools:** a native CoS bridge on `cos-agent-a`, or the Grok backend with host-dispatched tools. Needed before CoS can save anything.
4. **Tier-2 preferences:** keep immediately with undo for plainly standing statements ("from now on", "going forward", "always"), one-line question when scope is unclear. *My recommendation: as v1.0 says, plus A08b.*
5. **The persona session** this week, in his words, for MC and for CoS.

---

## 8. Acceptance additions

- **A08b:** a situational request ("keep it short today") is not kept as standing.
- **A15+:** the same sentence never takes two paths (regex vs. tool) in one deploy.
- **B1 exit+:** a ten-turn conversation where turn 10 depends on turn 3 passes under fresh-session dispatch, with per-turn cost recorded.
- **A02+:** both expiry boundaries (proposal window, execution grace) tested.
- **P2-5 proof:** cross-container `flock` on the Mac bind mount demonstrated, or Telegram writes routed through the domain service.

---

## 9. Where I agree with Codex's dispositions

- Keeping C07 for tier 2 (compile a grant from standing authority) rather than bypassing it.
- Removing the reply-text markers (`JOB:`, `ASK`, `MEETING`) for the MC path, not just the owner parsers. They are the alternate execution path my first review flagged (P2-1 there).
- Retaining a proposal before displaying it, with a display state; a hidden offer must never be approvable.
- The batch guest card with per-item results, never all-or-nothing wording.
- Research stays tier 3 until isolation, provider-data scope and an aggregate budget exist; the canary check this week proved the Mac runner reads anywhere.
- Reusing S's standing worker instead of a second scheduler.

## 10. Version history

| Version | Date | Change |
|---|---|---|
| 0.1 | 22 Oct 2026 | First review of v0.2 + v1.0 + domain API v0.1, with code verification and live dev facts. |

# MC-OC-001 Slice 0b — scoping note: record retrieval for MC

**For:** [MC-OC-001 v0.4](spec_mc_openclaw_operating_model_2026-10-24.md) §10 row 1 (Slice 0b)
**Date:** 24 October 2026 · **Author:** Claude Code · **Reviewer:** Codex
**Status:** Scoping only. Nothing is built or enabled. This note answers a question and sizes work; it is not a design approval.

Source references below are to the working tree of branch `claude/mc-first-light-m1` (commit `a64ba82f`), because the MC tool plugin and the MC turn capture are not on the spec branch’s base commit (`06d8bd09`). Whether each file is on `main`: the turn capture code is; the plugin catalogue `services/mc_operations/plugins/minimoi-tools/tools.json` is not yet.

## 1. OpenClaw’s F4 question: does Records & Rooms already expose a read tool for MC?

**Answer: no. Nothing today lets MC search or read its own prior conversations or Guild decisions as a tool.**

What exists, and why it is not that tool:

| What | Where | Why it does not answer F4 |
|---|---|---|
| MC’s tool catalogue (15 tools: host health, house/domain description, guest requests, GitHub issues and comments, repair ticket, lessons and preferences, gap recording, delegation, access invite) | `services/mc_operations/plugins/minimoi-tools/tools.json`, shown to the model by `MC_CHAT_AGENTS_SECTION.md` | No record, room, turn or decision read tool among them. `minimoi_github_issues` reads GitHub issues only (spec I08). |
| Records browser bridge: the portal proxies Records for Robert’s signed-in browser session under a Rooms identity binding | `minimoi_portal/records_bridge.py`, `minimoi_portal/rooms_identity.py` | Serves the owner’s browser, not an agent tool. It forwards arbitrary Records paths under Records’ own membership checks; it is not a retrieval contract and MC does not call it. |
| Records machine bridge: default-off, production-only, a fixed allow list of Records routes for hosted teammates (`hosted-teammates` listing, reachability, turn claim/start/heartbeat/fail, room RSVP and events, one document by id, one operation by id) | `minimoi_portal/records_machine_bridge.py` (`ROUTES`) | A worker-protocol surface for turns and room events. No list, search or history route, and `/documents/<id>` needs an id MC would not know. |
| CoS knowledge role: a standing, server-enforced read role for CoS over Robert-visible Records material, with a coverage register | `docs/specs/spec_158_collaboration_rooms_cos_beta_v09_2026-09-21.md` §4.1 | It is CoS’s role by design. Granting it to MC would break the separation §8.4 requires, and the spec states unintegrated domains must not be described as readable. |
| CoS’s bounded source search | `domains/cos/work/retrieval.py` (`search_sources`) | A confined file search for CoS work roots, not a turn or decision reader. |
| Spec 160 daily CoS turn log: `data/cos-turns/YYYY/YYYY-MM-DD.jsonl`, one line per turn, scrubbed, Private mode excluded | `docs/specs/spec_160_agent_memory_owned_by_minimoi_2026-09-28.md` §2 | **CoS only.** Spec 160 states MC’s dialog is not covered by it. Its §10 defines a small reader contract (retrieve one conversation by id, hand it over as fenced data) that was specified as a portability test, not built as an MC tool. |
| MC’s own text-turn capture: `mc_turn` lines at `MC_TURNS_DIR/YYYY/YYYY-MM-DD.jsonl`, written through the same shared writer (`core/agent_turns/writer.py`), with `_status` files | `minimoi_portal/guild_ui/mc/turn_capture.py` | **This is the data source for 0b.** It is opt-in: only the staging overlay sets `MC_TURNS_DIR`; production never does (stated in the file’s own header). Records only answered, live, kept turns; scrubbed; no off-the-record turns. Not exposed to the model. |
| MC timing trace | `minimoi_portal/guild_ui/mc/turn_log.py` (`mc_turns.jsonl`) | Timings and a saved flag only, no text. Not a source. |
| Shop floor conversation and notes stores, exposed to the signed-in owner’s UI | `minimoi_portal/guild_ui/conversations.py`, `/notes` in `minimoi_portal/guild_ui/api.py` | Owner-authenticated UI endpoints for the page, not an agent tool. They hold the live dialog; the capture above is the retention copy. |

So the first-slice gap OpenClaw named is real: native memory tools are denied by design, MiniMoi owns memory, and nothing yet reads that memory back to MC. The September resume defect was exactly this gap.

**Guild decisions: unverified.** I found no dedicated decisions store or API in the code searched (`minimoi_portal/guild_ui`, `domains/guild`, the plugin catalogue). Decisions appear to live in build-queue items, Rooms records and design documents; this note does not assert which. The scope of “Guild decisions” must be fixed by a short source inventory (section 3, step 1) before the tool is designed.

## 2. Sizing the retrieval tool

### What it is

One read-only MiniMoi plugin tool, provisionally `minimoi_recall`, added to `tools.json` and served by the portal beside the existing `minimoi_*` tools (same execution path, same executor token mechanism, same per-tool policy file). Two modes, one tool:

- `find`: bounded keyword search (query, optional date range, limit) returning short excerpts with a stable reference (conversation id, turn id, day).
- `open`: return one referenced conversation or decision record, capped in size.

Results are marked in the tool description and in the output as **data, not instructions**, in the same fencing Spec 160 §10 uses.

### Scope (the safety property)

Allowed sources, and only these:
1. MC’s own conversations: the `mc_turn` capture lines and the owner’s MC conversation records.
2. Guild decisions: the sources fixed by the inventory in section 3, step 1.

Excluded by construction, not by prompt: CoS turns (Spec 160 turn log), personal-agent data, German and Portuguese domain material, credentials, private-mode turns, other principals’ conversations. The tool resolves a fixed list of source roots on the server; the model cannot supply a path, a root or a file name, only a query and filters. Owner-only: it answers only for Robert’s own MC turns, as the existing owner-turn guards do for the other `minimoi_*` tools.

### Negative cross-role tests (A10 gate)

Run against disposable synthetic data, never the owner’s records:
- A planted secret-looking phrase in a CoS turn is never returned by an MC query that would match it.
- A query phrased to reach CoS or Rooms material returns nothing from outside the allowed roots.
- Path-like or glob-like query text does not change the roots searched.
- A private-mode turn (never captured) cannot be found.
- Conversely, the same tool configured for CoS later cannot see MC’s turns (applies when CoS adopts it, row 8; the roots are per role).
- A relevant prior decision and a stated preference are retrieved in a **new** conversation and after the 0a persona update, and a temporary instruction is not turned into a lasting lesson.

### No second memory system

Explicit: this tool reads existing MiniMoi-owned records. It writes nothing, keeps no index of its own that outlives a process (or, if a search index is later needed, rebuilds it from the source and holds no content that the source lacks), adds no store, no sync and no cache that could disagree with the source. It does not enable OpenClaw’s native memory tools, which stay denied. “Keep the thread” is delivered by retrieval from the shared record, not by a new record (spec §6.2).

### Size estimate

Order-of-magnitude, for planning; Codex should challenge it.

| Piece | Work | Estimate |
|---|---|---|
| Source inventory for Guild decisions; fix the roots and record fields | Read-only investigation | 0.5 day |
| Capture availability: `MC_TURNS_DIR` is staging-only, so production MC has no capture to read. Decide whether 0b is dev/staging-first (matches the rest of the rollout) and whether production capture needs its own approval | Decision, then a config flag, not new code | 0.5 day plus Robert’s call |
| Reader module: bounded line scan of the dated JSONL, tolerant of `log_gap` lines, scrub-aware, size caps, date filter, excerpting | New code, tests | 1.5–2 days |
| Plugin tool entry (schema, description, policy tier read-only) and portal route behind the executor token | Follows existing tool pattern | 1 day |
| Negative and positive tests (section above), including A10 | Tests with synthetic data | 1–1.5 days |
| Persona text: one capability line in the portable AGENTS and one runtime-binding entry (release 0.2, not 0.1) | Small | 0.25 day |
| Codex review and fixes | | 1 day |

Roughly 5–7 working days of build, plus review, with the main uncertainty in the Guild-decisions inventory and the production-capture decision. A plain keyword scan is enough for the first version; embeddings or a search index are not proposed, because they would start to look like a second memory system.

## 3. Proposed order

1. Source inventory for “Guild decisions” and a short list of the exact files or endpoints that hold them (read-only; reported back to Robert and Codex).
2. Decide where 0b runs first (dev/staging, where capture is on) and whether production capture is wanted; this is a separate approval, not assumed.
3. Build the reader and tool behind a default-off flag; negative tests first.
4. Persona release 0.2 adds the capability line; A10, A11 and A06 are the acceptance checks.
5. Ships when ready. It does not hold Slice 0a, and Slice 0a does not claim it.

## 4. Open points

- Guild decisions source: unverified, step 1.
- Production capture of MC turns: not enabled today; needs a decision.
- Whether the tool should also expose the Records/Rooms side (rooms where MC is a member) is deliberately left out of 0b; it would need its own scope and the CoS read-role separation resolved first.

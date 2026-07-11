# Spec: CoS Domain Extraction

**File:** `spec_cos_domain_extraction_2026-07-11.md`
**Status:** Spec Ready — for Claude Code plan mode
**Build queue #:** TBD — assign at registration
**Date:** 2026-07-11
**Author:** Claude.ai design session (Fable 5)
**Relationship to spec #133:** Prerequisite. Spec #133's Phase 1 containerizes CoS; this extraction must complete first so containerization wraps a standalone domain, not code still nested in Guild's import tree. Once done, this becomes "Phase 0" in spec #133's record and spec #133 gets a reference line added — not folded in wholesale, this stays its own doc.

---

## Intent

`chief_of_staff.py` currently lives at `domains/guild/agents/chief_of_staff.py` — structurally a child of the Guild domain, even though it already runs independently in practice (own bot, own Flask port, own polling process). This spec moves it to a standalone `domains/cos/` domain and relocates its memory file to a platform-owned location, so CoS is what its runtime behavior already suggests it is: its own thing.

**Deliberately narrow scope.** This is a relocation, not a redesign. Not included, and not to be combined into this pass:
- Memory format upgrade (`cos_memory.md` → structured `cos_memory.json`) — future work, tracked separately
- `guild.cos_agenda` Postgres schema rename — future work, tracked separately
- Any change to chat logic, tool calls, scheduled loops, or Grok integration — untouched
- Any change to `minimoi_cos_bot` or `rvsopenbot` bot identity/tokens — untouched

---

## What's Not Changing

- Both bots' external behavior (`minimoi_cos_bot` via `telegram_cos_bot.py`, and whatever remains of the `rvsopenbot` polling thread)
- Memory content and format — same `.md`, same 7,500-char cap, same `_append_memory()` / `_maybe_update_memory()` logic, just a new file path
- Postgres `guild.cos_agenda` — stays as-is, flagged as known follow-up
- Flask service on port 8769 — unchanged
- Guild's four-cabinet dashboard — unchanged in code; charter updated to note CoS graduated out (see step 6)

---

## Pre-Flight — run first, report findings before proceeding

**This is the plan-mode deliverable. Do not execute steps 1+ (Execution) until these are answered and reported back.**

1. Grep the full repo for every reference to `domains.guild.agents.chief_of_staff` and `guild.cos_agenda` outside of `chief_of_staff.py` and `telegram_cos_bot.py` themselves. Specifically check: Guild's dashboard/view code, any Guild aggregation or reporting scripts, `html_server.py` (known to reference `rvsopenbot` for German notifications — confirm it doesn't also touch CoS internals).
2. Confirm whether the tool calls inside `chief_of_staff.py` (ops status, ops log, domain health) are read-only or have any mutate/write capability. Quote the relevant function code, don't summarize.
3. Confirm whether `guild.cos_agenda` is ever auto-executed by anything downstream, or is strictly read by Robert/Guild UI for manual action. Quote the code path that reads from it.
4. Confirm whether the `rvsopenbot`-thread suppression inside `chief_of_staff.py` (mentioned as active when `telegram_cos_bot.py` handles polling separately) is a hard conditional in code, given both processes run simultaneously right now (PID 6413 and 6426 confirmed live). Quote the conditional.
5. Report back: clean cut (nothing outside these two files references the old path) or needs-a-shim (something else does, and what).

**Do not proceed past this section without those five answers reported.**

---

## Execution — proceed only after Pre-Flight is reported and reviewed

1. Create `domains/cos/` — new top-level domain, sibling to `curator/`, `german/`, `portuguese/`, `build/`, `guild/`
2. `git mv domains/guild/agents/chief_of_staff.py domains/cos/chief_of_staff.py` — preserve history
3. Update the import in `telegram_cos_bot.py`: `domains.guild.agents.chief_of_staff` → `domains.cos.chief_of_staff`
4. If Pre-Flight step 1 found any other references, update those too (shim or direct fix, per what was found — do not proceed with a silent break)
5. Relocate the memory file to a platform-owned path — not nested under `domains/cos/`, sitting alongside `build_queue.json` at platform root (e.g. `/opt/minimoi/cos_memory.md` on EC2; project-root-relative equivalent on Mac). Update the path reference(s) in `_append_memory()` / `_maybe_update_memory()`. Same file, same format — just moved. This is intentional: memory must outlive any single agent implementation behind CoS (agent-agnostic memory principle, spec #133), so it cannot live inside the domain directory of whichever code currently happens to be there.
6. Amend `GUILD_CHARTER.md`: one entry noting CoS graduated from the four-cabinet model into its own domain as of this spec, dated, same convention used for recording the Curator Tech placement decision.
7. Restart the launchd-managed processes under new paths.

---

## Verification

Run all of these before declaring done — this is also where the three Pre-Flight findings (steps 2–4 above) get confirmed under real conditions, not just read from code:

1. `minimoi_cos_bot` responds correctly via `telegram_cos_bot.py` — send a test message, confirm normal `_chat` behavior
2. Memory read/write confirmed at new path — send a message expected to trigger `_maybe_update_memory`, confirm entry lands in the relocated file, confirm old path is no longer written to
3. `rvsopenbot`-thread suppression still behaves as before with both processes running — confirm no double-response to a single message
4. Flask endpoints (`/chat`, `/status`, `/health`, `/loops`, `/event`) all respond on 8769, unchanged
5. Scheduled loops (build discipline, guest nudge, EC2 health, career/German/curator/novelty watches) still fire — spot-check at least one
6. Guild dashboard/views (if any reference CoS data) render without error
7. `guild.cos_agenda` still receives writes, unchanged in schema or content
8. No import errors, no orphaned references to the old `domains.guild.agents.chief_of_staff` path anywhere in the repo (re-grep to confirm clean)

---

## Definition of Done

- [ ] Pre-Flight findings reported and reviewed before execution began
- [ ] `domains/cos/chief_of_staff.py` exists; old path removed (via `git mv`, history preserved)
- [ ] All imports/references updated; re-grep confirms none remain pointing at the old path
- [ ] Memory file relocated to platform-owned path; old path no longer written to
- [ ] `GUILD_CHARTER.md` amended
- [ ] All 8 verification checks pass
- [ ] Both bots behave identically to pre-extraction, confirmed by direct test, not assumption
- [ ] Robert confirms CoS is usable and unchanged in behavior from Robert's side

---

## Commit

| Item | Location | Actor |
|---|---|---|
| This spec | `docs/specs/spec_cos_domain_extraction_2026-07-11.md` | Claude Code |
| Pre-flight findings | `agent_logs/claude_code_2026-07-11_[time].md` | Claude Code |
| `build_queue.json` | New entry, status spec_ready → in_progress → done | Claude Code |
| `GUILD_CHARTER.md` amendment | Committed as part of Execution step 6 | Claude Code |
| Spec #133 | Add one reference line noting this extraction as completed prerequisite | Claude.ai, next session |

Registration and execution approval: Robert.

---

*Spec: CoS Domain Extraction · 2026-07-11 · Claude.ai (Fable 5)*
*For Claude Code plan mode — Pre-Flight findings to be reviewed before Execution proceeds*

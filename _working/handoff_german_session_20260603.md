# Handoff — German Domain Bug Fixes + Grok Voice Flow
*2026-06-03 · Claude Code session · For next agent reading this*

---

## Way of Working — Testing Protocol (NEW — enforced from this session)

**If you cannot run an end-to-end test, say so explicitly.**
Do not commit and say "done." Flag it: "I cannot test this — please test or say proceed."

Major changes require one of:
- A real test you can run (curl, pytest, direct invocation)
- Robert testing it live and confirming
- Robert explicitly saying "proceed without test"

Unit tests (run_tests.py, research_uat.py) are always run before committing.
End-to-end UI/bot flows must be tested by Robert — Claude cannot open browsers or Telegram.

---

## Session context

Build plan: `_working/BUILD_PLAN_2026-06-03.md` (approved, in progress).
Branch: `main`. All changes committed.

Phase 1b (full Grok Voice prompt delivery) revealed a chain of production bugs
in the German domain that had been latent since the 2026-05-30 domain promotion.
All fixed and end-to-end verified by Robert.

---

## Bugs found and fixed (B-022, B-023 + related)

### B-022 — Stale `german_domain.pyc` at repo root
**Symptom:** `telegram_bot.py` loaded old GERMAN_BASE (`_NewDomains/language-german/`).
Voice commands failed: `get_german_session.py: No such file or directory`.
**Root cause:** After the 2026-05-30 domain promotion, `german_domain.py` moved to
`domains/german/` but its compiled `.pyc` remained in `__pycache__/` at repo root.
Python loaded the ghost bytecode instead of the real file.
**Fix:** Deleted `__pycache__/german_domain.cpython-314.pyc`. Added explicit
`sys.path.insert(0, str(_REPO_ROOT / "domains" / "german"))` in `telegram_bot.py`
before the `from german_domain import` — ensures correct module regardless of cache.
**File:** `telegram_bot.py` (lines ~24-30)

### B-023 — `--base-dir language/german/` in telegram_bot.py (11 calls)
**Symptom:** reviewer.py crashed on every transcript submission:
`domain_cfg_path.read_text()` → file not found.
**Root cause:** All subprocess calls to German pipeline scripts used
`--base-dir language/german/` — the old path relative to repo root.
With `cwd=GERMAN_BASE=domains/german/`, the resolved path was
`domains/german/language/german/` which doesn't exist.
`--base-dir` should point at the data directory: `data/`
(so `base / "config" / "domain.json"` → `domains/german/data/config/domain.json`).
**Fix:** `sed -i 's|"language/german/"|"data/"|g' telegram_bot.py` — all 11 calls.
**File:** `telegram_bot.py`

### Telegram button silent failure
**Symptom:** Clicking ✈️ Telegram öffnen on Gespräche page — nothing happened.
**Root cause 1:** Click handler referenced `currentPersonaIdx` (never declared).
ReferenceError thrown before toast or window.open. The actual variable is `selectedIdx`.
**Root cause 2:** `window.open()` called after `await fetch()` — browser loses
user-gesture context after async operations and blocks popups.
**Fix:** Changed to `selectedIdx`. Restructured click handler to be synchronous:
`window.open('tg://...')` fires immediately on click, API call runs as fire-and-forget `.then()`.
**File:** `domains/german/templates/german_gesprache.html`

### Conflicting start trigger in persona files
**Symptom:** Grok started the session immediately without waiting for "Start session."
**Root cause:** All 9 persona `.txt` files ended with "Ready when I give the start signal."
— a weaker phrase that contradicted the strict rule in UNIVERSAL_HEADER rule #5.
**Fix:** Stripped the line from all 9 files. UNIVERSAL_HEADER is the single source
of truth for session start timing.
**Files:** `domains/german/data/config/prompts/*.txt` (9 files)

---

## New features built (Phase 1b)

### Full Grok Voice system prompt delivery
`assemble_session_prompt()` in `german_domain.py` previously ignored the `scene`
parameter — Copy/Save delivered only the brief scene stub (~3 lines), not a usable prompt.

**Fix:** Scene text is now injected as `=== SCENARIO FOR THIS SESSION ===` block
between persona text and UNIVERSAL_FOOTER. Warm_up_* variants also resolved.
A `ROLES:` anchor line is injected immediately after the header:
`"ROLES: You are Frau Berger. The learner you are speaking with is Robert."`

**Also added to UNIVERSAL_FOOTER:** Voice-trigger end-session instruction.
PREFERRED path: stop voice manually, type "End session. Give me the transcript."
VOICE TRIGGER: if Robert says "end session" in voice mode, Grok instructed to
stop, exit silently, write transcript as text without reading aloud.

### Telegram prompt delivery (server-side)
New endpoint: `POST /api/send-to-telegram` in `html_server.py`.
Assembles full prompt, sends to Robert's chat via rvsopenbot (bot_token).

**Bot token clarification (important — do not change without reading this):**
| Bot | Keyring | Role |
|-----|---------|------|
| rvsopenbot | `telegram/bot_token` | Outbound delivery only — no polling, no message handler. CORRECT choice for prompt delivery — cannot misinterpret messages as commands. |
| minimoi_cmd_bot | `telegram/polling_bot_token` | Active command bot — DO NOT use for prompt delivery, its handler would try to process the prompt as a German command. |
| minimoi_agent_bot | separate key | OpenClaw gateway — do not use for German/Curator delivery. |

### Telegram deep-link button
`tg://resolve?domain=minimoi_cmd_bot` opens native Telegram app.
3s fallback: `https://t.me/minimoi_cmd_bot` if app not installed.
Using `window.open()` not `window.location.href` — keeps current page visible.
64-char hard limit on Telegram `?start=` param — never pass prompts that way.

---

## End-to-end test result (Robert, 2026-06-03)

Full flow verified:
1. Select Frau Berger → bakery_order on Gespräche page
2. Click ✈️ Telegram öffnen → prompt sent to rvsopenbot, native app opened
3. Copy prompt on mobile → paste into Grok Voice → session with Frau Berger
4. Copy transcript → paste into minimoi_cmd_bot
5. reviewer.py ran: errors noted, Anki cards generated, lesson plan created
6. Status confirmed: Session 68, 274 Anki cards, scaffold tracking working

---

## Where we are in the build plan

`_working/BUILD_PLAN_2026-06-03.md` — Track A, Stop-Gate 1 in progress.

**Completed today:**
- B-020, B-021 (test hygiene)
- B-022, B-023 (production bugs)
- Phase 1b (Grok Voice prompt delivery — core loop)

**Remaining Track A before Stop-Gate 1:**
- Phase 1a: Landing page link `/ueben` → `/gesprache`, label "Üben" → "Gespräche"
  File: `domains/german/templates/german_landing.html` — 5 min
- Phase 1c: Scene button German labels — add `labels` map to 8 persona JSON files
  File: `domains/german/data/config/personas/*.json` — 10 min

**After Stop-Gate 1:** Phase 2 (Curator subnav + dormant routing), then Track B (DB spine).

---

*Written by Claude Code. Robert to hand to OpenClaw for validation.*

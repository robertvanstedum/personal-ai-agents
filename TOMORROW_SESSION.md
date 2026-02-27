# Next Session — Phase 2B remainder + Phase 3A start

## Phase 2B Status (completed Feb 27 by OpenClaw)

✅ **Task 1: Ratings → Scoring** — `load_user_profile()` was already implemented and working. 13 feedback interactions actively personalizing Grok. Strong signals: The Duran (11 likes), institutional_debates, monetary_policy, analytical style.

✅ **Task 2: Serendipity Reserve** — Wired. `serendipity_reserve: 0.20` in `curator_preferences.json`. 80% personalized / 20% discovery. Tested: 4/20 articles selected as serendipity picks from 374 candidates. Commit: `90cc3d0`.

⏸️ **Task 3: Temporal Decay** — Correctly deferred. At 13 interactions, decay would over-penalize early signal. Revisit at 30-50 interactions. Design is in `CURATOR_FEEDBACK_DESIGN.md`.

---

## Next Session Prompt

> "Phase 2B is done except temporal decay (deferred — only 13 interactions). Read TOMORROW_SESSION.md first. Start with temporal decay gate check, then move to Phase 3A: OAuth 1.0a setup for X integration."

---

## Task Order

### 1. Temporal Decay gate check *(5 min)*

Check current interaction count in `curator_preferences.json` → `learned_patterns.sample_size`.
- If < 30: skip, note count, move on
- If ≥ 30: wire `decay_factor = 0.85/week` into `load_user_profile()` per `CURATOR_FEEDBACK_DESIGN.md`

### 2. Phase 3A — OAuth 1.0a setup for X *(longest lead-time item)*

Auth setup independently of adapter code. See `ROADMAP_X_INTEGRATION.md` in `rvs-openclaw-agent`.

- OAuth 1.0a first — bookmarks endpoint requires user context auth, not Bearer token
- Store credentials in macOS keychain (same pattern as other API keys)
- Verify auth works before writing any fetch logic
- Do NOT start `x_adapter.py` until auth is confirmed working

**Verify after each task.**

---

## If Time Allows

**Quick win first (~20 min): Telegram session commands**

Add `/reset` and `/status` commands — solves the $0.52/message session bloat problem immediately.

- `/reset` → sends "compact and reset session" to OpenClaw polling path; on webhook path, session is already stateless so reply "Session is stateless in webhook mode."
- `/status` → enhanced: show token count estimate + cost/msg alongside existing log lines. Target output: `Session: ~23k tokens | ~$0.12/msg | Last run: 07:02`
- Also add voice equivalents: "reset session" and "session status" to `VOICE_COMMAND_PATTERNS`

**Deeper fix (later session): Option B — TELEGRAM_CONTEXT.md**

Stateless Telegram sessions by design. Each command reads a small context file, executes, closes. Session never bloats because it never persists. Makes `/reset` unnecessary long-term.

**Then: Phase 3A OAuth 1.0a setup for X integration**

Getting auth working is the longest lead-time item and can be done independently of adapter code.
See `ROADMAP_X_INTEGRATION.md` (in `rvs-openclaw-agent`) — "OAuth 1.0a first" note at bottom.

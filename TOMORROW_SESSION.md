# Next Session — Telegram architecture decision + Phase 3A

## Phase 2B Status (completed Feb 27 by OpenClaw)

✅ **Task 1: Ratings → Scoring** — `load_user_profile()` was already implemented and working. 13 feedback interactions actively personalizing Grok. Strong signals: The Duran (11 likes), institutional_debates, monetary_policy, analytical style.

✅ **Task 2: Serendipity Reserve** — Wired. `serendipity_reserve: 0.20` in `curator_preferences.json`. 80% personalized / 20% discovery. Tested: 4/20 articles selected as serendipity picks from 374 candidates. Commit: `90cc3d0`.

⏸️ **Task 3: Temporal Decay** — Correctly deferred. At 13 interactions, decay would over-penalize early signal. Revisit at 30-50 interactions. Design is in `CURATOR_FEEDBACK_DESIGN.md`.

---

## Next Session Prompt

> "Read TOMORROW_SESSION.md. Start with the Telegram architecture decision (Option A vs B — see NOTEBOOK.md). Once decided, implement and verify end-to-end. Then temporal decay gate check, then Phase 3A OAuth 1.0a for X."

---

## Task Order

### 0. Telegram Architecture Decision *(15 min, decide before coding)*

OpenClaw correctly diagnosed this as an architecture problem, not a bug fix.
Full analysis in `rvs-openclaw-agent/NOTEBOOK.md` → "Telegram Architecture Decision".

**Option A — Single webhook, Flask handles everything:**
- Flask webhook active permanently (`allowed_updates: ['callback_query', 'message']`)
- Flask dispatches: callbacks → feedback, text commands → handle, voice → Whisper
- OpenClaw text commands come through webhook (Flask → OpenClaw agent via API?)
- Pro: one handler, no token conflict. Con: Flask must handle OpenClaw's conversational commands.

**Option B — Two bot tokens:**
- Original token → Flask webhook, `allowed_updates: ['callback_query']` only (feedback buttons)
- New token → OpenClaw Telegram channel (text commands, conversational)
- Create second bot via BotFather, assign to OpenClaw config
- Pro: clean separation. Con: two bots in Telegram, user must know which to use.

**Recommended: Option B.** Clean, no conflicts, each tool owns its interface. Voice deferred until text is solid.

**Current working state (don't break):**
- Flask webhook receives all message types ✅
- `/status`, `/run`, `/briefing` commands work ✅
- Like/Dislike/Save callbacks work ✅
- `start_telegram_webhook.sh` toggles OpenClaw on/off via `openclaw config set` ✅
- Voice threading fix committed (`5a2e974`) — untested but correct ✅

### 1. Temporal Decay gate check *(5 min)*

Check `curator_preferences.json` → `learned_patterns.sample_size`.
- If < 30: skip, note count, move on
- If ≥ 30: wire `decay_factor = 0.85/week` per `CURATOR_FEEDBACK_DESIGN.md`

### 2. Phase 3A — OAuth 1.0a setup for X

See `ROADMAP_X_INTEGRATION.md` in `rvs-openclaw-agent`.
- OAuth 1.0a first — bookmarks endpoint requires user context auth
- Store in macOS keychain
- Verify auth before writing `x_adapter.py`

**Verify after each task.**

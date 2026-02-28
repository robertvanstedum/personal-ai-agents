# Next Session — Telegram architecture decision + Phase 3A

## Phase 2B Status (completed Feb 27 by OpenClaw)

✅ **Task 1: Ratings → Scoring** — `load_user_profile()` was already implemented and working. 13 feedback interactions actively personalizing Grok. Strong signals: The Duran (11 likes), institutional_debates, monetary_policy, analytical style.

✅ **Task 2: Serendipity Reserve** — Wired. `serendipity_reserve: 0.20` in `curator_preferences.json`. 80% personalized / 20% discovery. Tested: 4/20 articles selected as serendipity picks from 374 candidates. Commit: `90cc3d0`.

⏸️ **Task 3: Temporal Decay** — Correctly deferred. At 13 interactions, decay would over-penalize early signal. Revisit at 30-50 interactions. Design is in `CURATOR_FEEDBACK_DESIGN.md`.

---

## Next Session Prompt

> "Read TOMORROW_SESSION.md and docs/FEATURE_TELEGRAM_ARCHITECTURE.md. Start with Phase 1 of the Telegram architecture spec — feedback bot webhook only, second bot token for OpenClaw. Then temporal decay gate check, then Phase 3A OAuth 1.0a for X."

---

## Task Order

### 0. Telegram Architecture — implement the spec *(Phase 1 only this session)*

**Full spec:** `docs/FEATURE_TELEGRAM_ARCHITECTURE.md` — read this before writing any code.

**The design (OpenClaw spec):**
- `telegram_feedback_bot.py` → buttons ONLY, webhook mode, always-on via launchd
- OpenClaw Telegram channel → text + voice commands (Telegram transcribes voice → text automatically for Premium users, no Whisper needed)
- Outbound briefings → direct API call from `curator_rss_v2.py`, no OpenClaw involved
- `TELEGRAM_CONTEXT.md` → OpenClaw reads 50-line context file instead of full session history

**⚠️ One gap in the spec to resolve first:**
OpenClaw's Option A says "webhook for feedback bot, OpenClaw polls for text — no conflict". This is WRONG if both use the same bot token — Telegram returns 409 for `getUpdates` when any webhook is active.
**Actual fix:** Two bot tokens (Option B in the spec). Create a second bot via BotFather for OpenClaw. 5 minutes of setup, eliminates the conflict permanently.

**Phase 1 tasks (from spec):**
1. Fix `telegram_feedback_bot.py` — callback_query ONLY, webhook mode, strip text/voice handlers
2. Fix `CURATOR_CALLBACKS.md` path lookup (search `rvs-openclaw-agent/` first)
3. Verify buttons work cleanly end-to-end
4. Create second bot token for OpenClaw (resolves the token conflict)

**Current working state (don't break):**
- Flask webhook receives all message types ✅
- `/status`, `/run`, `/briefing` commands work ✅
- Like/Dislike/Save callbacks work ✅
- `start_telegram_webhook.sh` toggles OpenClaw on/off via `openclaw config set` ✅
- Voice threading fix committed (`5a2e974`) — superseded by Telegram native transcription approach

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

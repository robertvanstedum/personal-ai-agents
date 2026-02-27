# Next Session — Phase 2B

## Session Prompt

> "Let's wire up Phase 2B — ratings into scoring, serendipity reserve, then temporal decay.
> Read TOMORROW_SESSION.md and CURATOR_FEEDBACK_DESIGN.md first. Work in that order."

---

## Task Order

### 1. Ratings → Scoring *(do first — most impactful)*

Deep dive ratings in `interests/ratings.json` boost/suppress future article scores.

- Same pattern as `load_user_profile()` — read ratings, inject into Grok prompt
- Rating scale: 1 (useless) → 4 (excellent/contrarian depth)
- Map to score modifier: rating 4 → boost related themes/sources; rating 1-2 → suppress
- `ratings` array is currently empty — wire the logic, test with a dummy entry first
- Verify with a live dry-run after wiring

### 2. Serendipity Reserve *(before temporal decay — safer on small dataset)*

Wire `serendipity_reserve = 0.20` into `score_entries_xai()`.

- 20% of final article slots bypass personalization scoring entirely
- Domain scope = RSS feed universe for now, no additional filtering
- Selected randomly from the non-top-80% pool (not from already-top-ranked articles)
- Creates `curation_settings.json` if it doesn't exist
- Verify with dry-run after wiring

### 3. Temporal Decay *(last — needs signal volume to absorb safely)*

Wire `decay_factor = 0.85/week` into `load_user_profile()`.

- Apply to `learned_patterns` weights before building the profile string
- Weight = raw_weight × (0.85 ^ weeks_since_last_interaction)
- Timestamp source: `last_updated` field in `curator_preferences.json`
- Hold until ~30-50 interactions; currently at ~6 — wire it but keep an eye on signal erosion
- Verify with dry-run after wiring

**Verify after each task — dry-run between each one, not just at the end.**

---

## If Time Allows

Start Phase 3A OAuth 1.0a setup for X integration.
Getting auth working is the longest lead-time item and can be done independently of adapter code.
See `ROADMAP_X_INTEGRATION.md` (in `rvs-openclaw-agent`) — "OAuth 1.0a first" note at bottom.

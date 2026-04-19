# BUILD — Curator v1.1.5: Scoring Tweaks
**Date:** April 18, 2026
**Built by:** Claude Code
**Reviewed by:** OpenClaw (Memory Agent) — add pre-conditions below before saving

---

## What Was Planned

Three targeted fixes to `curator_rss_v2.py`, specified in the Claude Code handoff from OpenClaw:

- **Tweak A**: Neutral recency score (50) for X posts with no publish date
- **Tweak C**: Tighten probationary source multiplier from 0.7 → 0.6
- **Tweak B**: Halve the diversity penalty for trusted-tier sources in the selection loop

Order: A → C → B. A/B test after each before proceeding.

---

## Pre-conditions Completed

- OpenClaw audit (April 18, 2026): confirmed `_load_source_trust()` and `_TRUST_MULTIPLIERS` already existed — no new loader needed
- OpenClaw confirmed `drop` tier already filtered upstream at line 1581 — Tweak C `continue` branch omitted by design
- OpenClaw confirmed `curator_sources.json` `trust` field already populated for all 60 sources — no new config required
- A/B test designed and executed by OpenClaw prior to this commit

---

## What Was Built

### Files Modified

**`curator_rss_v2.py`**

- **Tweak A** (`score_entry_mechanical()`, ~line 277): Added `else: recency_score = 50` to the `if entry["published"]:` block; moved `raw_score += recency_score` outside so it fires in both branches. Previously, entries without a publish date received 0 recency contribution (branch skipped entirely) — which in practice left undated X posts at an arbitrary rank.

- **Tweak C** (line 1419, `_TRUST_MULTIPLIERS`): Changed `'probationary': 0.7` → `'probationary': 0.6`. Value-only change; no structural changes to trust logic.

- **Tweak B** (~line 1707, diversity selection loop in `curate()`): Added three lines before `source_penalty` calculation to look up domain trust tier and apply a `0.5` discount for trusted-tier domains:
  ```python
  domain = _domain_from_url(entry.get('link', ''))
  trust = _source_trust.get(domain, 'neutral') if _source_trust else 'neutral'
  discount = 0.5 if trust == 'trusted' else 1.0
  source_penalty = (source_count ** 2) * 30 * diversity_weight * discount
  ```
  Uses `_source_trust` (already in scope from line 1582) and `_domain_from_url()` (module-level). No second loader added.

**`CHANGELOG.md`** — v1.1.5 entry added with fixes and A/B test results.

**`PROJECT_STATE.md`** — Version bumped to v1.1.5, last-updated date updated.

### Implementation notes

The handoff spec used pseudo-variable names (`source_trust_map`, `article_domain`) in Tweak B and `if age_hours is None` in Tweak A. Both were adapted to match actual codebase variable names without changing intent.

---

## Confirmed Working Output

A/B test run by OpenClaw on April 18, 2026 against f36f61c baseline (live pool, grok-4-1, temp=0.7, `--dry-run`):

- **Tweak A**: `X/@IranSpec` and `X/@__Injaneb96` (undated) dropped out of top 20 ✅
- **Tweak B**: Treasury MSPD +3 (#10→#7), X/@KobeissiLetter +5 (#17→#12), The Big Picture +1 ✅
- **Tweak C**: 6 new entrants from stronger sources replaced suppressed probationary content ✅
- Top 5 identical in both runs — no regression ✅

Live from 7 AM run April 19, 2026.

---

## Design Decisions Made During Build

- **Tweak B uses `_source_trust` directly, not a second load call.** The trust table is already loaded at line 1582 and is in scope throughout `curate()`. Adding a second `_load_source_trust()` call would be redundant and inconsistent with the pattern.
- **`discount` is binary (0.5 or 1.0), not a gradient.** Keeps the logic simple and predictable. Can be refined later if mid-tier sources (deprioritize/probationary) need their own discount curve.

---

## Cost

No new API calls introduced. No cost impact.

---

## Open Items Carried Forward

- **Issue #12**: X handle-level trust filtering not supported — `_domain_from_url()` returns bare `x.com` for all X URLs, so per-handle entries in `curator_sources.json` never match. Recommended fix: handle blocklist in `x_to_article.py`. Known account to block: `X/@samdblond` (startup promo content, surfaced at #19).
- **Bug #8**: Telegram save callback silent on NetworkError at startup — still open from prior sprint.

---

## Next Phase Scope

Per `PROJECT_STATE.md` and `ROADMAP.md` — nothing currently approved to build. Candidates for next session (Robert decides):

- Stale doc cleanup (root + docs/ — ~25 files to archive/delete)
- Bug #8 fix
- Issue #12 (X handle blocklist)
- Infrastructure upgrades from ROADMAP.md near-term list

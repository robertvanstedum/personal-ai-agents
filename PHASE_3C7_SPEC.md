# Phase 3C.7 — Incremental X Bookmark Pull

**Goal:** Fetch new X bookmarks since last pull, enrich with destination text, add to daily scoring pool without reprocessing the 398 historical archive signals.

**New file:** `x_pull_incremental.py`  
**New state file:** `x_pull_state.json` (project root, gitignored)

---

## Preconditions (do not modify, do not backfill)

- 398 historical signals already in `domain_signals.json`
- Tweet-only signals (no destination URL): `destination_text` intentionally absent — nothing to fetch
- URL signals (29): `destination_text` populated, `destination_text_source` set
- Backfill complete. Treat existing signals as **read-only**.

---

## Piece 1 — State file + pull logic

### `x_pull_state.json` schema

```json
{
  "last_pull_at": "<ISO8601 UTC or null on first run>",
  "last_pull_count": 0,
  "total_incremental_pulled": 0
}
```

- Create on first run if missing (`last_pull_at: null`)
- Update atomically at end of each successful run

### Pull logic

1. Load `x_pull_state.json`, read `last_pull_at`
2. Call X Bookmarks API (`GET /2/users/:id/bookmarks`) — paginate through all results
3. For each bookmark: check URL against existing `domain_signals.json` keys
   - URL exists → skip (covers all 398 historical + any prior incremental pulls)
   - URL new → enrich + add
4. Optimization (not guaranteed): stop pagination early if `last_pull_at` is set AND bookmark `created_at` < `last_pull_at` — dedup is the safety net, not this check
5. Write new signals to `domain_signals.json`
6. Update `x_pull_state.json` with `last_pull_at` = now, updated counts

### Enrichment

Call `fetch_destination_text()` from `curator_utils.py` — inline during pull, not batch.  
Set `destination_text_source` on every new signal regardless of outcome.

Fields per new signal: match existing 398 schema exactly. No new fields, no schema drift.

---

## Piece 2 — Integration with daily cron

*(Only after Piece 1 tested and confirmed)*

**`run_curator_cron.sh` update:**
- Add `python x_pull_incremental.py` call **before** `curator_rss_v2.py`
- New bookmarks land in `domain_signals.json` → Piece 2/3 pipeline picks them up automatically
- Failure in pull → log + continue, **never block the briefing**

---

## Test sequence

1. `--dry-run`: print what would be fetched/added, touch nothing
2. `--limit=5`: real run, 5 new bookmarks max — inspect signals output + state file
3. Confirm schema matches existing 398 historical signals
4. Full run
5. Confirm `x_pull_state.json` updated correctly (`last_pull_at`, counts)
6. Run `curator_rss_v2.py --dry-run` — confirm new signals appear as candidates

---

## Out of scope

- Re-enriching the 398 historical signals (complete, read-only)
- Issue #5 (feedback attribution) — deferred to post-3C.7 production observation
- Backfill for gaps if X API rate-limits (future hardening pass)

---

## Protocol

Follow CLAUDE.md step-by-step: build Piece 1, test, confirm, then Piece 2. No combining steps. Commit after each piece is tested and confirmed.

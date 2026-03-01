# Phase 3C — X Adapter / Domain Signals

**Goal:** Learn WHAT content ecosystem your trusted X curators point to (ft.com, nationalreview.com, etc.) and inject that as a scoring signal into the daily curator briefing — scoped by knowledge domain.

---

## Architecture

### Key Principle: One domain = one independent product
Each knowledge domain has its own front-end, its own schedule, and its own `domain_signals` bucket. The daily morning briefing currently runs only on **Finance and Geopolitics**. Other domains are built out later as needed.

### Storage structure (`curator_preferences.json`)
```json
"learned_patterns": {
  "domain_signals": {
    "Finance and Geopolitics": {
      "zerohedge.com": 14,
      "nationalreview.com": 9,
      "peterturchin.com": 8
    },
    "Health and Science": { ... added later ... },
    "Tech and AI":         { ... added later ... }
  }
}
```

### Scoring logic
- `curator_weight` = number of bookmarks saved from that X account (more saves → more trust)
- Cap: max 3 unique domains counted per curator (prevents one prolific linker from dominating)
- `domain_signals` scores are additive across runs (new bookmarks accumulate over time)

---

## Files

| File | Purpose |
|---|---|
| `curator_config.py` | **Single source of truth.** `DOMAINS`, `ACTIVE_DOMAIN`, `KNOWN_FOLDERS` |
| `x_import_archive.py` | **Phase 3C-alpha.** One-time/ad-hoc import from X data archive (`bookmarks.js`) |
| `x_adapter.py` | **Phase 3C-beta (deferred).** Incremental API-based updates (weekly/monthly) |
| `curator_rss_v2.py` | Reads `domain_signals[ACTIVE_DOMAIN]` and injects into LLM scoring prompt |

---

## curator_config.py — Change here, not in scripts

```python
DOMAINS = [
    "Finance and Geopolitics",   # Active — morning briefing
    "Health and Science",        # Future
    "Tech and AI",               # Future
    "Language and Culture",      # Future
    "Career and Commercial",     # Future
    "Other",
]

ACTIVE_DOMAIN = "Finance and Geopolitics"   # ← change this to switch briefing domain

KNOWN_FOLDERS = {
    '1926124453714387081': 'Finance and Geopolitics',
    '1881118951536538102': 'Language and Culture',
    '1926123095779078526': 'Health and Science',
    '1967313159158640645': 'Tech and AI',
    '1992980059464876233': 'Career and Commercial',
}
```

**Unknown folder IDs fall back to `ACTIVE_DOMAIN`.** Add new X folder IDs here as you create them.

---

## Phase 3C-alpha: Archive Import (x_import_archive.py)

### When to run
- **Tuesday** (first time): when X archive download arrives
- **Ad hoc**: after requesting a fresh X archive download
- **Not automated** — manual trigger only

### Tuesday workflow
```bash
# 1. Extract archive, copy bookmarks.js to project
cp ~/Downloads/twitter-archive/data/bookmarks.js .

# 2. Dry run — see what would be written
python3 x_import_archive.py --file=bookmarks.js --dry-run

# 3. Verbose run — see domain breakdown per folder
python3 x_import_archive.py --file=bookmarks.js --dry-run --verbose

# 4. Commit (write domain_signals)
python3 x_import_archive.py --file=bookmarks.js --verbose

# 5. Verify
python3 show_profile.py
python3 curator_rss_v2.py --dry-run --model=xai   # check LLM prompt includes domain line
```

### What it does
1. Parses `bookmarks.js` (strips JS wrapper, extracts JSON)
2. Shows folder distribution across all bookmarks
3. Groups tweet IDs by canonical domain (Finance/Geo, Health, etc.)
4. Batch-fetches tweet entities via OAuth 1.0a (100/call, 4-5 calls for ~400 tweets)
5. Extracts URL domains, filters noise (youtube, t.co, substack, github, etc.)
6. Scores by curator weight, writes to `domain_signals[domain_label]`

### Noise filter (`SKIP_DOMAINS`)
`x.com, twitter.com, t.co, youtube.com, youtu.be, substack.com, bit.ly, tinyurl.com, buff.ly, github.com`

---

## Phase 3C-beta: Incremental Updates (x_adapter.py) — DEFERRED

Runs on a weekly/monthly cadence to pick up new bookmarks added since the last archive.
Uses the X bookmark folder API + deduplication cache to avoid re-processing.

**Status:** Deferred until archive import is verified working.

**Known limitation:** X bookmark folder API hard-caps at 20 results per call, rejects
`max_results` parameter (HTTP 400). Bug filed with @XDevelopers.
Workaround: x_import_archive.py (full archive, no API limit).

---

## LLM Prompt Line (curator_rss_v2.py)

When `domain_signals["Finance and Geopolitics"]` has entries scoring 2+, the LLM
scoring prompt includes:

```
- Content domains from trusted X curators [Finance and Geopolitics]: zerohedge.com(+14), nationalreview.com(+9), ...
```

This influences scoring of ALL articles in the daily briefing — not just geopolitics.
Articles from these domains get a signal boost because your trusted curators link to them.

---

## Adding a Second Domain (future)

1. Set `ACTIVE_DOMAIN = "Health and Science"` in `curator_config.py`
2. Run archive import: `python3 x_import_archive.py --file=bookmarks.js`
3. Health signals written to `domain_signals["Health and Science"]` — no collision
4. Build Health front-end + schedule independently (different cadence than morning geo briefing)
5. Restore `ACTIVE_DOMAIN = "Finance and Geopolitics"` for daily job
6. *(Eventually)* `ACTIVE_DOMAIN` becomes a CLI flag so domains run independently

---

## X Bug Report

**Filed:** March 2026 via X Developers Community Forum + @XDevelopers

**Bug:** `GET /2/users/:id/bookmarks/folders/:folder_id` hard-caps at 20 results,
rejects `max_results` with HTTP 400, returns empty `meta: {}` (no pagination).

The main `/bookmarks` endpoint supports `max_results=100` and `next_token` pagination.
The folder-specific endpoint does not — this is the inconsistency.

**Workaround:** Use X data archive (`bookmarks.js`) for full historical access.

# Curator Feedback Design — Phase 2B

Technical design for wiring ratings, serendipity reserve, and temporal decay into the scoring pipeline.

---

## 1. Ratings → Scoring

**File:** `interests/ratings.json`
**Schema:**
```json
{
  "version": "1.0",
  "rating_scale": {
    "1": "useless — missed the point",
    "2": "shallow — surface analysis only",
    "3": "good — solid analysis with useful angles",
    "4": "excellent — contrarian depth, strong bibliography, actionable"
  },
  "ratings": [
    {
      "topic": "string",
      "source": "string (optional)",
      "rating": 1-4,
      "date": "YYYY-MM-DD",
      "notes": "string (optional)"
    }
  ]
}
```

**Implementation — `load_user_profile()`:**

After building the existing themes/sources/content-type section, append a ratings section:

```python
def load_ratings_profile() -> str:
    """Build a ratings-derived prompt section from interests/ratings.json."""
    ratings_path = Path(__file__).parent / 'interests' / 'ratings.json'
    try:
        with open(ratings_path) as f:
            data = json.load(f)
    except Exception:
        return ""

    ratings = data.get('ratings', [])
    if not ratings:
        return ""

    boosts, suppresses = [], []
    for r in ratings:
        topic = r.get('topic', '')
        rating = r.get('rating', 0)
        if rating >= 3:
            boosts.append(topic)
        elif rating <= 2:
            suppresses.append(topic)

    sections = []
    if boosts:
        sections.append("High-quality analysis found on: " + ", ".join(boosts))
    if suppresses:
        sections.append("Poor analysis found on: " + ", ".join(suppresses))

    return "\n".join(sections)
```

Inject result into `score_entries_xai()` prompt alongside `user_profile`.

---

## 2. Serendipity Reserve

**Config key:** `curation_settings.json` → `serendipity_reserve` (default: 0.20)

**Implementation — `score_entries_xai()`:**

After scoring and sorting all entries, apply serendipity reserve before returning:

```python
def apply_serendipity(scored_entries: list, reserve: float = 0.20) -> list:
    """
    Reserve `reserve` fraction of output slots for random non-top articles.
    Prevents the briefing from becoming an echo chamber.

    - Sorts entries by score descending
    - Takes top (1 - reserve) fraction normally
    - Fills remaining slots randomly from the rest
    """
    import random
    if not scored_entries or reserve <= 0:
        return scored_entries

    n = len(scored_entries)
    n_top = max(1, int(n * (1 - reserve)))

    sorted_entries = sorted(scored_entries, key=lambda x: x.get('score', 0), reverse=True)
    top = sorted_entries[:n_top]
    pool = sorted_entries[n_top:]

    n_serendipity = n - n_top
    serendipity = random.sample(pool, min(n_serendipity, len(pool)))

    # Tag serendipity entries so they're visible in output
    for e in serendipity:
        e['serendipity'] = True

    return top + serendipity
```

**Config loading:**
```python
def load_curation_settings() -> dict:
    settings_path = Path(__file__).parent / 'curation_settings.json'
    defaults = {'serendipity_reserve': 0.20, 'x_engagement_floor': 0, 'x_accounts': [], 'x_bookmark_folder_id': None}
    try:
        with open(settings_path) as f:
            return {**defaults, **json.load(f)}
    except Exception:
        return defaults
```

Create `curation_settings.json` with defaults on first run if missing.

---

## 3. Temporal Decay

**Decay factor:** 0.85 per week
**Formula:** `adjusted_weight = raw_weight × (0.85 ^ weeks_elapsed)`
**Timestamp source:** `curator_preferences.json` → `last_updated` field

**Implementation — inside `load_user_profile()`:**

Apply decay to weights before building the profile string:

```python
def apply_temporal_decay(weights: dict, last_updated_iso: str, decay_per_week: float = 0.85) -> dict:
    """
    Apply exponential decay to learned preference weights.
    Older signal fades toward neutral, newer signal dominates.
    """
    from datetime import datetime, timezone
    try:
        last = datetime.fromisoformat(last_updated_iso)
        now = datetime.now(timezone.utc)
        weeks = (now - last).days / 7.0
        factor = decay_per_week ** weeks
        return {k: v * factor for k, v in weights.items()}
    except Exception:
        return weights  # graceful fallback — no decay if timestamp missing/invalid
```

Apply to each weight dict (`preferred_themes`, `preferred_sources`, `preferred_content_types`) before threshold filtering.

**Caution:** With ~6 interactions, decay may over-penalize early signal. Monitor output quality after enabling. Consider gating with `if sample_size >= 20: apply_decay()` initially.

---

## File Locations

| File | Purpose |
|------|---------|
| `interests/ratings.json` | Deep dive quality ratings (user-maintained) |
| `curation_settings.json` | Tunable parameters (serendipity, X config, etc.) |
| `~/.openclaw/workspace/curator_preferences.json` | Canonical learned preferences (feedback loop) |
| `curator_rss_v2.py` | Main scoring pipeline — all three changes go here |

---

## Dry-Run Verification

After each task, run:
```bash
python curator_rss_v2.py --dry-run
```

Check `curator_preview.txt` for:
1. **Ratings:** Profile section mentions rating-derived boosts/suppresses (once ratings populated)
2. **Serendipity:** Some entries tagged `serendipity: True` in output
3. **Decay:** Profile weights reduced relative to raw values (add debug logging temporarily)

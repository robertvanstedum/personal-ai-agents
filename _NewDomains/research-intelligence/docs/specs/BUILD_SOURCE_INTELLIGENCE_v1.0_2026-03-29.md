# BUILD_SOURCE_INTELLIGENCE_v1.0_2026-03-29.md
*Date: 2026-03-29*
*Author: Claude Code (build plan from plan session)*
*Status: Approved — deferred to Phase 2 (after Mechanics is merged and stable)*
*Companion specs:*
- *SPEC_SOURCE_INTELLIGENCE_UPGRADE_v1.0_2026-03-28.md (original spec)*
- *BUILD_DEEPER_DIVE_MECHANICS_v1.0_2026-03-29.md (Phase 1, build first)*

---

## Intent

Reduce source repetition and improve source quality across research sessions. Phase 2 starts with novelty scoring only — the highest-value, lowest-risk mechanism. Subsequent mechanisms follow in order once novelty scoring is stable.

---

## Codebase Corrections vs. Original Spec

Five issues found during codebase review:

**1. Citation chasing partially exists**
`research.py:272` has `chase_citations()` using OpenAlex. Verify whether it is wired into the main session flow before building. The spec should not rebuild what already exists — check first, extend if needed.

**2. `curator_rss_v2.py` must stay out of scope**
The original spec scopes Curator novelty scoring in the same build. `curator_rss_v2.py` is 3,000+ lines, production-critical, and runs the daily briefing. Novelty scoring semantics differ (7-day window, per-domain) from research sessions (per-topic, unbounded). **Research-only for this build. Curator is a separate, later build.**

**3. No cross-session URL cache exists**
The spec correctly identifies this gap. The implementation will use `data/seen_urls/{topic}.json` — a simple flat file per topic — rather than parsing session `.md` files on each run. This is more reliable and faster.

**4. Japanese translation cost is 20–30× baseline — monitor/defer**
"$0.01–0.02 per source" assumes one Japanese source per session. Realistic sessions surface 8–12 candidates; 3–4 Japanese sources would be $0.06–0.08/session vs. $0.000–0.003 current baseline. Japanese translation is **deferred** pending monitoring. Add a flag in config to enable when ready: `"enable_japanese_translation": false`.

**5. Build order is correct — do not collapse**
Novelty scoring → discovery allocation → lateral search → citation chasing → Japanese translation. Each mechanism is additive. The order minimizes regression risk.

---

## Phase 2 Scope: Novelty Scoring Only

This build delivers one mechanism: **discount already-seen URLs** in the triage scoring pass, so the agent naturally prefers new sources over repeated ones.

All other mechanisms (discovery allocation, lateral search, citation chasing, Japanese translation) are deferred to Phase 3+.

---

## Files to Create / Modify

| File | Action | What Changes |
|------|--------|--------------|
| `agent/source_utils.py` | Create | `load_seen_urls(topic)`, `save_seen_urls(topic, urls)`, `apply_novelty_score(candidates, seen_urls, discount=0.3)` |
| `data/seen_urls/` | Create (dir) | Per-topic URL cache directory |
| `agent/research.py` | Modify | Hook novelty scoring into triage loop; call `save_seen_urls()` after session |
| `agent/config.json` | Modify | Add `novelty_discount_factor: 0.3` to search config block |

---

## `agent/source_utils.py`

```python
"""
source_utils.py — Shared source quality utilities for research sessions.

Phase 2: Novelty scoring only.
"""

import json
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
SEEN_URLS_DIR = ROOT / 'data' / 'seen_urls'


def load_seen_urls(topic: str) -> set[str]:
    """Load the set of already-seen URLs for a topic."""
    SEEN_URLS_DIR.mkdir(parents=True, exist_ok=True)
    path = SEEN_URLS_DIR / f'{topic}.json'
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return set(data.get('urls', []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen_urls(topic: str, new_urls: list[str]) -> None:
    """Append new URLs to the seen set for a topic."""
    SEEN_URLS_DIR.mkdir(parents=True, exist_ok=True)
    path = SEEN_URLS_DIR / f'{topic}.json'
    existing = load_seen_urls(topic)
    merged = sorted(existing | set(new_urls))
    path.write_text(json.dumps({'topic': topic, 'urls': merged}, indent=2))


def apply_novelty_score(
    candidates: list[dict],
    seen_urls: set[str],
    discount: float = 0.3,
) -> list[dict]:
    """
    Apply novelty discount to already-seen URLs.

    Multiplies `score` field by (1 - discount) for seen URLs.
    Adds `novelty_flag: true/false` for transparency.

    candidates: list of dicts with at least 'url' and 'score' fields
    seen_urls:  set of URLs seen in previous sessions
    discount:   0.0 = no discount, 1.0 = zero score for seen URLs
    """
    for c in candidates:
        url = c.get('url', '')
        if url and url in seen_urls:
            c['score'] = c.get('score', 0) * (1 - discount)
            c['novelty_flag'] = False
        else:
            c['novelty_flag'] = True
    return candidates
```

---

## Hook into `research.py`

In the triage loop, after candidates are scored and before final ranking:

```python
from source_utils import load_seen_urls, save_seen_urls, apply_novelty_score

# Load seen URLs for this topic
seen_urls = load_seen_urls(topic)

# Apply novelty discount before final ranking
candidates = apply_novelty_score(candidates, seen_urls, discount=config.get('novelty_discount_factor', 0.3))

# ... existing ranking and selection logic ...

# After session: persist new URLs to seen set
new_urls = [c['url'] for c in selected_candidates if c.get('url')]
save_seen_urls(topic, new_urls)
```

---

## Config Change — `agent/config.json`

Add to the `"search"` block:

```json
"search": {
  "brave_api_key_env": "BRAVE_API_KEY",
  "rate_limit_seconds": 1,
  "results_per_query": 8,
  "max_searches": 8,
  "novelty_discount_factor": 0.3,
  "enable_japanese_translation": false
}
```

---

## Deferred Mechanisms (Phase 3+)

These are in the original spec but excluded from this build:

| Mechanism | Why Deferred |
|-----------|-------------|
| **Discovery allocation** (25% budget for lateral searches) | Depends on novelty scoring being stable first |
| **Lateral search** (adjacent topic queries) | Requires discovery allocation framework |
| **Citation chasing** | Partially exists (`chase_citations()` in research.py) — verify before rebuilding |
| **Japanese translation** | 20–30× cost increase; requires monitoring period first |
| **Curator novelty scoring** | Different semantics (7-day window, per-domain); separate build |

---

## Japanese Translation — Monitoring Plan

Before enabling:
1. Run 5–10 sessions across topics and observe how often Japanese sources appear in candidates
2. Estimate actual translation cost per session (not the optimistic $0.01–0.02 assumption)
3. If cost is acceptable, enable `enable_japanese_translation: true` in config
4. Hook into triage pass: detect `lang == "ja"`, call translation model before scoring

Current config flag `enable_japanese_translation: false` ensures nothing runs until explicitly enabled.

---

## Build Order

1. **`agent/source_utils.py`** — utility functions (standalone, no dependencies)
2. **`data/seen_urls/`** — create directory (empty, populated on first session run)
3. **`agent/config.json`** — add `novelty_discount_factor` to search block
4. **`agent/research.py`** — hook novelty scoring into triage loop

Commit as single batch. No UI changes needed.

---

## Verification

```bash
# 1. Run a session for a topic
python agent/research.py --topic strait-of-hormuz

# 2. Check seen_urls cache created
cat data/seen_urls/strait-of-hormuz.json
# → {"topic": "strait-of-hormuz", "urls": ["https://...", ...]}

# 3. Run a second session for same topic
python agent/research.py --topic strait-of-hormuz

# 4. Verify novelty discount applied in logs
# → triage log should show reduced scores for previously-seen URLs

# 5. Verify new sources appear at top of rankings
# → session findings should differ from session 1
```

---

## Scope Boundary

**This build answers one question:** Does novelty scoring surface meaningfully different sources in session 2+ for the same topic?

If yes: proceed to discovery allocation (Phase 3).
If no signal: investigate whether the seen_urls cache is being populated and read correctly before extending.

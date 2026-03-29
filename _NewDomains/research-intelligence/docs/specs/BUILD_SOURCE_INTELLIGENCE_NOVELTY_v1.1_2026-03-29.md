# BUILD_SOURCE_INTELLIGENCE_NOVELTY_v1.1_2026-03-29.md
*Date: 2026-03-29*
*Author: Claude Code (plan session)*
*Status: Approved — ready to build*
*Supersedes: BUILD_SOURCE_INTELLIGENCE_v1.0_2026-03-29.md (Phase 2 scope, corrected)*

---

## Context

Research sessions repeatedly surface the same URLs across runs for the same topic.
This build adds a per-topic "seen URLs" cache so already-evaluated sources are
score-discounted on future runs, pushing novel sources to the top of the ranking.
Single mechanism, no UI changes, no risk to existing triage or synthesis flows.

---

## Codebase State (confirmed before build)

| Item | Status |
|------|--------|
| `agent/source_utils.py` | Does not exist — create |
| `data/seen_urls/` | Does not exist — create |
| `agent/config.json` search block | 4 keys — add 2 |
| `chase_citations()` at line 613 | **Active, wired in** — do not touch |
| Candidate `url` + `score` fields | Confirmed present on all scored dicts |

---

## Files to Change

| File | Action |
|------|--------|
| `agent/source_utils.py` | Create |
| `data/seen_urls/.gitkeep` | Create (directory marker) |
| `agent/config.json` | Modify — add 2 keys to `"search"` block |
| `agent/research.py` | Modify — import + 3 insertion points |

All paths relative to:
`/Users/vanstedum/Projects/personal-ai-agents/_NewDomains/research-intelligence/`

---

## 1. `agent/source_utils.py`

```python
"""
source_utils.py — Shared source quality utilities for research sessions.
Phase 2: Novelty scoring only.
"""
import json
from pathlib import Path

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

    Multiplies `score` by (1 - discount) for seen URLs.
    Adds `novelty_flag: True/False` for transparency in logs.
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

## 2. `agent/config.json` Change

Add to the `"search"` block (currently ends after `"max_searches": 8`):

```json
"novelty_discount_factor": 0.3,
"enable_japanese_translation": false
```

---

## 3. `agent/research.py` — Three Insertion Points

**A. Import** — at top, with other local imports:
```python
from source_utils import load_seen_urls, save_seen_urls, apply_novelty_score
```

**B. Load seen URLs** — before the `[2] Triage` step, after `topic` and `config` are bound:
```python
# Novelty scoring — load seen URLs for this topic
seen_urls = load_seen_urls(topic)
if seen_urls:
    print(f"  Novelty cache: {len(seen_urls)} previously-seen URLs for '{topic}'")
```

**C. Apply discount** — just before line 597 (`scored.sort(...)`), after triage loop:
```python
# Apply novelty discount before ranking
novelty_discount = config.get("search", {}).get("novelty_discount_factor", 0.3)
apply_novelty_score(scored, seen_urls, discount=novelty_discount)
flagged = sum(1 for c in scored if not c.get("novelty_flag", True))
if flagged:
    print(f"  Novelty: {flagged} seen URL(s) discounted by {novelty_discount:.0%}")
```

Scores are modified in-place. The sort at line 597 and re-sort at line 664 both
use adjusted values automatically. Citation chasing naturally elevates novel sources
for the `>= 4` threshold check.

**D. Save** — after `findings_path.write_text(...)` (line ~847), before step 6:
```python
# Persist seen URLs for next session (all triaged candidates)
all_scored_urls = [c['url'] for c in scored if c.get('url')]
if all_scored_urls:
    save_seen_urls(topic, all_scored_urls)
    print(f"  Novelty cache: {len(all_scored_urls)} URLs saved for '{topic}'")
```

---

## Verification

```bash
cd /Users/vanstedum/Projects/personal-ai-agents/_NewDomains/research-intelligence
source ../../../venv/bin/activate

# Syntax check
python3 -c "import ast; ast.parse(open('agent/research.py').read()); print('research.py OK')"
python3 -c "import ast; ast.parse(open('agent/source_utils.py').read()); print('source_utils.py OK')"

# Run a session — cache created on first run
python agent/research.py --topic hellscape-taiwan-porcupine --session-name hellscape-taiwan-porcupine-003
cat data/seen_urls/hellscape-taiwan-porcupine.json
# → {"topic": "hellscape-taiwan-porcupine", "urls": ["https://...", ...]}

# Run second session — should print "Novelty: N seen URL(s) discounted by 30%"
python agent/research.py --topic hellscape-taiwan-porcupine --session-name hellscape-taiwan-porcupine-004
```

---

## Not in Scope

- Discovery allocation, lateral search, citation chasing extension, Japanese translation
- Curator novelty scoring (`curator_rss_v2.py`) — different semantics, separate build
- UI changes of any kind

---

## Deferred Mechanisms (Phase 3+)

| Mechanism | Reason Deferred |
|-----------|----------------|
| Discovery allocation | Depends on novelty scoring being stable first |
| Lateral search | Requires discovery allocation framework |
| Citation chasing extension | Already partially exists — verify before extending |
| Japanese translation | 20–30× cost increase; needs monitoring period |
| Curator novelty scoring | Different semantics (7-day window, per-domain) |

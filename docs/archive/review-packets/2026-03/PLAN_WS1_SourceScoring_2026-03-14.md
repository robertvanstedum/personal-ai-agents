# Workstream 1 — Source Scoring

curator_sources.json trust weights

Date: 2026-03-14 | Author: Robert & Claude | Status: APPROVED — Built March 14, 2026 (see BUILD_WS5_PHASE_A.md for pre-condition record)

## Context

Adds domain-level trust weights upstream of Grok scoring. A `curator_sources.json` file already exists with 5 trusted entries but is not yet read by any code. This plan wires it into both the main briefing pipeline (`curator_rss_v2.py`) and the priority feed (`curator_priority_feed.py`), and adds auto-discovery of new Brave domains as probationary.

## Step 1 — Seed curator_sources.json

Final 9-entry file (4 new entries added):

| Domain | Trust | set_by | Note |
|---|---|---|---|
| warontherocks.com | trusted | explicit | Defense/security analysis |
| foreignaffairs.com | trusted | explicit | Premier geopolitics journal |
| arxiv.org | trusted | explicit | Academic preprints, q-fin |
| justsecurity.org | trusted | explicit | National security / international law |
| cepr.org | trusted | explicit | Economic policy research, VoxEU |
| crisisgroup.org | trusted | explicit | Conflict analysis |
| investing.com | drop | explicit | Aggregator noise |
| theduran.com | neutral | explicit | |
| zerohedge.com | neutral | explicit | |

## Step 2 — curator_rss_v2.py: Apply Trust Multipliers

Trust multiplier reference table:

| Trust tier | Multiplier | Effect |
|---|---|---|
| trusted | 1.5× | Boosted — high-confidence sources |
| neutral | 1.0× | No change (default for unknown domains) |
| deprioritize | 0.5× | Half score — surfaces rarely |
| probationary | 0.7× | Slight discount — auto-discovered domains |
| drop | — | Excluded before Grok scoring (saves tokens) |

### A. Module-level helpers (add near other utility functions, before curate()):

```python
def _load_source_trust() -> dict:
    """Load curator_sources.json -> {domain: trust_tier} dict."""
    path = Path(__file__).parent / 'curator_sources.json'
    if not path.exists():
        return {}
    try:
        entries = json.loads(path.read_text())
        return {e['domain']: e['trust'] for e in entries
                if 'domain' in e and 'trust' in e}
    except Exception:
        return {}

_TRUST_MULTIPLIERS = {
    'trusted': 1.5, 'neutral': 1.0,
    'deprioritize': 0.5, 'probationary': 0.7
}

def _domain_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith('www.') else netloc
    except Exception:
        return ''
```

### B. In curate(), after building all_entries and BEFORE scoring (drop filter):

```python
# Load source trust table
_source_trust = _load_source_trust()

# Filter out 'drop' domains before scoring (saves LLM tokens)
before_drop = len(all_entries)
all_entries = [
    e for e in all_entries
    if _source_trust.get(
        _domain_from_url(e.get('link', '')), 'neutral') != 'drop'
]
if before_drop != len(all_entries):
    print(f"Source trust: dropped "
          f"{before_drop - len(all_entries)} entries (drop tier)")
```

### C. In curate(), AFTER score assignment (apply multipliers):

```python
# Apply trust multipliers to scores
for entry in all_entries:
    domain = _domain_from_url(entry.get('link', ''))
    tier = _source_trust.get(domain, 'neutral')
    multiplier = _TRUST_MULTIPLIERS.get(tier, 1.0)
    if multiplier != 1.0:
        entry['raw_score'] = entry.get('raw_score', entry['score'])
        entry['score'] = round(entry['score'] * multiplier, 2)
        entry['trust_tier'] = tier
```

## Step 3 — curator_priority_feed.py: Auto-Log Probationary Domains

Any domain returned by Brave that is not already in DOMAIN_WHITELIST or curator_sources.json is auto-logged as probationary (set_by='auto') for human review. Idempotent — duplicate domains skipped.

### Helper function (add near whitelist_filter):

```python
def _log_probationary_domains(raw_results: list) -> None:
    """
    Auto-add any domain surfaced by Brave that is not already in
    curator_sources.json. Sets trust='probationary', set_by='auto'.
    Idempotent -- skips domains already in the file.
    """
    import json as _json
    from datetime import datetime as _dt

    sources_path = Path(__file__).parent / 'curator_sources.json'
    try:
        existing = _json.loads(sources_path.read_text()) if sources_path.exists() else []
    except Exception:
        existing = []

    known_domains = {e['domain'] for e in existing}

    new_entries = []
    for r in raw_results:
        domain = extract_domain(r.get('url', ''))
        if (domain and domain not in known_domains
                and domain not in DOMAIN_WHITELIST):
            known_domains.add(domain)
            new_entries.append({
                'domain': domain,
                'trust': 'probationary',
                'set_by': 'auto',
                'note':  f'auto-discovered via Brave '
                         f'{_dt.now().strftime("%Y-%m-%d")}',
            })

    if new_entries:
        existing.extend(new_entries)
        sources_path.write_text(_json.dumps(existing, indent=2))
        log.info(f'Logged {len(new_entries)} new probationary domain(s): '
                 f'{[e["domain"] for e in new_entries]}')
```

### Call site (after each brave_search(), before whitelist_filter()):

```python
raw = brave_search(query, count=50)
_log_probationary_domains(raw)          # <-- new line
whitelisted = whitelist_filter(raw)
```

## Files Modified

| File | Change |
|---|---|
| curator_sources.json | Add 4 entries: crisisgroup (trusted), investing (drop), theduran (neutral), zerohedge (neutral) |
| curator_rss_v2.py | _load_source_trust(), _domain_from_url(), _TRUST_MULTIPLIERS; drop filter before scoring; multiplier after scoring |
| curator_priority_feed.py | _log_probationary_domains() helper; called after each brave_search() |

## Verification

1. Run `python curator_rss_v2.py --dry-run --model xai` — check for:
   - "Source trust: dropped N entries" if investing.com articles present in pool
   - Scores for crisisgroup.org / foreignaffairs.com articles should be ×1.5 vs raw_score

2. Run priority feed — inspect `curator_sources.json` afterward:
   - New Brave domains should appear with trust='probationary', set_by='auto'

3. Spot-check a trusted source entry:
   - Confirm raw_score preserved and `score = raw_score x 1.5`

---

*Generated 2026-03-14 | personal-ai-agents / Workstream 1*
*Converted from PDF to markdown 2026-03-15*

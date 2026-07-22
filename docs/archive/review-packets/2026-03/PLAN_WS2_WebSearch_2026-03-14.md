# WS2 Completion — Web Search in Daily Briefing

Workstream 2 final item: Brave search candidates in curator_rss_v2.py
Date: 2026-03-14 | Author: Robert & Claude | Status: APPROVED — Built March 14, 2026

## Context

The main daily briefing currently draws candidates from two pools: RSS feeds (~390 articles) and X bookmarks (~332 articles). This plan adds a third pool: Brave web search results, topic-guided by active priorities and static baseline queries. These candidates compete on merit for the same 20 daily slots — enrichment, not dominance. Completes Workstream 2.

## Reused Code (no changes to these)

| Function / Constant | File | Notes |
|---|---|---|
| brave_search(), DOMAIN_WHITELIST | curator_priority_feed.py | Lazy-imported inside helper to avoid circular import |
| load_priorities() | curator_rss_v2.py:1291 | Returns active, non-expired priorities |
| _domain_from_url() | curator_rss_v2.py (WS1) | Strips www., lowercases netloc |
| hashlib (MD5) | curator_rss_v2.py imports | MD5(url)[:5] — same hash scheme as RSS/X entries |

| Source | Queries | Results each | Max candidates |
|---|---|---|---|
| Active priorities | up to 3 | 5 | 15 |
| Baseline topics | 6 evergreen | ~1 | 5 |
| Total web search | | | ≤ 20 of ~898 pool |

<2.3% of pool — enrichment, not dominance. Most web candidates won't reach top 20; that is correct.

## Step 1 — Add _fetch_web_search_candidates() helper

Insert after the _TRUST_MULTIPLIERS / _domain_from_url block (WS1 helpers), before def curate().

```python
def _fetch_web_search_candidates(
    seen_hashes: set,
    priority_limit: int = 3,
    results_per_priority: int = 5,
    baseline_total: int = 5,
) -> List[Dict]:
    """
    Fetch Brave web search candidates for the daily briefing pool.
    Priority queries first (up to 3 x 5 = 15), baseline fills after (5 total).
    Domain-whitelist filtered, deduped vs seen_hashes. Tagged source_type='web_search'.
    """
    # Lazy import — curator_priority_feed imports curator_rss_v2 at module level
    try:
        from curator_priority_feed import brave_search, DOMAIN_WHITELIST
    except ImportError:
        print("Web search: curator_priority_feed not available, skipping")
        return []

    BASELINE_TOPICS = [
        'geopolitics', 'monetary policy', 'emerging markets',
        'AI economy', 'energy markets', 'US foreign policy',
    ]
    candidates = []
    seen_urls: set = set()

    def _to_entry(r: dict, query_label: str):
        url = r.get('url', '')
        if not url: return None
        domain = _domain_from_url(url)
        if domain not in DOMAIN_WHITELIST: return None
        h = hashlib.md5(url.encode()).hexdigest()[:5]
        if h in seen_hashes or url in seen_urls: return None
        seen_urls.add(url)
        return {
            'hash_id': h, 'source': domain, 'source_type': 'web_search',
            'title':   r.get('title', '').strip(),
            'link':    url,
            'summary': r.get('description', '').strip(),
            'published': None, 'query_label': query_label,
        }

    # 1. Priority queries
    for priority in load_priorities()[:priority_limit]:
        keywords = priority.get('keywords', [])
        if not keywords: continue
        label = priority.get('label', priority.get('id', ''))
        raw = brave_search(' '.join(keywords), count=results_per_priority * 2)
        added = 0
        for r in raw:
            if added >= results_per_priority: break
            entry = _to_entry(r, f"priority:{label}")
            if entry:
                candidates.append(entry)
                seen_hashes.add(entry['hash_id'])
                added += 1
        print(f"  Web [{label}]: {added} candidates")

    # 2. Baseline topics
    per_topic = max(1, (baseline_total + len(BASELINE_TOPICS)-1) // len(BASELINE_TOPICS))
    baseline_added = 0
    for topic in BASELINE_TOPICS:
        if baseline_added >= baseline_total: break
        for r in brave_search(topic, count=per_topic * 2):
            if baseline_added >= baseline_total: break
            entry = _to_entry(r, f"baseline:{topic}")
            if entry:
                candidates.append(entry)
                seen_hashes.add(entry['hash_id'])
                baseline_added += 1
    if baseline_added:
        print(f"  Web baseline: {baseline_added} candidates")

    return candidates
```

## Step 2 — Call site in curate()

Insert after print(f"X bookmarks merged...") (~line 1527) and before the source trust block:

```python
    # Web search enrichment — active priority keywords + baseline topics
    print(f"\n Web search enrichment:")
    _ws_seen = {e['hash_id'] for e in all_entries if e.get('hash_id')}
    web_candidates = _fetch_web_search_candidates(seen_hashes=_ws_seen)
    if web_candidates:
        all_entries.extend(web_candidates)
        print(f"  Pool after web search: {len(all_entries)} candidates")
    else:
        print(f"  No web candidates added (Brave unavailable or all filtered)")
```

## Design Notes

- **Circular import:** curator_priority_feed imports curator_rss_v2 at module level. Solved via lazy import inside the helper function body — not at module level.
- **Trust multipliers (WS1):** web search entries pass through the existing trust filter automatically — they have link fields and _domain_from_url works on them.
- **source_type: 'web_search'** enables WS5 intelligence to distinguish web candidates from RSS/X entries in performance tracking.
- **query_label** field (e.g. "priority:Chicago Crime", "baseline:geopolitics") lets WS5 attribute which query surfaced which article.
- **Fail-safe:** brave_search() returns [] on any API/key failure. _fetch_web_search_candidates() returns []. curate() logs cleanly and continues — zero impact on daily briefing.

## Verification

1. `python curator_rss_v2.py --dry-run --model xai` — look for:
   - "Web search enrichment:" section in output
   - "Web [<priority label>]: N candidates" per active priority
   - "Web baseline: N candidates"
   - Total pool count higher than ~878
2. Spot-check: a web candidate in dry-run output should show source_type: web_search
3. Confirm graceful degradation: if Brave unavailable, "No web candidates added" logs and run completes normally.

---

*Generated 2026-03-14 | personal-ai-agents / Workstream 2 final*
*Converted from PDF to markdown 2026-03-15*

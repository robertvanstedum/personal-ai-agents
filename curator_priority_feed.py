#!/usr/bin/env python3
"""
curator_priority_feed.py - Priority Feed runner (Tier 2 — Web Search Layer).

For each active priority, runs a Brave web search constrained to a trusted
domain whitelist, pre-filters with Haiku, scores survivors with Grok, and
appends new articles to the priority's feed[] array in priorities.json.

Usage:
    python curator_priority_feed.py              # all active priorities
    python curator_priority_feed.py --dry-run    # print results, touch nothing
    python curator_priority_feed.py --priority-id=p_007  # one priority only
"""

import argparse
import json
import keyring
import logging
import requests
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# ── Scoring functions imported from curator (do not duplicate) ──────────────
from curator_rss_v2 import (
    load_user_profile,
    score_entries_haiku_prefilter,
    score_entries_xai,
)

# ── Config ───────────────────────────────────────────────────────────────────

PRIORITIES_FILE = Path.home() / '.openclaw' / 'workspace' / 'priorities.json'

BRAVE_API_URL = 'https://api.search.brave.com/res/v1/web/search'

# Trusted domain whitelist — web search results outside this set are discarded.
# Expanded from spec with Robert's additions (2026-03-13).
DOMAIN_WHITELIST = {
    # Finance / policy originals
    'reuters.com', 'ft.com', 'economist.com', 'foreignpolicy.com',
    'foreignaffairs.com', 'project-syndicate.org', 'politico.com',
    'theguardian.com', 'nytimes.com', 'bloomberg.com',
    'warontherocks.com', 'justsecurity.org', 'crisisgroup.org', 'acleddata.com',
    # Added by Robert — round 1 (2026-03-13)
    'hrw.org', 'cfr.org', 'chathamhouse.org', 'bbc.com', 'apnews.com',
    'middleeasteye.net', 'theintercept.com', 'sipri.org',
    'worldbank.org', 'imf.org',
    # Added by Robert — round 2 (2026-03-13)
    'wsj.com', 'washingtonpost.com',           # major US nationals (missing from original)
    'chicagobusiness.com',                     # Crain's Chicago Business
    'crimelab.uchicago.edu',                   # UChicago Crime Lab research
    'blockclubchicago.org',                    # Block Club Chicago local reporting
    'counciloncj.org',                         # Council on Criminal Justice
    # Added — US domestic topics (2026-03-14)
    'cbsnews.com', 'cnn.com', 'us.cnn.com',    # US nationals
    'chicagotribune.com',                      # Chicago Tribune
    'chicago.suntimes.com',                    # Chicago Sun-Times
    'therealdeal.com',                         # Urban real estate / economic
    'wirepoints.org',                          # Illinois policy analysis
}

# Haiku pre-filter: keep results scoring at or above this threshold
PREFILTER_MIN_SCORE = 3.0

# Max new articles appended per priority per run
MAX_NEW_PER_PRIORITY = 10

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
log = logging.getLogger(__name__)


# ── Priorities ────────────────────────────────────────────────────────────────

def load_priorities(priority_id: str | None = None) -> list:
    """
    Load active, non-expired priorities from priorities.json.
    If priority_id is given, return only that one (still must be active).
    """
    if not PRIORITIES_FILE.exists():
        return []
    data = json.loads(PRIORITIES_FILE.read_text())
    now = datetime.now(timezone.utc)
    active = []
    for p in data.get('priorities', []):
        if not p.get('active', False):
            continue
        expires_at = p.get('expires_at')
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < now:
                    continue
            except ValueError:
                pass
        if priority_id and p['id'] != priority_id:
            continue
        active.append(p)
    return active


def save_priorities(priorities_updated: list) -> None:
    """Write updated priority objects back to priorities.json atomically."""
    data = json.loads(PRIORITIES_FILE.read_text())
    # Replace matching priorities in-place by id
    updated_by_id = {p['id']: p for p in priorities_updated}
    for i, p in enumerate(data['priorities']):
        if p['id'] in updated_by_id:
            data['priorities'][i] = updated_by_id[p['id']]
    tmp = PRIORITIES_FILE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    tmp.rename(PRIORITIES_FILE)


# ── Brave Search ──────────────────────────────────────────────────────────────

def brave_search(query: str, count: int = 20) -> list:
    """
    Query Brave Search API and return raw result list.
    Each result has: title, url, description, page_age.
    Returns [] on any error (caller decides whether to abort or skip).
    """
    api_key = keyring.get_password('brave_search', 'api_key')
    if not api_key:
        log.error('Brave API key not found in keychain (brave_search / api_key)')
        return []
    try:
        resp = requests.get(
            BRAVE_API_URL,
            headers={
                'Accept': 'application/json',
                'Accept-Encoding': 'gzip',
                'X-Subscription-Token': api_key,
            },
            params={'q': query, 'count': count, 'search_lang': 'en', 'freshness': 'pw'},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get('web', {}).get('results', [])
    except Exception as e:
        log.error(f'Brave search failed for query {query!r}: {e}')
        return []


def extract_domain(url: str) -> str:
    """Return bare domain (no www.) from URL."""
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith('www.') else netloc
    except Exception:
        return ''


def whitelist_filter(results: list) -> list:
    """Discard results whose domain is not in DOMAIN_WHITELIST."""
    kept = []
    for r in results:
        domain = extract_domain(r.get('url', ''))
        if domain in DOMAIN_WHITELIST:
            kept.append(r)
    return kept


def _log_probationary_domains(raw_results: list) -> None:
    """
    Auto-add any domain surfaced by Brave that isn't already in
    curator_sources.json. Sets trust='probationary', set_by='auto'.
    Idempotent — skips domains already in the file.
    """
    sources_path = Path(__file__).parent / 'curator_sources.json'
    try:
        existing = json.loads(sources_path.read_text()) if sources_path.exists() else []
    except Exception:
        existing = []
    known_domains = {e['domain'] for e in existing}

    new_entries = []
    for r in raw_results:
        domain = extract_domain(r.get('url', ''))
        if domain and domain not in known_domains and domain not in DOMAIN_WHITELIST:
            known_domains.add(domain)   # prevent dupes within this batch
            new_entries.append({
                'domain': domain,
                'trust':  'probationary',
                'set_by': 'auto',
                'note':   f'auto-discovered via Brave {datetime.now().strftime("%Y-%m-%d")}',
            })

    if new_entries:
        existing.extend(new_entries)
        sources_path.write_text(json.dumps(existing, indent=2))
        log.info(f'Logged {len(new_entries)} new probationary domain(s): '
                 f'{[e["domain"] for e in new_entries]}')


def results_to_entries(results: list) -> list:
    """
    Convert Brave result dicts to the entry format expected by
    score_entries_haiku_prefilter() and score_entries_xai():
      title, source, summary, link
    """
    entries = []
    for r in results:
        url = r.get('url', '')
        entries.append({
            'title':   r.get('title', '').strip(),
            'source':  extract_domain(url),
            'summary': r.get('description', '').strip(),
            'link':    url,
        })
    return entries


# ── Feed ──────────────────────────────────────────────────────────────────────

def existing_feed_urls(priority: dict) -> set:
    """Return the set of URLs already in the priority's feed."""
    return {a['url'] for a in priority.get('feed', [])}


def entries_to_feed_articles(entries: list, fetched_at: str) -> list:
    """Convert scored entries to feed article dicts (the stored schema)."""
    articles = []
    for e in entries:
        articles.append({
            'url':        e['link'],
            'title':      e['title'],
            'source':     e['source'],
            'fetched_at': fetched_at,
            'score':      round(float(e.get('score', 0.0)), 2),
            'summary':    e.get('summary', ''),
        })
    return articles


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_priority(priority: dict, user_profile: str,
                 dry_run: bool, fetched_at: str) -> int:
    """
    Run the full search → filter → score → dedup → append pipeline for one priority.
    Returns the number of new articles added (0 on dry-run or no results).
    """
    label    = priority['label']
    keywords = priority.get('keywords', [])
    query    = ' '.join(keywords)

    print(f"\n  Priority: {label} [{priority['id']}]")
    print(f"  Query:    {query!r}")

    # 1. Brave search
    raw = brave_search(query, count=20)
    print(f"  Brave:    {len(raw)} raw results")
    if not raw:
        print("  → No results from Brave, skipping")
        return 0

    # 1b. Log any unknown domains as probationary in curator_sources.json
    _log_probationary_domains(raw)

    # 2. Domain whitelist filter
    whitelisted = whitelist_filter(raw)
    print(f"  Whitelist: {len(whitelisted)}/{len(raw)} passed "
          f"({[extract_domain(r['url']) for r in whitelisted]})")
    if not whitelisted:
        print("  → No results passed whitelist, skipping")
        return 0

    # 3. Convert to entry format for scoring
    entries = results_to_entries(whitelisted)

    # 4. Haiku pre-filter (keeps all, sorted by score; we apply threshold manually)
    try:
        pre_filtered = score_entries_haiku_prefilter(
            entries,
            top_n=len(entries),     # keep all, we'll threshold below
            fallback_on_error=True,
            user_profile=user_profile,
        )
    except Exception as e:
        print(f"  ⚠️  Haiku pre-filter failed: {e} — using all whitelisted results")
        pre_filtered = entries

    above_threshold = [e for e in pre_filtered
                       if e.get('score', 0) >= PREFILTER_MIN_SCORE]
    print(f"  Haiku:    {len(above_threshold)}/{len(pre_filtered)} "
          f"passed threshold (>={PREFILTER_MIN_SCORE})")
    if not above_threshold:
        print("  → Nothing passed Haiku pre-filter, skipping")
        return 0

    # 5. Grok scoring
    # score_entries_xai returns score-only dicts [{score, category, method, raw_score}, ...]
    # — merge them back onto the original entry dicts so we keep title/link/summary.
    try:
        xai_results = score_entries_xai(
            above_threshold,
            fallback_on_error=True,
            user_profile=user_profile,
        )
        for entry, result in zip(above_threshold, xai_results):
            entry.update(result)
        scored = above_threshold
    except Exception as e:
        print(f"  ⚠️  Grok scoring failed: {e} — using Haiku scores")
        scored = above_threshold

    scored.sort(key=lambda e: e.get('score', 0), reverse=True)

    # 6. Dedup against existing feed
    seen_urls    = existing_feed_urls(priority)
    new_entries  = [e for e in scored if e['link'] not in seen_urls]
    new_entries  = new_entries[:MAX_NEW_PER_PRIORITY]

    print(f"  Grok:     top scores — "
          f"{[round(e.get('score',0),1) for e in scored[:5]]}")
    print(f"  New (after dedup): {len(new_entries)}")

    if not new_entries:
        print("  → All results already in feed")
        return 0

    for e in new_entries:
        print(f"    + [{e.get('score',0):.1f}] {e['source']}: {e['title'][:60]}")

    if dry_run:
        return 0

    # 7. Append to feed
    new_articles = entries_to_feed_articles(new_entries, fetched_at)
    priority.setdefault('feed', [])
    # Prepend new articles (most recent first), cap at 50 total
    priority['feed'] = (new_articles + priority['feed'])[:50]
    priority['feed_last_updated'] = fetched_at

    return len(new_articles)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, priority_id: str | None = None) -> None:
    """Run priority feed for all active priorities (or one if priority_id given)."""

    priorities = load_priorities(priority_id=priority_id)
    if not priorities:
        if priority_id:
            print(f"No active priority found with id={priority_id!r}")
        else:
            print("No active priorities — nothing to do")
        return

    print(f"{'DRY-RUN: ' if dry_run else ''}Priority Feed run")
    print(f"Active priorities: {len(priorities)}")

    user_profile = load_user_profile()
    if user_profile:
        print(f"User profile loaded ({len(user_profile)} chars)")

    fetched_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    updated    = []
    total_new  = 0

    for priority in priorities:
        added = run_priority(priority, user_profile, dry_run, fetched_at)
        total_new += added
        if added > 0:
            updated.append(priority)

    if not dry_run and updated:
        save_priorities(updated)
        print(f"\n✅ priorities.json updated — {total_new} new articles added "
              f"across {len(updated)} priorities")
    elif dry_run:
        print(f"\n--- DRY-RUN COMPLETE — nothing written ---")
    else:
        print(f"\nNo new articles added")


def main():
    parser = argparse.ArgumentParser(
        description='Priority Feed runner — web search per active priority'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Print results without writing to priorities.json')
    parser.add_argument('--priority-id', default=None, metavar='ID',
                        help='Run for one priority only (e.g. p_007)')
    args = parser.parse_args()

    run(dry_run=args.dry_run, priority_id=args.priority_id)


if __name__ == '__main__':
    main()

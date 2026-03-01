#!/usr/bin/env python3
"""
x_adapter.py — Phase 3C: X Adapter / t.co URL Enrichment

Two modes for fetching bookmark URL entities:

1. --from-bootstrap  (default / recommended)
   Loads the 398 tweet IDs stored by x_bootstrap.py, batch-fetches their
   URL entities, and builds a domain_signals map from all historical bookmarks.
   This is the richest signal — covers the full curator ecosystem.

2. --folder=<name>
   Fetches from a specific X bookmark folder (newer feature; folders tend to
   be smaller since they capture only recently-organized bookmarks).
   Use --list-folders first to see available folder names.

Both modes update learned_patterns['domain_signals'] in curator_preferences.json.
Scores are curator-trust weighted (more saves from a curator → higher domain weight).

Usage:
    python x_adapter.py --from-bootstrap              # Enrich from all 398 stored bookmarks
    python x_adapter.py --from-bootstrap --dry-run    # Preview, no writes
    python x_adapter.py --from-bootstrap --verbose    # Show per-curator breakdown
    python x_adapter.py --from-bootstrap --full       # Re-fetch all (ignore cache)
    python x_adapter.py --list-folders                # See available bookmark folders
    python x_adapter.py --folder="Finance and geopolitics" --dry-run
"""

import json
import sys
import time
import requests
import tweepy
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from x_auth import get_x_client
from x_oauth2_authorize import get_valid_token

# ── Paths ──────────────────────────────────────────────────────────────────
PREFS_PATH = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'
CACHE_PATH = Path(__file__).parent / 'x_adapter_cache.json'

# ── Twitter API base ────────────────────────────────────────────────────────
API_BASE = 'https://api.x.com/2'

# ── Domain noise filter ────────────────────────────────────────────────────
# These produce no useful content-ecosystem signal
SKIP_DOMAINS = {
    'x.com', 'twitter.com', 't.co',            # Twitter self-links
    'pic.twitter.com', 'pbs.twimg.com',         # Twitter media
    'youtube.com', 'youtu.be',                  # Video (not articles)
    'substack.com',                             # Platform, not a source
    'bit.ly', 'tinyurl.com', 'ow.ly',           # Generic shorteners
    'buff.ly',                                  # Buffer social shortener
    'github.com',                               # Code hosting, not content
}

# ── Constants ──────────────────────────────────────────────────────────────
MAX_LINKS_PER_CURATOR = 3    # Cap: stops one prolific linker from dominating
MIN_DOMAIN_SCORE      = 2    # Minimum score to appear in profile prompt


# ── Auth ───────────────────────────────────────────────────────────────────

def get_headers(token: str) -> dict:
    """Build Authorization headers for X API requests."""
    return {'Authorization': f'Bearer {token}'}


def get_my_user_id(token: str) -> str:
    """Get the authenticated user's X user ID."""
    resp = requests.get(f'{API_BASE}/users/me', headers=get_headers(token))
    resp.raise_for_status()
    return resp.json()['data']['id']


# ── Folder API ─────────────────────────────────────────────────────────────

def fetch_bookmark_folders(user_id: str, token: str) -> list[dict]:
    """
    List all bookmark folders for the authenticated user.
    Returns list of folder dicts: {id, name, description, created_at}
    """
    url  = f'{API_BASE}/users/{user_id}/bookmarks/folders'
    resp = requests.get(url, headers=get_headers(token))
    resp.raise_for_status()
    return resp.json().get('data', [])


def fetch_folder_tweet_ids(user_id: str, folder_id: str, token: str) -> list[str]:
    """
    Step 1: Fetch tweet IDs from a specific bookmark folder.

    The folder endpoint returns IDs only — no params supported, no entities.
    Paginate via meta.next_token until exhausted.
    Returns list of tweet ID strings.
    """
    url      = f'{API_BASE}/users/{user_id}/bookmarks/folders/{folder_id}'
    all_ids  = []
    params   = {}
    page     = 0

    while True:
        page += 1
        resp = requests.get(url, headers=get_headers(token), params=params or None)
        resp.raise_for_status()
        data = resp.json()

        ids = [t['id'] for t in data.get('data', [])]
        all_ids.extend(ids)
        print(f"  Page {page}: {len(ids)} tweet IDs (running total: {len(all_ids)})")

        next_token = data.get('meta', {}).get('next_token')
        if not next_token:
            break
        params = {'pagination_token': next_token}
        time.sleep(1)

    return all_ids


def fetch_tweet_details(tweet_ids: list[str]) -> tuple[list, dict]:
    """
    Step 2: Fetch full tweet details (text, entities, author) for given tweet IDs.

    Uses OAuth 1.0a (get_x_client) with user_auth=True — confirmed working.
    Batches 100 IDs per request per Twitter API limit.

    Returns: (tweets list of dicts, authors dict {author_id: username})
    """
    client  = get_x_client()
    results = []
    authors = {}
    total   = (len(tweet_ids) + 99) // 100

    for i in range(0, len(tweet_ids), 100):
        batch     = tweet_ids[i:i + 100]
        batch_num = i // 100 + 1
        print(f"  Fetching details batch {batch_num}/{total} ({len(batch)} tweets)...")

        try:
            resp = client.get_tweets(
                ids=batch,
                tweet_fields=['entities', 'text', 'author_id'],
                expansions=['author_id'],
                user_fields=['username'],
                user_auth=True
            )
        except tweepy.TweepyException as e:
            print(f"  ⚠️  API error on batch {batch_num}: {e}")
            continue

        if not resp.data:
            continue

        # Collect author usernames from expansions
        if resp.includes and 'users' in resp.includes:
            for user in resp.includes['users']:
                authors[str(user.id)] = user.username

        for tweet in resp.data:
            entities = tweet.entities or {}
            results.append({
                'id':        str(tweet.id),
                'text':      tweet.text or '',
                'author_id': str(tweet.author_id),
                'entities':  dict(entities) if hasattr(entities, '__iter__') else {},
            })

        if i + 100 < len(tweet_ids):
            time.sleep(1)

    return results, authors


# ── Domain extraction ──────────────────────────────────────────────────────

def extract_domain(expanded_url: str) -> str | None:
    """
    Extract clean domain from a URL. Returns None for noise/irrelevant URLs.

    Examples:
        'https://www.ft.com/content/...'     → 'ft.com'
        'https://arxiv.org/abs/2301.00001'   → 'arxiv.org'
        'https://t.co/xyz'                   → None  (SKIP_DOMAINS)
        'https://youtube.com/watch?v=...'    → None  (SKIP_DOMAINS)
    """
    try:
        parsed = urlparse(expanded_url)
        domain = parsed.netloc.lower()
        if domain.startswith('www.'):
            domain = domain[4:]
        if not domain or domain in SKIP_DOMAINS:
            return None
        return domain
    except Exception:
        return None


# ── Domain scoring ─────────────────────────────────────────────────────────

def build_domain_scores(tweets: list, authors: dict, id_to_curator: dict | None = None) -> tuple:
    """
    Build curator-trust weighted domain scores from folder bookmarks.

    Weight logic:
      curator_weight = number of bookmarks saved from that X account
      (more bookmarks saved from a curator → higher trust → their links
       carry more weight in domain_signals)

    Cap logic (MAX_LINKS_PER_CURATOR = 3):
      Count at most 3 unique domains per curator.
      Prevents one prolific linker from dominating the signal — we
      credit diversity of curators more than volume from one account.

    Returns:
      domain_scores (Counter): domain → weighted score
      curator_domain_details (dict): domain → [(curator_handle, weight)]
      stats (dict): summary counts for reporting
    """
    # Build author_id → curator handle.
    # id_to_curator (from bootstrap) takes precedence; falls back to API expansions.
    def curator_handle(author_id, tweet_id=None):
        if id_to_curator and tweet_id and tweet_id in id_to_curator:
            return id_to_curator[tweet_id]
        uname = authors.get(author_id, author_id)
        return f'X/@{uname}'

    # Count bookmarks per curator (= trust weight)
    curator_save_counts = Counter(
        curator_handle(t.get('author_id', ''), t.get('id')) for t in tweets
    )

    # Accumulate domains per curator across all their bookmarked tweets
    curator_domains  = defaultdict(list)
    tweets_with_links = 0
    tweets_text_only  = 0

    for tweet in tweets:
        author_id = tweet.get('author_id', '')
        tweet_id  = tweet.get('id', '')
        curator   = curator_handle(author_id, tweet_id)
        entities  = tweet.get('entities') or {}
        urls      = entities.get('urls', []) if isinstance(entities, dict) else []

        domains = [d for u in urls if (d := extract_domain(u.get('expanded_url', '')))]

        if domains:
            curator_domains[curator].extend(domains)
            tweets_with_links += 1
        else:
            tweets_text_only += 1

    # Score: curator-weighted, capped at MAX_LINKS_PER_CURATOR unique domains
    domain_scores          = Counter()
    curator_domain_details = defaultdict(list)  # domain → [(handle, weight)]

    for curator, domains in curator_domains.items():
        curator_weight = curator_save_counts.get(curator, 1)

        # Top MAX_LINKS_PER_CURATOR unique domains from this curator.
        # Most-linked domains get priority if curator links to more than 3.
        top_domains = Counter(domains).most_common(MAX_LINKS_PER_CURATOR)

        for domain, _ in top_domains:
            domain_scores[domain] += curator_weight
            curator_domain_details[domain].append((curator, curator_weight))

    stats = {
        'total_bookmarks':   len(tweets),
        'tweets_with_links': tweets_with_links,
        'tweets_text_only':  tweets_text_only,
        'curators_with_links': len(curator_domains),
        'total_curators':    len(curator_save_counts),
        'unique_domains':    len(domain_scores),
        'domains_2plus':     sum(1 for s in domain_scores.values() if s >= MIN_DOMAIN_SCORE),
    }

    return domain_scores, curator_domain_details, stats


# ── Bootstrap loader ───────────────────────────────────────────────────────

def load_bootstrap_tweet_ids() -> tuple[list[str], dict[str, str]]:
    """
    Load tweet IDs and curator handles from x_bootstrap data stored in preferences.

    x_bootstrap.py stores all imported bookmarks under:
      feedback_history['x_bootstrap_<date>']['saved']
    Each entry: {'article_id': 'x_{tweet_id}', 'source': 'X/@handle', ...}

    Returns:
      tweet_ids  (list[str]): tweet IDs to fetch
      id_to_curator (dict): tweet_id → 'X/@handle' for weighting
    """
    prefs = json.loads(PREFS_PATH.read_text())
    fh    = prefs.get('feedback_history', {})

    tweet_ids      = []
    id_to_curator  = {}

    for key, val in fh.items():
        if not key.startswith('x_bootstrap_'):
            continue
        saved = val.get('saved', [])
        for entry in saved:
            article_id = entry.get('article_id', '')
            source     = entry.get('source', '')
            if article_id.startswith('x_') and source:
                tid = article_id[2:]   # strip 'x_' prefix
                tweet_ids.append(tid)
                id_to_curator[tid] = source

    return tweet_ids, id_to_curator


# ── Cache ──────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    """Load processed tweet IDs per folder."""
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict):
    """Persist processed tweet IDs per folder."""
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


# ── Output ─────────────────────────────────────────────────────────────────

def print_summary(
    folder_name: str,
    domain_scores: Counter,
    curator_domain_details: dict,
    stats: dict,
    verbose: bool = False
):
    """Print enrichment summary to stdout."""
    print()
    print(f"X Adapter — domain enrichment  [{folder_name}]")
    print("─" * 50)
    print(f"Bookmarks in folder:     {stats['total_bookmarks']}")
    print(f"Tweets with links:       {stats['tweets_with_links']}")
    print(f"Tweets text-only:        {stats['tweets_text_only']}")
    print(f"Curators with links:     {stats['curators_with_links']} of {stats['total_curators']}")
    print(f"Unique domains found:    {stats['unique_domains']}")
    print(f"Domains scoring {MIN_DOMAIN_SCORE}+:      {stats['domains_2plus']}")
    print()

    top = domain_scores.most_common(20)
    if not top:
        print("No domains found.")
        return

    print("Top content domains:")
    for domain, score in top:
        if verbose:
            contributors = sorted(curator_domain_details[domain], key=lambda x: -x[1])[:5]
            contrib_str  = ', '.join(
                f"{c.replace('X/@', '')} x{w}" for c, w in contributors
            )
            print(f"  {domain:<40} +{score:<6} ({contrib_str})")
        else:
            print(f"  {domain:<40} +{score}")

    if len(domain_scores) > 20:
        print(f"  ... and {len(domain_scores) - 20} more domains")


# ── Preferences update ─────────────────────────────────────────────────────

def update_preferences(domain_scores: Counter):
    """
    Merge domain_scores into learned_patterns['domain_signals'].

    domain_signals is separate from preferred_sources:
      - preferred_sources: manually trained (likes/saves in curator briefing)
      - domain_signals:    inferred automatically from X bookmark link ecosystems

    Scores are additive across runs so new bookmarks accumulate over time.
    """
    prefs = json.loads(PREFS_PATH.read_text())
    lp    = prefs.setdefault('learned_patterns', {})

    existing = lp.get('domain_signals', {})
    for domain, score in domain_scores.items():
        existing[domain] = existing.get(domain, 0) + score

    lp['domain_signals'] = existing
    PREFS_PATH.write_text(json.dumps(prefs, indent=2))
    print(f"\n✅ Updated domain_signals ({len(domain_scores)} domains) in learned_patterns.")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    dry_run         = '--dry-run'        in sys.argv
    full            = '--full'           in sys.argv
    verbose         = '--verbose'        in sys.argv
    list_folders    = '--list-folders'   in sys.argv
    from_bootstrap  = '--from-bootstrap' in sys.argv

    # Parse --folder=<name>
    folder_name = None
    for arg in sys.argv:
        if arg.startswith('--folder='):
            folder_name = arg.split('=', 1)[1]
            break

    # Default to --from-bootstrap when no mode is specified
    if not list_folders and not folder_name:
        from_bootstrap = True

    if dry_run:
        print("=" * 50)
        print("🧪 DRY RUN — no files will be written")
        print("=" * 50)

    # Auth — auto-refreshes token if expired (no browser needed after first auth)
    # Only needed for folder mode (OAuth 2.0) or listing folders.
    # Bootstrap mode only uses OAuth 1.0a (via get_x_client inside fetch_tweet_details).
    token   = None
    user_id = None

    if list_folders or folder_name:
        try:
            token = get_valid_token()
        except RuntimeError as e:
            print(f"❌ {e}")
            sys.exit(1)
        print("\nConnecting to X API...")
        user_id = get_my_user_id(token)
        print(f"  Authenticated as user ID: {user_id}")

    # ── --list-folders mode ────────────────────────────────────────────────
    if list_folders:
        print("\nFetching bookmark folders...")
        folders = fetch_bookmark_folders(user_id, token)
        if not folders:
            print("  No bookmark folders found.")
        else:
            print(f"\n  {'NAME':<30} {'ID':<20} DESCRIPTION")
            print(f"  {'─'*30} {'─'*20} {'─'*30}")
            for f in folders:
                name  = f.get('name', '')
                fid   = f.get('id', '')
                desc  = f.get('description', '')[:40]
                print(f"  {name:<30} {fid:<20} {desc}")
            print(f"\n  Use: python x_adapter.py --folder=<name>")
        return

    # ── --from-bootstrap mode (default) ───────────────────────────────────
    if from_bootstrap:
        print("\nLoading stored bookmark IDs from x_bootstrap data...")
        all_ids, id_to_curator = load_bootstrap_tweet_ids()
        print(f"  Found {len(all_ids)} stored bookmark IDs across {len(set(id_to_curator.values()))} curators.")

        if not all_ids:
            print("❌ No x_bootstrap data found in preferences.")
            print("   Run x_bootstrap.py first to import your X bookmarks.")
            sys.exit(1)

        cache_key     = 'bootstrap'
        cache         = {} if full else load_cache()
        processed_ids = set(cache.get(cache_key, []))

        new_ids = [tid for tid in all_ids if tid not in processed_ids]
        if not new_ids and not full:
            print("✅ All bootstrap bookmarks already processed. Use --full to re-run.")
            sys.exit(0)
        if processed_ids and not full:
            print(f"  {len(processed_ids)} already cached. Fetching details for {len(new_ids)} new.")

        print(f"\nFetching tweet details and URL entities for {len(new_ids)} bookmarks...")
        tweets, authors = fetch_tweet_details(new_ids)
        print(f"  Received details for {len(tweets)} tweets.")

        domain_scores, curator_domain_details, stats = build_domain_scores(tweets, authors, id_to_curator)
        print_summary("All X bookmarks (bootstrap)", domain_scores, curator_domain_details, stats, verbose=verbose)

        if dry_run:
            print("\n🧪 Dry run complete — learned_patterns NOT updated.")
            print("   Run without --dry-run to write domain_signals.")
        else:
            update_preferences(domain_scores)
            cached_ids = processed_ids | set(new_ids)
            cache[cache_key] = sorted(cached_ids)
            save_cache(cache)
            print(f"   Cache updated ({len(cached_ids)} processed IDs).")
        return

    # ── --folder=<name> mode ───────────────────────────────────────────────
    print(f"\nLooking up folder: '{folder_name}'...")
    folders = fetch_bookmark_folders(user_id, token)
    match   = next(
        (f for f in folders if f.get('name', '').lower() == folder_name.lower()),
        None
    )
    if not match:
        available = [f.get('name', '') for f in folders]
        print(f"❌ Folder '{folder_name}' not found.")
        print(f"   Available: {available}")
        sys.exit(1)

    folder_id    = match['id']
    folder_label = match['name']
    print(f"  Found: '{folder_label}' (id: {folder_id})")

    cache         = {} if full else load_cache()
    processed_ids = set(cache.get(folder_id, []))

    if full and processed_ids:
        print(f"  --full flag: reprocessing all bookmarks in '{folder_label}'.")

    print(f"\nStep 1 — Fetching tweet IDs from '{folder_label}'...")
    all_ids = fetch_folder_tweet_ids(user_id, folder_id, token)
    print(f"  Total: {len(all_ids)} tweet IDs in folder.")

    new_ids = [tid for tid in all_ids if tid not in processed_ids]
    if not new_ids and not full:
        print("✅ All bookmarks already processed. Use --full to re-run.")
        sys.exit(0)
    if processed_ids and not full:
        print(f"  {len(processed_ids)} already cached. Fetching {len(new_ids)} new.")

    print(f"\nStep 2 — Fetching tweet details and URL entities...")
    tweets, authors = fetch_tweet_details(new_ids)
    print(f"  Received details for {len(tweets)} tweets.")

    domain_scores, curator_domain_details, stats = build_domain_scores(tweets, authors)
    print_summary(folder_label, domain_scores, curator_domain_details, stats, verbose=verbose)

    if dry_run:
        print("\n🧪 Dry run complete — learned_patterns NOT updated.")
        print("   Run without --dry-run to write domain_signals.")
    else:
        update_preferences(domain_scores)
        cached_ids = processed_ids | set(new_ids)
        cache[folder_id] = sorted(cached_ids)
        save_cache(cache)
        print(f"   Cache updated ({len(cached_ids)} processed IDs for '{folder_label}').")


if __name__ == '__main__':
    main()

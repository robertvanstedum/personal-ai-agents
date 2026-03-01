#!/usr/bin/env python3
"""
x_import_archive.py — Phase 3C-alpha: One-time X bookmark archive import.

Parses the X data archive bookmarks.js, extracts the content domains your
trusted curators link to, and writes them into domain_signals in
curator_preferences.json — scoped by knowledge domain (Finance/Geo, Health, etc.).

This is the primary signal source until Phase 3C-beta (x_adapter.py incremental)
is operational. Run it once with the archive, then re-run whenever you request
a fresh archive download from X.

How to get your archive:
  1. Go to x.com → Settings → Your Account → Download an archive of your data
  2. Wait for the email (1–48 hours)
  3. Download and unzip
  4. Copy data/bookmarks.js to this project folder
  5. Run: python3 x_import_archive.py --file=bookmarks.js --dry-run

Folder → Domain mapping (curator_config.py KNOWN_FOLDERS):
  Unknown folders fall back to ACTIVE_DOMAIN (Finance and Geopolitics).
  Add new folder IDs to KNOWN_FOLDERS before running to get clean routing.

Usage:
    python3 x_import_archive.py --file=bookmarks.js --dry-run    # Preview only
    python3 x_import_archive.py --file=bookmarks.js --verbose    # Full breakdown
    python3 x_import_archive.py --file=bookmarks.js              # Write signals
    python3 x_import_archive.py --file=bookmarks.js --full       # Re-process all
"""

import json
import re
import sys
import time
import tweepy
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

from x_auth import get_x_client
from curator_config import ACTIVE_DOMAIN, KNOWN_FOLDERS

# ── Paths ──────────────────────────────────────────────────────────────────
PREFS_PATH = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'
CACHE_PATH = Path(__file__).parent / 'x_import_archive_cache.json'

# ── Domain noise filter ────────────────────────────────────────────────────
SKIP_DOMAINS = {
    'x.com', 'twitter.com', 't.co',
    'pic.twitter.com', 'pbs.twimg.com',
    'youtube.com', 'youtu.be',
    'substack.com',
    'bit.ly', 'tinyurl.com', 'ow.ly',
    'buff.ly',
    'github.com',
}

# ── Constants ──────────────────────────────────────────────────────────────
MAX_LINKS_PER_CURATOR = 3
MIN_DOMAIN_SCORE      = 2


# ── Archive parsing ─────────────────────────────────────────────────────────

def parse_archive(archive_path: str) -> tuple[list[dict], dict[str, int]]:
    """
    Parse bookmarks.js from the X data archive.

    The file is JavaScript, not pure JSON:
      window.YTD.bookmarks.part0 = [{...}, ...];

    Each entry contains:
      {
        "bookmarkId": "...",
        "tweetId": "1234567890",
        "addedAt": "2024-01-15T12:00:00.000Z",
        "bookmarkCollectionId": "1926124453714387081"   ← folder (empty string if unfiled)
      }

    Returns:
      entries       (list[dict]): all raw bookmark entries
      folder_counts (dict): folder_label → count for reporting
    """
    path = Path(archive_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Archive file not found: {archive_path}\n"
            "Download your X archive at: x.com → Settings → Your Account → "
            "Download an archive of your data"
        )

    raw   = path.read_text(encoding='utf-8')
    match = re.search(
        r'window\.YTD\.bookmarks\.part\d+\s*=\s*(\[.*\])\s*;?\s*$',
        raw, re.DOTALL
    )
    if not match:
        raise ValueError(
            f"Could not parse {archive_path}.\n"
            "Expected format: window.YTD.bookmarks.part0 = [{...}, ...];\n"
            "Make sure you're pointing to bookmarks.js from the X data archive."
        )

    entries       = json.loads(match.group(1))
    folder_counts = {}

    for entry in entries:
        collection  = entry.get('bookmarkCollectionId', '')
        label       = KNOWN_FOLDERS.get(collection, 'Unfiled' if not collection else f'Unknown ({collection})')
        folder_counts[label] = folder_counts.get(label, 0) + 1

    return entries, folder_counts


def group_by_domain(entries: list[dict]) -> dict[str, list[str]]:
    """
    Group tweet IDs by their canonical domain label.

    Unknown folder IDs fall back to ACTIVE_DOMAIN.

    Returns: domain_label → list of tweet_id strings
    """
    groups = defaultdict(list)
    for entry in entries:
        tweet_id   = entry.get('tweetId', '')
        collection = entry.get('bookmarkCollectionId', '')
        if not tweet_id:
            continue
        # Folder ID → canonical domain, unknown → ACTIVE_DOMAIN
        label = KNOWN_FOLDERS.get(collection, ACTIVE_DOMAIN)
        groups[label].append(tweet_id)
    return dict(groups)


# ── Tweet entity fetching ──────────────────────────────────────────────────

def fetch_tweet_details(tweet_ids: list[str]) -> tuple[list, dict]:
    """
    Batch-fetch tweet text + URL entities via OAuth 1.0a (100 per call).

    Returns: (tweets list of dicts, authors dict {author_id: username})
    """
    client  = get_x_client()
    results = []
    authors = {}
    total   = (len(tweet_ids) + 99) // 100

    for i in range(0, len(tweet_ids), 100):
        batch     = tweet_ids[i:i + 100]
        batch_num = i // 100 + 1
        print(f"    Batch {batch_num}/{total} ({len(batch)} tweets)...", end=' ', flush=True)

        try:
            resp = client.get_tweets(
                ids=batch,
                tweet_fields=['entities', 'text', 'author_id'],
                expansions=['author_id'],
                user_fields=['username'],
                user_auth=True
            )
        except tweepy.TweepyException as e:
            print(f"⚠️  API error: {e}")
            continue

        if not resp.data:
            print("0 returned")
            continue

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

        print(f"{len(resp.data)} returned")

        if i + 100 < len(tweet_ids):
            time.sleep(1)

    return results, authors


# ── Domain extraction ──────────────────────────────────────────────────────

def extract_domain(expanded_url: str) -> str | None:
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

def build_domain_scores(tweets: list, authors: dict) -> tuple:
    """
    Curator-trust weighted domain scoring.

    curator_weight = number of bookmarks saved from that account.
    Cap: MAX_LINKS_PER_CURATOR unique domains counted per curator.
    """
    def curator_handle(author_id):
        uname = authors.get(author_id, author_id)
        return f'X/@{uname}'

    curator_save_counts = Counter(
        curator_handle(t.get('author_id', '')) for t in tweets
    )
    curator_domains    = defaultdict(list)
    tweets_with_links  = 0
    tweets_text_only   = 0

    for tweet in tweets:
        author_id = tweet.get('author_id', '')
        curator   = curator_handle(author_id)
        entities  = tweet.get('entities') or {}
        urls      = entities.get('urls', []) if isinstance(entities, dict) else []
        domains   = [d for u in urls if (d := extract_domain(u.get('expanded_url', '')))]

        if domains:
            curator_domains[curator].extend(domains)
            tweets_with_links += 1
        else:
            tweets_text_only += 1

    domain_scores          = Counter()
    curator_domain_details = defaultdict(list)

    for curator, domains in curator_domains.items():
        curator_weight = curator_save_counts.get(curator, 1)
        top_domains    = Counter(domains).most_common(MAX_LINKS_PER_CURATOR)
        for domain, _ in top_domains:
            domain_scores[domain] += curator_weight
            curator_domain_details[domain].append((curator, curator_weight))

    stats = {
        'total':             len(tweets),
        'tweets_with_links': tweets_with_links,
        'tweets_text_only':  tweets_text_only,
        'curators_with_links': len(curator_domains),
        'total_curators':    len(curator_save_counts),
        'unique_domains':    len(domain_scores),
        'domains_2plus':     sum(1 for s in domain_scores.values() if s >= MIN_DOMAIN_SCORE),
    }

    return domain_scores, curator_domain_details, stats


# ── Output ─────────────────────────────────────────────────────────────────

def print_domain_summary(
    domain_label: str,
    domain_scores: Counter,
    curator_domain_details: dict,
    stats: dict,
    verbose: bool = False
):
    print()
    print(f"  [{domain_label}]")
    print(f"  {'─' * 48}")
    print(f"  Bookmarks:     {stats['total']}  |  With links: {stats['tweets_with_links']}  |  Text-only: {stats['tweets_text_only']}")
    print(f"  Curators:      {stats['curators_with_links']} with links of {stats['total_curators']} total")
    print(f"  Domains found: {stats['unique_domains']}  |  Scoring {MIN_DOMAIN_SCORE}+: {stats['domains_2plus']}")

    top = domain_scores.most_common(15)
    if not top:
        print("  (No link-bearing bookmarks in this domain)")
        return

    print()
    for domain, score in top:
        if verbose:
            contributors = sorted(curator_domain_details[domain], key=lambda x: -x[1])[:4]
            contrib_str  = ', '.join(f"{c.replace('X/@', '')} x{w}" for c, w in contributors)
            print(f"  {domain:<40} +{score:<5} ({contrib_str})")
        else:
            print(f"  {domain:<40} +{score}")

    if len(domain_scores) > 15:
        print(f"  ... and {len(domain_scores) - 15} more")


# ── Preferences update ─────────────────────────────────────────────────────

def update_preferences(domain_label: str, domain_scores: Counter):
    """
    Merge domain_scores into domain_signals[domain_label] in preferences.

    Nested structure:
      learned_patterns.domain_signals.{domain_label}.{url_domain} = score

    Scores are additive across runs.
    """
    prefs       = json.loads(PREFS_PATH.read_text())
    lp          = prefs.setdefault('learned_patterns', {})
    all_signals = lp.setdefault('domain_signals', {})
    existing    = all_signals.setdefault(domain_label, {})

    for domain, score in domain_scores.items():
        existing[domain] = existing.get(domain, 0) + score

    lp['domain_signals'] = all_signals
    PREFS_PATH.write_text(json.dumps(prefs, indent=2))
    print(f"  ✅ domain_signals['{domain_label}'] updated ({len(domain_scores)} domains)")


# ── Cache ──────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict):
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    dry_run = '--dry-run' in sys.argv
    verbose = '--verbose' in sys.argv
    full    = '--full'    in sys.argv

    archive_path = None
    for arg in sys.argv:
        if arg.startswith('--file='):
            archive_path = arg.split('=', 1)[1]

    if not archive_path:
        print("Usage:")
        print("  python3 x_import_archive.py --file=bookmarks.js --dry-run")
        print("  python3 x_import_archive.py --file=bookmarks.js --verbose")
        print("  python3 x_import_archive.py --file=bookmarks.js")
        print()
        print("Get your archive: x.com → Settings → Your Account → Download an archive of your data")
        sys.exit(0)

    if dry_run:
        print("=" * 56)
        print("🧪 DRY RUN — no files will be written")
        print("=" * 56)

    # ── Parse archive ──────────────────────────────────────────────────────
    print(f"\nParsing archive: {archive_path}")
    try:
        entries, folder_counts = parse_archive(archive_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ {e}")
        sys.exit(1)

    print(f"  Total bookmarks: {len(entries)}")
    print()
    print("  Folder distribution:")
    for label, count in sorted(folder_counts.items(), key=lambda x: -x[1]):
        mapped = '✓' if any(label == v for v in KNOWN_FOLDERS.values()) else '→ ACTIVE_DOMAIN fallback'
        print(f"    {label:<35} {count:>4}  {mapped}")

    # ── Group by domain ────────────────────────────────────────────────────
    groups = group_by_domain(entries)
    print()
    print("  Domain grouping:")
    for domain_label, ids in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"    {domain_label:<35} {len(ids):>4} bookmarks")

    # ── Load cache ─────────────────────────────────────────────────────────
    cache = {} if full else load_cache()

    # ── Process each domain ────────────────────────────────────────────────
    all_written = {}

    for domain_label, tweet_ids in groups.items():
        print(f"\n{'─' * 56}")
        print(f"Processing: {domain_label} ({len(tweet_ids)} bookmarks)")

        cache_key     = f'archive_{domain_label}'
        processed_ids = set(cache.get(cache_key, []))
        new_ids       = [tid for tid in tweet_ids if tid not in processed_ids]

        if not new_ids and not full:
            print(f"  ✅ All {len(tweet_ids)} already processed — skipping. Use --full to reprocess.")
            continue

        if processed_ids and not full:
            print(f"  {len(processed_ids)} cached, fetching {len(new_ids)} new.")

        print(f"  Fetching tweet details ({len(new_ids)} tweets)...")
        tweets, authors = fetch_tweet_details(new_ids)
        print(f"  Received: {len(tweets)} tweets")

        domain_scores, curator_domain_details, stats = build_domain_scores(tweets, authors)
        print_domain_summary(domain_label, domain_scores, curator_domain_details, stats, verbose=verbose)

        if not dry_run:
            update_preferences(domain_label, domain_scores)
            cached_ids           = processed_ids | set(new_ids)
            cache[cache_key]     = sorted(cached_ids)
            all_written[domain_label] = len(domain_scores)

    # ── Finalize ───────────────────────────────────────────────────────────
    print()
    print("=" * 56)
    if dry_run:
        print("🧪 Dry run complete — nothing written.")
        print("   Run without --dry-run to commit domain_signals.")
    else:
        save_cache(cache)
        print("✅ Archive import complete.")
        for label, count in all_written.items():
            print(f"   {label}: {count} domains written")
        print()
        print("Next steps:")
        print("  python3 show_profile.py          # verify signals appear")
        print("  python3 curator_rss_v2.py --dry-run --model=xai  # verify LLM prompt")


if __name__ == '__main__':
    main()

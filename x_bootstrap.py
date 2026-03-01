#!/usr/bin/env python3
"""
x_bootstrap.py - One-time historical ingestion of X bookmarks.

Fetches all bookmarks and treats each as a "Save" signal to seed
the learning loop. Updates preferred_sources in curator_preferences.json.

Usage:
    python x_bootstrap.py           # dry-run: shows what would be imported
    python x_bootstrap.py --import  # actually write to learning loop
"""

import json
import sys
import time
import keyring
import tweepy
from datetime import datetime, timezone
from pathlib import Path

PREFS_PATH   = Path.home() / '.openclaw' / 'workspace' / 'curator_preferences.json'
DRY_RUN      = '--import' not in sys.argv


def get_client():
    oauth2_token = keyring.get_password('x_oauth2', 'access_token')
    if not oauth2_token:
        raise RuntimeError("No OAuth 2.0 access token. Run x_oauth2_authorize.py first.")
    return tweepy.Client(bearer_token=oauth2_token)


def fetch_all_bookmarks(client):
    """Fetch all bookmarks via pagination. Returns (tweets, authors dict)."""
    me = client.get_me(user_auth=False)
    print(f"Fetching all bookmarks for @{me.data.username}...\n")

    all_tweets = []
    authors    = {}
    next_token = None
    page       = 0

    while True:
        page += 1
        kwargs = dict(
            max_results=100,
            tweet_fields=['text', 'author_id', 'created_at', 'entities'],
            expansions=['author_id'],
            user_fields=['username'],
        )
        if next_token:
            kwargs['pagination_token'] = next_token

        resp = client.get_bookmarks(**kwargs)

        if resp.includes and 'users' in resp.includes:
            for u in resp.includes['users']:
                authors[u.id] = u.username

        if resp.data:
            all_tweets.extend(resp.data)
            print(f"  Page {page}: {len(resp.data)} bookmarks fetched (running total: {len(all_tweets)})")

        # Check for more pages
        if resp.meta and resp.meta.get('next_token'):
            next_token = resp.meta['next_token']
            time.sleep(1)  # Courtesy pause for rate limits
        else:
            break

    return all_tweets, authors


def normalize(tweet, authors):
    """Normalize a tweet to the standard article schema."""
    username = authors.get(tweet.author_id, str(tweet.author_id))
    text     = tweet.text.strip()
    return {
        'article_id':   f"x_{tweet.id}",
        'title':        f"@{username}: {text[:80]}",
        'source':       f"X/@{username}",
        'content':      text,
        'url':          f"https://x.com/{username}/status/{tweet.id}",
        'content_type': 'social_post',
        'fetched_at':   str(tweet.created_at)[:10] if tweet.created_at else '',
    }


def main():
    client = get_client()
    tweets, authors = fetch_all_bookmarks(client)

    print(f"\nTotal: {len(tweets)} bookmarks fetched.\n")

    # Normalize all tweets
    articles = [normalize(t, authors) for t in tweets]

    # Count by source for summary
    source_counts = {}
    for art in articles:
        source_counts[art['source']] = source_counts.get(art['source'], 0) + 1

    print("Top accounts in your bookmarks:")
    for src, count in sorted(source_counts.items(), key=lambda x: -x[1])[:20]:
        bar = '#' * min(count, 20)
        print(f"  {count:3d}  {bar:20s}  {src}")

    print(f"\nSample (first 5):")
    for art in articles[:5]:
        print(f"  [{art['source']}] {art['title'][:70]}")

    if DRY_RUN:
        print(f"\n--- DRY-RUN COMPLETE ---")
        print(f"Would import {len(articles)} bookmarks as 'saved' signals.")
        print(f"Run with --import to write to the learning loop.")
        return

    # --- IMPORT ---
    print(f"\nImporting {len(articles)} bookmarks...")

    prefs = json.loads(PREFS_PATH.read_text())
    lp      = prefs.setdefault('learned_patterns', {})
    history = prefs.setdefault('feedback_history', {})

    preferred_sources = lp.setdefault('preferred_sources', {})

    # Collect already-imported X article IDs to avoid duplicates
    existing_ids = set()
    for day in history.values():
        for item in day.get('saved', []):
            if item.get('article_id', '').startswith('x_'):
                existing_ids.add(item['article_id'])

    # Write to feedback_history under bootstrap key
    today     = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    day_key   = f"x_bootstrap_{today}"
    day_entry = history.setdefault(day_key, {'liked': [], 'disliked': [], 'saved': []})

    new_count = 0
    for art in articles:
        if art['article_id'] in existing_ids:
            continue

        day_entry['saved'].append({
            'article_id': art['article_id'],
            'title':      art['title'],
            'source':     art['source'],
        })

        # Each bookmark = +1 to source score (same weight as a Telegram Save)
        preferred_sources[art['source']] = preferred_sources.get(art['source'], 0) + 1
        new_count += 1

    # Update metadata
    lp['preferred_sources'] = preferred_sources
    lp['sample_size']       = lp.get('sample_size', 0) + new_count
    lp['last_updated']      = datetime.now(timezone.utc).isoformat()

    PREFS_PATH.write_text(json.dumps(prefs, indent=2))

    skipped = len(articles) - new_count
    print(f"Imported {new_count} new signals. ({skipped} already existed, skipped.)")
    print(f"\nTop sources now in learning loop:")
    for src, score in sorted(preferred_sources.items(), key=lambda x: -x[1])[:15]:
        print(f"  +{score:3d}  {src}")

    print(f"\nDone. Run 'python show_profile.py' to see full updated profile.")


if __name__ == '__main__':
    main()

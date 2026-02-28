#!/usr/bin/env python3
"""
x_bookmarks_test.py - Verify bookmark access before building bootstrap.

Fetches the first 5 bookmarks using OAuth 1.0a user context.
Dry-run only - does not write anything.

Usage:
    python x_bookmarks_test.py
"""

import keyring
import tweepy


if __name__ == '__main__':
    # OAuth 2.0 PKCE token — must be passed as bearer_token for get_bookmarks to work
    oauth2_token = keyring.get_password('x_oauth2', 'access_token')
    if not oauth2_token:
        print("ERROR: No OAuth 2.0 access token. Run x_oauth2_authorize.py first.")
        exit(1)

    client = tweepy.Client(bearer_token=oauth2_token)

    # Get user ID
    me = client.get_me(user_auth=False)
    user_id = me.data.id
    print(f"Connected as @{me.data.username} (id: {user_id})\n")

    # Fetch first 5 bookmarks with user context auth
    print("Fetching bookmarks (dry-run, first 5)...\n")
    response = client.get_bookmarks(
        max_results=5,
        tweet_fields=['text', 'author_id', 'created_at'],
        expansions=['author_id'],
        user_fields=['username'],
    )

    if not response.data:
        print("No bookmarks returned - check API permissions")
    else:
        users = {}
        if response.includes and 'users' in response.includes:
            for u in response.includes['users']:
                users[u.id] = u.username

        for i, tweet in enumerate(response.data, 1):
            author = users.get(tweet.author_id, '?')
            preview = tweet.text[:100].replace('\n', ' ')
            print(f"  [{i}] @{author}")
            print(f"       {preview}...")
            print()

        print(f"Returned {len(response.data)} bookmarks.")

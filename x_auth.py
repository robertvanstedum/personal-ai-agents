#!/usr/bin/env python3
"""
x_auth.py - Verify X API credentials stored in keychain.

Loads OAuth 1.0a credentials and makes a simple API call
to confirm authentication works before building bootstrap logic.

Usage:
    python x_auth.py
"""

import keyring
import tweepy


def get_x_client() -> tweepy.Client:
    """Load X credentials from keychain and return authenticated client."""
    consumer_key        = keyring.get_password('x_api', 'consumer_key')
    consumer_secret     = keyring.get_password('x_api', 'consumer_secret')
    access_token        = keyring.get_password('x_api', 'access_token')
    access_token_secret = keyring.get_password('x_api', 'access_token_secret')

    missing = [k for k, v in {
        'consumer_key':        consumer_key,
        'consumer_secret':     consumer_secret,
        'access_token':        access_token,
        'access_token_secret': access_token_secret,
    }.items() if not v]

    if missing:
        raise RuntimeError(f"Missing credentials in keychain: {missing}")

    return tweepy.Client(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )


if __name__ == '__main__':
    print("Loading credentials from keychain...")
    client = get_x_client()
    print("Credentials loaded. Testing API call...")

    me = client.get_me()

    if me and me.data:
        print(f"Auth OK - connected as @{me.data.username} (id: {me.data.id})")
    else:
        print("Auth failed - no data returned")

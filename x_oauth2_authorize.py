#!/usr/bin/env python3
"""
x_oauth2_authorize.py - One-time OAuth 2.0 PKCE authorization for X bookmarks.

Run this once to authorize bookmark access.
Stores the access token in keychain for all future use.

Usage:
    python x_oauth2_authorize.py
"""

import os
import keyring
import tweepy

# Allow HTTP for localhost redirect (safe for local dev — exchange still goes over HTTPS)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'


def main():
    client_id     = keyring.get_password('x_oauth2', 'client_id')
    client_secret = keyring.get_password('x_oauth2', 'client_secret')

    if not client_id or not client_secret:
        print("ERROR: OAuth 2.0 credentials not found in keychain.")
        print("Run store_x_oauth2.py first.")
        return

    # Set up OAuth 2.0 handler with bookmark scope
    handler = tweepy.OAuth2UserHandler(
        client_id=client_id,
        redirect_uri="http://localhost:3000/callback",
        scope=["bookmark.read", "tweet.read", "users.read"],
        client_secret=client_secret,
    )

    # Step 1: Get authorization URL
    auth_url = handler.get_authorization_url()
    print("\n--- OAuth 2.0 Authorization ---\n")
    print("1. Open this URL in your browser:\n")
    print(f"   {auth_url}\n")
    print("2. Click 'Authorize app'")
    print("3. You will be redirected to localhost:3000 (page won't load - that's fine)")
    print("4. Copy the FULL URL from your browser address bar and paste it below\n")

    response_url = input("Paste the full redirect URL: ").strip()

    # Step 2: Exchange code for token
    print("\nExchanging code for access token...")
    token = handler.fetch_token(response_url)

    access_token = token.get('access_token')
    if not access_token:
        print("ERROR: No access token returned.")
        return

    # Step 3: Store in keychain
    keyring.set_password('x_oauth2', 'access_token', access_token)
    print(f"Access token stored in keychain: {access_token[:8]}...")
    print("\nAuthorization complete. You can now run x_bookmarks_test.py")


if __name__ == '__main__':
    main()

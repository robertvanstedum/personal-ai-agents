#!/usr/bin/env python3
"""
x_oauth2_authorize.py - OAuth 2.0 PKCE authorization for X bookmarks.

Run this once to authorize. Stores access_token, refresh_token, and
expiry in a JSON file on the curator data volume (safe in Docker).
client_id and client_secret come from SSM/env (never rotate, no file needed).

Token file: $CURATOR_DATA_DIR/x_oauth2_tokens.json
  (default: data/curator/x_oauth2_tokens.json in repo)

Usage:
    python x_oauth2_authorize.py          # Full browser auth (run once)
    python x_oauth2_authorize.py --refresh # Refresh token without browser
    python x_oauth2_authorize.py --status  # Check token status
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import requests
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs


_DATA_DIR = Path(os.environ.get("CURATOR_DATA_DIR", str(Path(__file__).parent / "data" / "curator")))
_TOKEN_FILE = _DATA_DIR / "x_oauth2_tokens.json"


def _get_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret). Lookup: env → keyring → SSM."""
    client_id = os.environ.get("X_CLIENT_ID")
    client_secret = os.environ.get("X_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret

    # Try keyring (Mac dev environment)
    try:
        import keyring as _kr
        client_id = client_id or _kr.get_password('x_oauth2', 'client_id')
        client_secret = client_secret or _kr.get_password('x_oauth2', 'client_secret')
        if client_id and client_secret:
            return client_id, client_secret
    except Exception:
        pass

    # Try SSM (EC2 production)
    try:
        import boto3
        prefix = os.environ.get("SSM_PREFIX", "/minimoi/production/")
        ssm = boto3.client('ssm', region_name='us-east-1')
        client_id = ssm.get_parameter(Name=f"{prefix}x_client_id", WithDecryption=True)['Parameter']['Value']
        client_secret = ssm.get_parameter(Name=f"{prefix}x_client_secret", WithDecryption=True)['Parameter']['Value']
        return client_id, client_secret
    except Exception:
        pass

    return "", ""


def _load_token_file() -> dict:
    """Load rotating tokens from JSON file on the data volume.
    Falls back to keyring on first run (Mac migration path)."""
    try:
        return json.loads(_TOKEN_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Migration: read from keyring and write to file so future runs use the file
    try:
        import keyring as _kr
        access  = _kr.get_password('x_oauth2', 'access_token')
        refresh = _kr.get_password('x_oauth2', 'refresh_token')
        expires = _kr.get_password('x_oauth2', 'expires_at')
        if access:
            data = {k: v for k, v in {
                'access_token': access, 'refresh_token': refresh, 'expires_at': expires
            }.items() if v}
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            _TOKEN_FILE.write_text(json.dumps(data, indent=2))
            print(f"Migrated tokens from keyring → {_TOKEN_FILE}")
            return data
    except Exception:
        pass
    return {}


def _save_token_file(data: dict):
    """Persist rotating tokens to JSON file, merging with existing data."""
    existing = _load_token_file()
    existing.update(data)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(json.dumps(existing, indent=2))


def get_stored_tokens() -> dict:
    """Load all OAuth 2.0 tokens. client_id/secret from env/keyring/SSM; rotating tokens from file."""
    client_id, client_secret = _get_credentials()
    file_data = _load_token_file()
    return {
        'client_id':     client_id,
        'client_secret': client_secret,
        'access_token':  file_data.get('access_token'),
        'refresh_token': file_data.get('refresh_token'),
        'expires_at':    file_data.get('expires_at'),
    }


def store_tokens(access_token: str, refresh_token: str | None, expires_in: int | None):
    """Persist rotating tokens to the data volume file (not keyring)."""
    data = {}
    if access_token:
        data['access_token'] = access_token
    if refresh_token:
        data['refresh_token'] = refresh_token
    if expires_in:
        data['expires_at'] = str(int(time.time()) + expires_in)
    _save_token_file(data)

    print(f"  access_token:  {access_token[:12]}... ✅")
    if refresh_token:
        print(f"  refresh_token: {refresh_token[:12]}... ✅")
    if expires_in:
        print(f"  expires_in:    {expires_in // 3600}h {(expires_in % 3600) // 60}m")
    print(f"  token file:    {_TOKEN_FILE}")


def refresh_access_token(tokens: dict) -> str:
    """
    Use stored refresh_token to get a new access_token without browser interaction.
    Refresh tokens on X are single-use — always store the new one immediately.
    Returns new access_token.
    """
    refresh_token = tokens.get('refresh_token')
    client_id     = tokens.get('client_id')
    client_secret = tokens.get('client_secret')

    if not refresh_token:
        raise RuntimeError("No refresh_token stored. Run full auth: python x_oauth2_authorize.py")

    print("Refreshing access token...")
    resp = requests.post(
        'https://api.x.com/2/oauth2/token',
        data={
            'grant_type':    'refresh_token',
            'refresh_token': refresh_token,
            'client_id':     client_id,
            'client_secret': client_secret,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Token refresh failed ({resp.status_code}): {resp.text}\n"
            "Run full auth: python x_oauth2_authorize.py"
        )

    data = resp.json()
    new_access  = data.get('access_token')
    new_refresh = data.get('refresh_token')   # X rotates refresh tokens — store immediately
    expires_in  = data.get('expires_in')

    if not new_access:
        raise RuntimeError(f"No access_token in refresh response: {data}")

    print("Tokens refreshed:")
    store_tokens(new_access, new_refresh, expires_in)
    return new_access


def get_valid_token() -> str:
    """
    Return a valid access token, auto-refreshing if expired.
    Used by x_adapter.py and x_bootstrap.py — no manual auth needed after first run.
    """
    tokens     = get_stored_tokens()
    access     = tokens.get('access_token')
    expires_at = tokens.get('expires_at')

    if not access:
        raise RuntimeError("No access token. Run: python x_oauth2_authorize.py")

    # Refresh if expired or within 5 minutes of expiry
    if expires_at:
        remaining = int(expires_at) - int(time.time())
        if remaining < 300:   # < 5 minutes left
            print(f"Token expires in {remaining}s — refreshing...")
            return refresh_access_token(tokens)

    return access


def full_auth_flow():
    """
    Run the full browser-based OAuth 2.0 PKCE flow using raw requests.
    Avoids Tweepy's OAuth2UserHandler to prevent MismatchingStateError.
    """
    client_id, client_secret = _get_credentials()
    redirect_uri  = "http://localhost:3000/callback"

    if not client_id or not client_secret:
        print("ERROR: OAuth 2.0 credentials not found.")
        print("Set X_CLIENT_ID / X_CLIENT_SECRET env vars, or store in keyring / SSM.")
        return

    # ── PKCE: generate code_verifier + code_challenge ──────────────────────
    code_verifier  = secrets.token_urlsafe(64)
    digest         = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
    state          = secrets.token_urlsafe(16)

    # ── Build authorization URL ─────────────────────────────────────────────
    params = {
        'response_type':         'code',
        'client_id':             client_id,
        'redirect_uri':          redirect_uri,
        'scope':                 'bookmark.read tweet.read users.read offline.access',
        'state':                 state,
        'code_challenge':        code_challenge,
        'code_challenge_method': 'S256',
    }
    auth_url = 'https://twitter.com/i/oauth2/authorize?' + urlencode(params)

    print("\n--- OAuth 2.0 Authorization (PKCE) ---\n")
    print("1. Open this URL in your browser:\n")
    print(f"   {auth_url}\n")
    print("2. Click 'Authorize app'")
    print("3. You will be redirected to localhost:3000 (page won't load — that's fine)")
    print("4. Copy the FULL URL from your browser address bar and paste it below\n")

    response_url = input("Paste the full redirect URL: ").strip()

    # ── Verify state to guard against CSRF ─────────────────────────────────
    qs             = parse_qs(urlparse(response_url).query)
    returned_state = qs.get('state', [None])[0]
    code           = qs.get('code',  [None])[0]

    if returned_state != state:
        print(f"❌ State mismatch (expected {state}, got {returned_state}). Possible CSRF.")
        return
    if not code:
        print("❌ No authorization code found in redirect URL.")
        return

    # ── Exchange code for tokens ────────────────────────────────────────────
    print("\nExchanging code for tokens...")
    resp = requests.post(
        'https://api.x.com/2/oauth2/token',
        data={
            'grant_type':    'authorization_code',
            'code':          code,
            'redirect_uri':  redirect_uri,
            'code_verifier': code_verifier,
            'client_id':     client_id,
            'client_secret': client_secret,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )

    if resp.status_code != 200:
        print(f"❌ Token exchange failed ({resp.status_code}): {resp.text}")
        return

    data          = resp.json()
    access_token  = data.get('access_token')
    refresh_token = data.get('refresh_token')
    expires_in    = data.get('expires_in')

    if not access_token:
        print(f"❌ No access_token in response: {data}")
        return

    print("\nStoring tokens in token file:")
    store_tokens(access_token, refresh_token, expires_in)

    if not refresh_token:
        print("\n⚠️  No refresh_token returned.")
        print("   Check that 'offline.access' scope is enabled in your X app settings")
        print("   at developer.twitter.com → your app → User authentication settings.")
    else:
        print("\n✅ Authorization complete. Future token refreshes are automatic.")


def show_status():
    """Print current token status."""
    tokens     = get_stored_tokens()
    access     = tokens.get('access_token')
    refresh    = tokens.get('refresh_token')
    expires_at = tokens.get('expires_at')

    print("\n--- OAuth 2.0 Token Status ---")
    print(f"  token file:    {_TOKEN_FILE} ({'exists' if _TOKEN_FILE.exists() else 'MISSING'})")
    print(f"  access_token:  {'✅ stored' if access else '❌ missing'}")
    print(f"  refresh_token: {'✅ stored' if refresh else '❌ missing (re-auth needed)'}")
    if expires_at:
        remaining = int(expires_at) - int(time.time())
        if remaining > 0:
            h, m = divmod(remaining // 60, 60)
            print(f"  expires_in:    {h}h {m}m remaining")
        else:
            print(f"  expires_in:    ❌ expired {abs(remaining) // 60}m ago")
    else:
        print("  expires_in:    unknown (no expiry stored)")


def main():
    if '--status' in sys.argv:
        show_status()
    elif '--refresh' in sys.argv:
        tokens = get_stored_tokens()
        try:
            refresh_access_token(tokens)
            print(f"\n✅ Token refreshed successfully.")
        except RuntimeError as e:
            print(f"❌ {e}")
    else:
        full_auth_flow()


if __name__ == '__main__':
    main()

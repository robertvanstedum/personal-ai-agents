"""
get_secret.py — credential helper for local dev and AWS production.

On Mac (local dev): reads from macOS Keychain via keyring — unchanged.
On EC2 (production): reads from environment variable injected by AWS
Secrets Manager. Keyring is never called.

Usage:
    from get_secret import get_secret
    token = get_secret("TELEGRAM_BOT_TOKEN", "telegram", "bot_token")
"""

import os


def get_secret(env_var: str, keyring_service: str, keyring_account: str) -> str | None:
    value = os.environ.get(env_var)
    if value:
        return value
    try:
        import keyring
        return keyring.get_password(keyring_service, keyring_account)
    except Exception:
        return None

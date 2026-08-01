"""
get_secret.py — credential helper for local dev and AWS production.

On Mac (local dev): reads env var first, then macOS Keychain via keyring.
On EC2 (production): reads env var first, then AWS SSM Parameter Store.

SSM path convention: /minimoi/production/{key.lower()}

Usage:
    from core.get_secret import get_secret
    token = get_secret("TELEGRAM_BOT_TOKEN", "telegram", "bot_token")
"""

import os


# Canonical platform secret names may temporarily resolve older production
# names while credentials are migrated. Callers should always request the
# canonical name; aliases belong here rather than in individual domains.
SECRET_ALIASES = {
    "XAI_API_KEY": ("GROK_API_KEY",),
}


def get_secret(key: str, keyring_service: str = None, keyring_account: str = None) -> str:
    candidate_keys = (key, *SECRET_ALIASES.get(key, ()))

    for candidate_key in candidate_keys:
        value = os.environ.get(candidate_key)
        if value:
            return value

    if keyring_service and keyring_account:
        try:
            import keyring
            val = keyring.get_password(keyring_service, keyring_account)
            if val:
                return val
        except Exception:
            pass

    try:
        import boto3
        ssm = boto3.client("ssm", region_name="us-east-1")
        last_error = None
        for candidate_key in candidate_keys:
            param_name = f"/minimoi/production/{candidate_key.lower()}"
            try:
                response = ssm.get_parameter(Name=param_name, WithDecryption=True)
                return response["Parameter"]["Value"]
            except Exception as e:
                last_error = e
        raise last_error
    except Exception as e:
        raise RuntimeError(
            f"Could not retrieve secret '{key}' from env, keyring, or SSM "
            f"(path: /minimoi/production/{key.lower()}): {e}"
        )

"""
get_secret.py — credential helper for local dev and AWS production.

On Mac (local dev): reads env var first, then macOS Keychain via keyring.
On EC2 (production): reads env var first, then AWS SSM Parameter Store.

SSM path convention: /minimoi/production/{key.lower()}

Usage:
    from get_secret import get_secret
    token = get_secret("TELEGRAM_BOT_TOKEN", "telegram", "bot_token")
"""

import os


def get_secret(key: str, keyring_service: str = None, keyring_account: str = None) -> str:
    value = os.environ.get(key)
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
        param_name = f"/minimoi/production/{key.lower()}"
        response = ssm.get_parameter(Name=param_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except Exception as e:
        raise RuntimeError(
            f"Could not retrieve secret '{key}' from env, keyring, or SSM "
            f"(path: /minimoi/production/{key.lower()}): {e}"
        )

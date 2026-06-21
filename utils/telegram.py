"""
utils/telegram.py — Role-aware Telegram token helpers.

Lookup order for tokens: env var → macOS Keychain (keyring) → AWS SSM.
SSM path is role-aware:
  MINIMOI_ROLE=production  →  /minimoi/production/
  MINIMOI_ROLE=standby     →  /minimoi/test/

On EC2 (production), the instance role grants SSM read — no IAM key needed.
On Mac (standby), sends are suppressed by is_production() guards before the
token is consumed, so SSM inaccessibility on Mac is harmless.
"""
import os


def _ssm_prefix() -> str:
    role = os.environ.get('MINIMOI_ROLE', 'production')
    env = 'production' if role == 'production' else 'test'
    return f'/minimoi/{env}/'


def _get_token(ssm_key: str, keyring_service: str, keyring_account: str, env_var: str) -> str:
    """Lookup: env var → keyring → SSM. Returns '' on all failures."""
    val = os.environ.get(env_var)
    if val:
        return val
    try:
        import keyring
        val = keyring.get_password(keyring_service, keyring_account)
        if val:
            return val
    except Exception:
        pass
    try:
        import boto3
        ssm = boto3.client('ssm', region_name='us-east-1')
        name = f'{_ssm_prefix()}{ssm_key}'
        resp = ssm.get_parameter(Name=name, WithDecryption=True)
        return resp['Parameter']['Value']
    except Exception:
        return ''


def get_system_token() -> str:
    """Token for minimoi_system_bot — briefings, alerts, !ops commands."""
    return _get_token(
        ssm_key='telegram_system_bot_token',
        keyring_service='telegram',
        keyring_account='bot_token',
        env_var='TELEGRAM_SYSTEM_BOT_TOKEN',
    )


def get_agent_token() -> str:
    """Token for minimoi_agent_bot — CoS + OpenClaw conversational."""
    return _get_token(
        ssm_key='telegram_agent_bot_token',
        keyring_service='telegram',
        keyring_account='agent_bot_token',
        env_var='TELEGRAM_AGENT_BOT_TOKEN',
    )


def get_chat_id() -> str:
    """Telegram chat ID — env var first, then keyring."""
    val = os.environ.get('TELEGRAM_CHAT_ID')
    if val:
        return val
    try:
        import keyring
        val = keyring.get_password('telegram', 'chat_id')
        if val:
            return val
    except Exception:
        pass
    return ''

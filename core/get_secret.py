"""
get_secret.py — credential helper for local dev and AWS production.

Default mode (unchanged): reads env var first, then tries macOS Keychain,
then falls through to AWS SSM Parameter Store, whichever succeeds first.
This permissive chain is fine for secrets that are the same value in both
places or where a stale/missing local entry falling through to SSM is
harmless (e.g. an API key shared across environments).

environment_scoped=True is for secrets that must NEVER cross environments
— most notably DATABASE_URL, where dev and production point at genuinely
different databases with different passwords. With this flag:
  - on dev (MINIMOI_ROLE=standby): only env var, then Keychain are tried.
    SSM is never consulted, so a missing/misconfigured dev Keychain entry
    cannot silently resolve to production's secret.
  - on production (MINIMOI_ROLE=production): only env var, then SSM are
    tried. Keychain is never consulted.
  - if MINIMOI_ROLE is unset or holds any other value: refuses outright,
    before touching either store. utils.role.is_production() defaults to
    True when MINIMOI_ROLE is absent (by design, so EC2 works without
    explicit setup) — reusing that default here would mean a fresh dev
    checkout with no MINIMOI_ROLE set could silently query production's
    SSM. environment_scoped therefore requires the role to be named
    explicitly and does not fall back to that default.
Either path raises immediately if nothing is found, rather than trying
the other environment's source.

SSM path convention: /minimoi/production/{key.lower()}

Usage:
    from core.get_secret import get_secret
    token = get_secret("TELEGRAM_BOT_TOKEN", "telegram", "bot_token")
    db_url = get_secret("DATABASE_URL", "minimoi-dev-db", "database_url", environment_scoped=True)
"""

import os

_RECOGNIZED_ROLES = ("production", "standby")


def _explicit_environment_role() -> str:
    """Require MINIMOI_ROLE to be one of the recognized, explicit values.

    Deliberately does not call utils.role.is_production(): that function
    defaults to 'production' when MINIMOI_ROLE is unset, which is correct
    for its own purpose (EC2 works with zero setup) but wrong here — a
    fresh dev checkout with no MINIMOI_ROLE would then silently resolve
    environment_scoped secrets against production's SSM instead of
    refusing. Only a role this function has actually seen named may pass."""
    role = os.environ.get("MINIMOI_ROLE")
    if role not in _RECOGNIZED_ROLES:
        raise RuntimeError(
            f"environment_scoped secret lookup requires MINIMOI_ROLE to be "
            f"explicitly set to one of {_RECOGNIZED_ROLES!r}, got {role!r}. "
            f"Refusing to guess which environment's secret store to use."
        )
    return role


def _from_keyring(keyring_service: str | None, keyring_account: str | None) -> str | None:
    if not keyring_service or not keyring_account:
        return None
    try:
        import keyring
        return keyring.get_password(keyring_service, keyring_account) or None
    except Exception:
        return None


def _from_ssm(key: str) -> str:
    import boto3
    ssm = boto3.client("ssm", region_name="us-east-1")
    param_name = f"/minimoi/production/{key.lower()}"
    response = ssm.get_parameter(Name=param_name, WithDecryption=True)
    return response["Parameter"]["Value"]


def get_secret(
    key: str,
    keyring_service: str = None,
    keyring_account: str = None,
    *,
    environment_scoped: bool = False,
) -> str:
    value = os.environ.get(key)
    if value:
        return value

    if environment_scoped:
        role = _explicit_environment_role()
        if role == "production":
            try:
                return _from_ssm(key)
            except Exception as e:
                raise RuntimeError(
                    f"Could not retrieve production secret '{key}' from env or SSM "
                    f"(path: /minimoi/production/{key.lower()}). Dev Keychain is not "
                    f"consulted for environment_scoped secrets on production. {e}"
                ) from e
        val = _from_keyring(keyring_service, keyring_account)
        if val:
            return val
        raise RuntimeError(
            f"Could not retrieve dev secret '{key}' from env or Keychain "
            f"(service={keyring_service!r}, account={keyring_account!r}). "
            f"SSM is not consulted for environment_scoped secrets on dev — "
            f"run `keyring set {keyring_service} {keyring_account}` to store it."
        )

    val = _from_keyring(keyring_service, keyring_account)
    if val:
        return val

    try:
        return _from_ssm(key)
    except Exception as e:
        raise RuntimeError(
            f"Could not retrieve secret '{key}' from env, keyring, or SSM "
            f"(path: /minimoi/production/{key.lower()}): {e}"
        ) from e

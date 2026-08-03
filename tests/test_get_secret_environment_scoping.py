"""
Regression tests for the German dev/prod voice-drift investigation's
follow-on finding: minimoi_portal/domain_auth.py's DATABASE_URL fallback
was a hardcoded, since-rotated password, and the failure to connect was
swallowed silently by app.py's login route (see
_working/CLAUDE_HANDOFF_GERMAN_HISTORY_RECOVERY_2026-08-02.md).

These tests cover the get_secret(..., environment_scoped=True) contract
that domain_auth._db_url() now relies on:
  - dev (MINIMOI_ROLE=standby) must never fall through to production's SSM
  - production (MINIMOI_ROLE=production) must never fall through to a dev
    Keychain entry
  - an unset or unrecognized MINIMOI_ROLE must refuse outright rather than
    defaulting to production, the way utils.role.is_production() does for
    its own (different) purpose
"""
import pytest

from core import get_secret as get_secret_module
from core.get_secret import get_secret


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MINIMOI_ROLE", raising=False)


def _forbid(name):
    def _fail(*args, **kwargs):
        raise AssertionError(f"{name} must not be consulted for this environment_scoped lookup")
    return _fail


def test_environment_scoped_dev_uses_keyring_not_ssm(monkeypatch):
    monkeypatch.setenv("MINIMOI_ROLE", "standby")
    monkeypatch.setattr(
        get_secret_module, "_from_keyring", lambda service, account: "postgresql://dev-value"
    )
    monkeypatch.setattr(get_secret_module, "_from_ssm", _forbid("SSM"))

    value = get_secret("DATABASE_URL", "minimoi-dev-db", "database_url", environment_scoped=True)
    assert value == "postgresql://dev-value"


def test_environment_scoped_dev_raises_when_keyring_empty_never_tries_ssm(monkeypatch):
    monkeypatch.setenv("MINIMOI_ROLE", "standby")
    monkeypatch.setattr(get_secret_module, "_from_keyring", lambda service, account: None)
    monkeypatch.setattr(get_secret_module, "_from_ssm", _forbid("SSM"))

    with pytest.raises(RuntimeError, match="Keychain"):
        get_secret("DATABASE_URL", "minimoi-dev-db", "database_url", environment_scoped=True)


def test_environment_scoped_production_uses_ssm_not_keyring(monkeypatch):
    monkeypatch.setenv("MINIMOI_ROLE", "production")
    monkeypatch.setattr(get_secret_module, "_from_ssm", lambda key: "postgresql://prod-value")
    monkeypatch.setattr(get_secret_module, "_from_keyring", _forbid("Keychain"))

    value = get_secret("DATABASE_URL", "minimoi-dev-db", "database_url", environment_scoped=True)
    assert value == "postgresql://prod-value"


def test_environment_scoped_production_raises_when_ssm_fails_never_tries_keyring(monkeypatch):
    monkeypatch.setenv("MINIMOI_ROLE", "production")

    def _fail_ssm(key):
        raise RuntimeError("parameter not found")

    monkeypatch.setattr(get_secret_module, "_from_ssm", _fail_ssm)
    monkeypatch.setattr(get_secret_module, "_from_keyring", _forbid("Keychain"))

    with pytest.raises(RuntimeError, match="SSM"):
        get_secret("DATABASE_URL", "minimoi-dev-db", "database_url", environment_scoped=True)


def test_environment_scoped_refuses_when_role_is_unset(monkeypatch):
    """The critical guard: utils.role.is_production() defaults to True
    when MINIMOI_ROLE is absent (correct for its own purpose — EC2 needs
    zero setup). Reusing that default here would let a fresh dev checkout
    with no MINIMOI_ROLE silently query production's SSM. It must refuse
    instead, before touching either store."""
    monkeypatch.setattr(get_secret_module, "_from_keyring", _forbid("Keychain"))
    monkeypatch.setattr(get_secret_module, "_from_ssm", _forbid("SSM"))

    with pytest.raises(RuntimeError, match="MINIMOI_ROLE"):
        get_secret("DATABASE_URL", "minimoi-dev-db", "database_url", environment_scoped=True)


def test_environment_scoped_refuses_when_role_is_unrecognized(monkeypatch):
    monkeypatch.setenv("MINIMOI_ROLE", "staging")
    monkeypatch.setattr(get_secret_module, "_from_keyring", _forbid("Keychain"))
    monkeypatch.setattr(get_secret_module, "_from_ssm", _forbid("SSM"))

    with pytest.raises(RuntimeError, match="MINIMOI_ROLE"):
        get_secret("DATABASE_URL", "minimoi-dev-db", "database_url", environment_scoped=True)


def test_environment_scoped_env_var_short_circuits_before_role_check(monkeypatch):
    """An explicit env var wins outright — no MINIMOI_ROLE, Keychain, or
    SSM lookup needed at all."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://from-env")
    monkeypatch.setattr(get_secret_module, "_from_keyring", _forbid("Keychain"))
    monkeypatch.setattr(get_secret_module, "_from_ssm", _forbid("SSM"))

    value = get_secret("DATABASE_URL", "minimoi-dev-db", "database_url", environment_scoped=True)
    assert value == "postgresql://from-env"


def test_non_scoped_lookup_ignores_role_entirely(monkeypatch):
    """Existing callers (OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, ...) don't
    pass environment_scoped and must keep the permissive try-everything
    chain, unaffected by MINIMOI_ROLE being unset."""
    monkeypatch.setattr(get_secret_module, "_from_keyring", lambda service, account: None)
    monkeypatch.setattr(get_secret_module, "_from_ssm", lambda key: "postgresql://ssm-fallback")

    value = get_secret("SOME_OTHER_SECRET", "svc", "account")
    assert value == "postgresql://ssm-fallback"

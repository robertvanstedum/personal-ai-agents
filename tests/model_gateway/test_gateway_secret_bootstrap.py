"""Tests for safe, repeatable local gateway-key provisioning."""

import os

from scripts.credentials.ensure_model_gateway_key import (
    VARIABLE_NAME,
    ensure_env_secret,
    ensure_gateway_key,
)


def test_bootstrap_creates_owner_only_key_without_rewriting_it(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=value\n")

    assert ensure_gateway_key(env_path) == "created"
    first = env_path.read_text()
    assert "EXISTING=value\n" in first
    key_line = next(line for line in first.splitlines() if line.startswith(VARIABLE_NAME))
    assert key_line.startswith(f"{VARIABLE_NAME}=sk-")
    assert len(key_line.partition("=")[2]) >= 48
    assert oct(os.stat(env_path).st_mode & 0o777) == "0o600"

    assert ensure_gateway_key(env_path) == "existing"
    assert env_path.read_text() == first


def test_bootstrap_replaces_an_empty_declaration(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(f"{VARIABLE_NAME}=\n")

    assert ensure_gateway_key(env_path) == "created"
    assert env_path.read_text().count(f"{VARIABLE_NAME}=") == 1
    assert env_path.read_text().splitlines()[0].startswith(f"{VARIABLE_NAME}=sk-")


def test_generic_bootstrap_can_preserve_an_existing_runtime_value(tmp_path):
    env_path = tmp_path / ".env"

    assert ensure_env_secret(
        env_path,
        variable_name="COS_AGENT_A_GATEWAY_TOKEN",
        value="existing-runtime-token",
    ) == "created"
    assert "COS_AGENT_A_GATEWAY_TOKEN=existing-runtime-token" in env_path.read_text()
    assert ensure_env_secret(
        env_path,
        variable_name="COS_AGENT_A_GATEWAY_TOKEN",
        value="replacement-must-not-win",
    ) == "existing"
    assert "replacement-must-not-win" not in env_path.read_text()

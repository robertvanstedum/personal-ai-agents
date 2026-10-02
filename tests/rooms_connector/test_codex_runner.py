"""Rooms R2 v0.4 (docs/specs/minimoi-connected-work/ROOMS_R2.md §3.6): the Codex
runner. No model and no network: the Codex CLI is tests/rooms_connector/fake_codex.py
behind a tiny wrapper per behaviour (the runner strips the environment, so the
mode is baked into each wrapper). The offline boundary probe is exercised against
the real binary separately (EVIDENCE.md); here it is stubbed except where its
fail-closed behaviour is the subject.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import pytest

from services.rooms_connector import codex_runner as XR
from services.rooms_connector.codex_runner import CodexRunner

FAKE = Path(__file__).with_name("fake_codex.py")
MESSAGES = [{"role": "system", "content": "etiquette"}, {"role": "user", "content": "[transcript]"},
            {"role": "user", "content": "trigger"}]
PROOF = {"id": "proof-turn", "room": "r", "response_key": "k", "brief": {"kind": "proof"}}


def cli(tmp_path, mode="ok", log=None):
    path = tmp_path / f"codex-{mode}"
    lines = ["#!/bin/sh", f"FAKE_CODEX_MODE={mode}"]
    if log:
        lines.append(f"FAKE_CODEX_LOG={log}")
    lines.append(f"export FAKE_CODEX_MODE{' FAKE_CODEX_LOG' if log else ''}")
    lines.append(f'exec {sys.executable} {FAKE} "$@"')
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return str(path)


def runner(tmp_path, mode="ok", log=None, probe=True, max_turn_s=20):
    home = tmp_path / "codex-home"
    home.mkdir(mode=0o700, exist_ok=True)
    r = CodexRunner(cli=cli(tmp_path, mode, log), home=str(home), state_dir=str(tmp_path / "state"),
                    turns_root=str(tmp_path / "turns"), max_turn_s=max_turn_s)
    r.boundary_probe = lambda timeout=60: probe
    return r


def test_answer_is_the_last_agent_message_with_usage(tmp_path):
    log = tmp_path / "argv.log"
    out = runner(tmp_path, log=log).stream(MESSAGES, "u", "a" * 32, turn=PROOF)
    assert out == {"outcome": "done", "text": "A short useful point from Codex.",
                   "usage": {"prompt_tokens": 120, "completion_tokens": 14}, "detail": "done"}
    call = [json.loads(line) for line in log.read_text().splitlines() if "app-server" in json.loads(line)["argv"]][0]
    argv = call["argv"]
    assert argv[:3] == ["app-server", "--listen", "stdio://"]
    for name in XR.DISABLED:
        assert name in argv
    for setting in ('web_search="disabled"', "skills.bundled.enabled=false", "skills.include_instructions=false",
                    'approval_policy="never"', 'sandbox_mode="read-only"', 'forced_login_method="chatgpt"'):
        assert setting in argv
    assert not any("model_provider" in a for a in argv)                     # never the probe's provider
    assert set(call["env"]) <= {"HOME", "CODEX_HOME", "PATH", "LANG", "FAKE_CODEX_MODE", "FAKE_CODEX_LOG",
                                "PWD", "SHLVL", "_", "__CF_USER_TEXT_ENCODING", "OLDPWD", "LC_CTYPE"}
    assert not any(k.startswith("OPENAI") for k in call["env"])


@pytest.mark.parametrize("kind", ["commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch",
                                  "imageView", "imageGeneration", "collabAgentToolCall", "subAgentActivity"])
def test_any_non_message_item_fails_closed(tmp_path, kind):
    started = time.time()
    out = runner(tmp_path, f"item_{kind}").stream(MESSAGES, "u", "b" * 32, turn=PROOF)
    assert out["outcome"] == "error" and out["reason"] == "runner_boundary" and out["text"] == ""
    assert time.time() - started < 10                                       # interrupted, not waited out


def test_a_server_request_is_declined_and_fails_closed(tmp_path):
    out = runner(tmp_path, "server_request").stream(MESSAGES, "u", "c" * 32, turn=PROOF)
    assert out["outcome"] == "error" and out["reason"] == "runner_boundary"


def test_unauthorized_before_any_work_is_signed_out(tmp_path):
    out = runner(tmp_path, "unauthorized").stream(MESSAGES, "u", "d" * 32, turn=PROOF)
    assert out["outcome"] == "refused" and out["reason"] == "signed_out" and out["usage"] is None


def test_unauthorized_after_output_and_usage_stays_uncertain_with_evidence(tmp_path):
    out = runner(tmp_path, "late_unauth").stream(MESSAGES, "u", "e" * 32, turn=PROOF)
    assert out["outcome"] == "error" and out["reason"] == "signed_out_after_start" and out["text"] == ""
    assert out["usage"] == {"prompt_tokens": 100, "completion_tokens": 12}


def test_usage_limit_is_named(tmp_path):
    out = runner(tmp_path, "usage_limit").stream(MESSAGES, "u", "f" * 32, turn=PROOF)
    assert out["outcome"] == "refused" and out["reason"] == "usage_limit"


def test_empty_answer_and_malformed_stream(tmp_path):
    assert runner(tmp_path, "empty").stream(MESSAGES, "u", "g" * 32, turn=PROOF)["detail"] == "empty"
    out = runner(tmp_path, "malformed").stream(MESSAGES, "u", "h" * 32, turn=PROOF)
    assert out["outcome"] == "error" and out["detail"] == "malformed"


def test_timeout_kills_and_is_uncertain(tmp_path):
    out = runner(tmp_path, "slow", max_turn_s=2).stream(MESSAGES, "u", "i" * 32, turn=PROOF)
    assert out["outcome"] == "error" and out["detail"] == "timeout"


def test_stop_during_a_turn(tmp_path):
    r = runner(tmp_path, "slow")
    import threading
    result = {}
    t = threading.Thread(target=lambda: result.update(r.stream(MESSAGES, "u", "j" * 32, turn=PROOF)))
    t.start()
    time.sleep(1.5)
    r.stop("j" * 32)
    t.join(15)
    assert result["outcome"] == "stopped"


@pytest.mark.parametrize("mode,reason", [("api_key", "signed_out"), ("signed_out", "signed_out")])
def test_only_a_chatgpt_sign_in_is_ready(tmp_path, mode, reason):
    r = runner(tmp_path, mode)
    assert r.check(allow_unproven=True) == (False, reason)
    out = r.stream(MESSAGES, "u", "k" * 32, turn=PROOF)
    assert out["outcome"] == "refused" and out["reason"] == reason          # no process for the turn


@pytest.mark.parametrize("entry", ["config.toml", "AGENTS.md", "rules", "skills", "plugins", "hooks", "unknown.txt"])
def test_codex_home_allowlist_fails_closed(tmp_path, entry):
    r = runner(tmp_path)
    target = Path(r.home) / entry
    target.mkdir() if entry in ("rules", "skills", "plugins", "hooks") else target.write_text("x")
    assert r.check(allow_unproven=True) == (False, "startup_inputs")


def test_codex_home_allows_its_own_runtime_files(tmp_path):
    r = runner(tmp_path)
    for name in ("auth.json", "installation_id", "state_5.sqlite", "goals_1.sqlite-wal", "logs_2.sqlite"):
        (Path(r.home) / name).write_text("x")
    (Path(r.home) / "tmp").mkdir()
    assert r.check(allow_unproven=True) == (True, None)


def test_a_failed_boundary_probe_makes_codex_unready(tmp_path):
    assert runner(tmp_path, probe=False).check(allow_unproven=True) == (False, "runner_boundary")


def test_the_real_probe_fails_closed_when_nothing_is_captured(tmp_path):
    r = runner(tmp_path)
    del r.boundary_probe                                                     # the fake never calls the endpoint
    assert r.boundary_probe(timeout=5) is False


def test_proof_binding_and_change(tmp_path):
    r = runner(tmp_path)
    assert r.check() == (False, "not_proven_with_this_runner")
    out = r.stream(MESSAGES, "u", "l" * 32, turn=PROOF)
    assert out["outcome"] == "done"
    assert r.record_proof(PROOF) is True and r.proof_path().name == "proof-codex.json"
    assert r.check() == (True, None)
    record = json.loads(r.proof_path().read_text())
    assert record["version"] == "0.145.0" and record["proof_turn"] == "proof-turn"
    record["profile_sha256"] = "changed"
    r.proof_path().write_text(json.dumps(record))
    assert r.check() == (False, "runner_changed_since_proof")


def test_profile_hash_covers_flags_and_catalog(tmp_path):
    r = runner(tmp_path)
    base = r.fingerprint()["profile_sha256"]
    other = tmp_path / "catalog.json"
    other.write_text(XR.CATALOG.read_text().replace('"text"', '"text", "image"'))
    r.catalog = other
    assert r.fingerprint()["profile_sha256"] != base

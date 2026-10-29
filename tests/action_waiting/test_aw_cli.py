"""C6, the agents' CLI scripts/agents/waiting.py against a fake MiniMoi on 127.0.0.1 (spec v0.2 §3.8): T20.

The fake server is a local HTTP server on the loopback address; the fallback Telegram sender and the Keychain are
fakes. No real network, Keychain or Telegram call is made."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("waiting_cli", ROOT / "scripts" / "agents" / "waiting.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

TOKEN = "t" * 40 + "-agent-cli-test"


class FakeMiniMoi:
    def __init__(self):
        self.replies = []                                     # (status, body) per call; the last one repeats
        self.calls = []
        handler = self._handler()
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.calls.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
                status, reply = fake.replies[min(len(fake.calls), len(fake.replies)) - 1]
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        return H

    def close(self):
        self.server.shutdown()


@pytest.fixture
def server():
    s = FakeMiniMoi()
    yield s
    s.close()


@pytest.fixture
def deps(tmp_path, server):
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    for agent in ("claude-code", "codex"):
        f = token_dir / f"{agent}-dev.token"
        f.write_text(TOKEN + "\n")
        os.chmod(f, 0o600)
    sent, sleeps, out = [], [], io.StringIO()
    d = cli.Deps(environ={"MINIMOI_NOTICES_DEV_URL": server.url}, token_dir=token_dir,
                 telegram=lambda text: sent.append(text) or "fallback sent", sleep=sleeps.append,
                 run=lambda *a, **k: pytest.fail("the Keychain must not be read when a token file exists"), out=out)
    d.sent, d.sleeps, d.output = sent, sleeps, out
    return d


def run(deps, *args):
    code = cli.main(list(args), deps)
    return code, deps.output.getvalue()


WORDS = "Need your yes on merging PR #12 before I continue"


def test_recorded_and_deliverable_exits_0_without_fallback(server, deps):
    server.replies = [(200, {"code": "W-3", "state": "open", "new": True, "delivery": "pending"})]
    code, out = run(deps, "raise", "--env", "dev", "--agent", "codex", "--branch", "codex/x", WORDS)
    assert code == 0 and out.strip() == "W-3 new · delivery pending" and deps.sent == []
    call = server.calls[0]
    assert call["path"] == "/internal/notices/v1/raise" and call["auth"] == f"Bearer {TOKEN}"
    assert call["body"] == {"key": cli.default_key("codex", None, WORDS), "words": WORDS, "branch": "codex/x"}
    assert TOKEN not in out


@pytest.mark.parametrize("delivery", ["record", "not_configured", "failing", "stalled"])
def test_recorded_but_not_deliverable_sends_the_fallback(server, deps, delivery):
    server.replies = [(200, {"code": "W-3", "state": "open", "new": False, "delivery": delivery})]
    code, out = run(deps, "raise", "--env", "dev", "--agent", "claude-code", WORDS)
    assert code == 0 and "W-3 already waiting" in out
    assert len(deps.sent) == 1
    text = deps.sent[0]
    assert text.startswith(f"[dev] MiniMoi · recorded as W-3, not sent by MiniMoi ({delivery})")
    assert "Claude Code is blocked on you" in text and WORDS not in text and "?waiting=W-3" in text


def test_no_fallback_flag(server, deps):
    server.replies = [(200, {"code": "W-3", "state": "open", "new": True, "delivery": "record"})]
    assert run(deps, "raise", "--env", "dev", "--agent", "codex", "--no-fallback", WORDS)[0] == 0
    assert deps.sent == []


@pytest.mark.parametrize("status,error", [(401, "unauthorized"), (400, "request_invalid"), (409, "key_conflict"),
                                          (429, "too_many_open")])
def test_refusals_exit_2_without_fallback(server, deps, status, error):
    server.replies = [(status, {"error": error})]
    code, out = run(deps, "raise", "--env", "dev", "--agent", "codex", WORDS)
    assert code == 2 and error in out and deps.sent == [] and len(server.calls) == 1


def test_unreachable_exits_3_after_three_tries_and_sends_not_recorded(deps):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    deps.environ = {"MINIMOI_NOTICES_DEV_URL": f"http://127.0.0.1:{closed}"}
    planted = WORDS + " token ghp_ABCDEFGHIJKLMNOPQRSTUVWX"
    code, out = run(deps, "raise", "--env", "dev", "--agent", "codex", "--branch", "codex/x", planted)
    assert code == 3 and "NOT RECORDED" in out and deps.sleeps == [cli.WAIT_S, cli.WAIT_S]
    [text] = deps.sent
    assert text.startswith("[dev] MiniMoi · NOT RECORDED in MiniMoi\nCodex is blocked on you · branch codex/x")
    assert "ghp_" not in text and "[credential removed]" in text
    deps.sent.clear()
    assert run(deps, "raise", "--env", "dev", "--agent", "codex", "--no-fallback", WORDS)[0] == 3
    assert deps.sent == []


def test_server_errors_are_retried_then_unreachable(server, deps):
    server.replies = [(503, {"error": "unavailable"})]
    assert run(deps, "raise", "--env", "dev", "--agent", "codex", WORDS)[0] == 3
    assert len(server.calls) == 3


def test_resolve(server, deps):
    server.replies = [(200, {"code": "W-3", "state": "resolved"})]
    code, out = run(deps, "resolve", "--env", "dev", "--agent", "codex", "--key", "k-000001")
    assert code == 0 and out.strip() == "W-3 resolved" and server.calls[0]["body"] == {"key": "k-000001"}


def test_a_world_readable_token_file_is_refused(server, deps):
    os.chmod(deps.token_dir / "codex-dev.token", 0o644)
    with pytest.raises(SystemExit, match="readable by others"):
        run(deps, "raise", "--env", "dev", "--agent", "codex", WORDS)
    assert server.calls == []


def test_install_prints_only_the_hashes_line(tmp_path):
    made, out = {}, io.StringIO()

    def fake_security(cmd, **kw):
        if cmd[1] == "find-generic-password":
            account = cmd[cmd.index("-a") + 1]
            return SimpleNamespace(returncode=0 if account in made else 44, stdout=made.get(account, ""))
        made[cmd[cmd.index("-a") + 1]] = cmd[cmd.index("-w") + 1]
        return SimpleNamespace(returncode=0, stdout="")
    deps = cli.Deps(run=fake_security, out=out, token_dir=tmp_path, environ={})
    assert cli.main(["install", "--env", "prod"], deps) == 0
    line = out.getvalue().strip()
    expected = ",".join(f"{a}:{hashlib.sha256(made[f'{a}-prod'].encode()).hexdigest()}" for a in ("claude-code", "codex"))
    assert line == f"MINIMOI_ACTION_NOTICES_AGENT_TOKENS={expected}"
    assert all(token not in line for token in made.values())
    out.truncate(0)
    out.seek(0)
    assert cli.main(["install", "--env", "prod"], deps) == 0     # idempotent: the same tokens, the same line
    assert out.getvalue().strip() == line


def test_prod_url_is_fixed(deps):
    assert cli.base_url("prod", deps) == "https://minimoi.ai"
    deps.environ = {"MINIMOI_NOTICES_DEV_URL": "ftp://example.test"}
    with pytest.raises(SystemExit):
        cli.base_url("dev", deps)

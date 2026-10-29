"""The census's root rows from MC's two front-door connections and from the Codex desktop queue (F1) (host runtime spec
v0.5 R6.6-c, R6.7-g, LCH-5; Robert, 28 Oct: "cover F1"). The children's environments stay exactly P0's (the existing
launch-contract tests run unchanged beside these). Fake CLIs and fake queue runs only: no real Claude Code or Codex
starts, no Keychain read, and each test's HOME is its own folder."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_requests import codex_desktop_queue as Q
from core.agent_requests.connection import ConnectionManager
from core.agent_requests.direct_contact import Outbox
from core.mc_delegation_runner import census as C
from test_codex_desktop_queue import IDENTITY, SOURCE, TARGET, binding
from test_codex_provider import THREAD
from test_connection import SID
from test_connection_launch_contract import P0_CLAUDE, P0_CODEX, PLANTED, VALUE, _claude, _codex, launchd  # noqa: F401


def rows(folder):
    return {r["agent_id"]: r for r in C.CensusFile(folder).rows(host_id="mac-test-1", environment="dev")}


def no_value(folder):
    return all(VALUE.encode() not in p.read_bytes() for p in Path(folder).rglob("*") if p.is_file())


def lch5(row):
    """LCH-5: a root row has start_path, route and launch, and a transcript_ref (or not_kept with a reason)."""
    t = row["transcript_ref"]
    return (row["parent_id"] is None and row["start_path"] and row["route"] and row["launch"]
            and t is not None and (t["kept"] or t["reason"]))


def test_the_claude_connection_writes_its_root_row_at_start_and_its_state_as_passes_see_it(tmp_path, launchd):
    root, seen, census = str(tmp_path / "ledger"), {}, str(tmp_path / "census")
    p = _claude(tmp_path, root, seen)
    m = ConnectionManager(root, "claude-code", p, session_id=SID, census_dir=census)
    p.start()
    assert set(seen["env"]) == P0_CLAUDE and VALUE not in seen["env"].values()      # the child is unchanged (P0 §5)
    row = rows(census)[SID]
    assert lch5(row) and (row["start_path"], row["route"], row["vendor"], row["role"], row["item_id"]) == \
        ("connection", "subscription", "anthropic", "session", None)
    assert row["launch"]["profile"] == "unattended" and row["launch"]["route"] == "claude-code.subscription"
    assert set(row["launch"]["env_names"]) == P0_CLAUDE and set(PLANTED) <= set(row["launch"]["removed"])
    assert row["launch"]["binary"]["sha256"] == "sha256:" + __import__("agent_fakes").pin_of(p.claude_bin)
    assert row["transcript_ref"] == {"kept": True, "session_id": SID, "path": f"~/.claude/projects/*/{SID}.jsonl"}
    assert (row["children"], row["state"], row["source"]) == ("not_exposed", "running", "connection.start")
    m.step()
    assert rows(census)[SID]["state"] == "idle"                                       # the probe: idle
    p.close()
    m.step()
    assert (rows(census)[SID]["state"], rows(census)[SID]["reason"]) == ("idle", "offline")
    assert len(rows(census)) == 1 and no_value(census)
    m.close()


def test_the_codex_connection_writes_its_root_row_when_its_manager_takes_the_first_start(tmp_path, launchd):
    root, seen, census = str(tmp_path / "ledger"), {}, str(tmp_path / "census")
    p = _codex(tmp_path, root, seen)
    with pytest.raises(TimeoutError):                      # the first --create starts before its manager exists
        p.start()
    assert set(seen["env"]) == P0_CODEX
    m = ConnectionManager(root, "codex", p, session_id=THREAD, census_dir=census)
    row = rows(census)[THREAD]
    assert lch5(row) and (row["vendor"], row["route"], row["launch"]["route"]) == ("openai", "subscription",
                                                                                   "codex.subscription")
    assert row["transcript_ref"]["path"] == "~/.codex/sessions/**/rollout-*.jsonl" and no_value(census)
    m.close()


def test_a_connection_whose_root_row_cannot_be_written_never_starts(tmp_path, launchd):
    root, seen = str(tmp_path / "ledger"), {}
    (tmp_path / "a-file").write_text("")
    p = _claude(tmp_path, root, seen)
    m = ConnectionManager(root, "claude-code", p, session_id=SID, census_dir=str(tmp_path / "a-file" / "census"))
    with pytest.raises(OSError):
        p.start()
    assert seen == {}                                                                 # nothing was spawned
    m.close()
    with pytest.raises(Exception):
        ConnectionManager(root, "claude-code", p, session_id=SID, census_dir="relative/census")


def test_without_a_census_folder_a_connection_writes_none(tmp_path, launchd):
    root, seen = str(tmp_path / "ledger"), {}
    p = _claude(tmp_path, root, seen)
    m = ConnectionManager(root, "claude-code", p, session_id=SID)
    p.start()
    assert m.census is None and not (tmp_path / "census").exists()
    p.close()
    m.close()


# ── F1: the MC agent bridge's Codex desktop queue ──────────────────────────────────────────────────────────────────
def queue(tmp_path, monkeypatch, census_dir, native_home=None):
    tmp_path.chmod(0o700)
    calls = []
    monkeypatch.setattr(Q, "_binary", lambda path: IDENTITY)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)
    transport = Q.CodexDesktopQueue(binding(), run=run, native_home=native_home, census_dir=census_dir)
    box = Outbox(tmp_path, {TARGET.alias: TARGET}, transport)
    return box, calls


def test_f1_each_queued_turn_writes_the_threads_root_row_never_claimed_as_governed(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", VALUE)
    census = str(tmp_path / "census")
    box, calls = queue(tmp_path, monkeypatch, census, native_home="/Users/someone/.codex")
    second = replace(SOURCE, note_id="note-2", authorization_ref="kept-note:note-2")
    for source, action in ((SOURCE, "Synthetic exact-session review request."), (second, "A second synthetic one.")):
        rid = box.enqueue(TARGET.alias, source, action)
        assert box.dispatch(rid)["state"] == "dispatched"
    assert len(calls) == 2 and set(calls[0][1]["env"]) == {"HOME", "CODEX_HOME", "PATH", "LANG"}
    (row,) = rows(census).values()                                                   # one agent, two turns
    assert row["agent_id"] == TARGET.session_id and lch5(row)
    assert (row["vendor"], row["start_path"], row["route"], row["state"], row["reason"]) == \
        ("openai", "connection", "subscription", "unknown", "turn_not_observed")
    assert row["launch"]["profile"] == "desktop_app" and row["launch"]["governed"] is False
    assert row["launch"]["env_names"] == ["CODEX_HOME", "HOME", "LANG", "PATH"]
    assert "TELEGRAM_BOT_TOKEN" in row["launch"]["removed"]                          # a name, never its value
    assert row["launch"]["binary"] == {"path": Q.BUNDLED_BINARY, "sha256": None, "pinned_by": "metadata_identity"}
    assert row["transcript_ref"] == {"kept": True, "session_id": TARGET.session_id,
                                     "path": "/Users/someone/.codex/sessions"}
    raw = b"".join(p.read_bytes() for p in Path(census).glob("*.jsonl"))
    assert b"Synthetic exact-session" not in raw and b"request_not_new_owner_approval" not in raw and no_value(census)


def test_f1_a_row_that_cannot_be_written_queues_nothing(tmp_path, monkeypatch):
    (tmp_path / "a-file").write_text("")
    box, calls = queue(tmp_path, monkeypatch, str(tmp_path / "a-file" / "census"))
    rid = box.enqueue(TARGET.alias, SOURCE, "Synthetic exact-session review request.")
    assert box.dispatch(rid)["state"] == "unknown" and calls == []                  # never sent, never retried


def test_f1_without_a_census_folder_the_queue_is_todays(tmp_path, monkeypatch):
    box, calls = queue(tmp_path, monkeypatch, None)
    rid = box.enqueue(TARGET.alias, SOURCE, "Synthetic exact-session review request.")
    assert box.dispatch(rid)["state"] == "dispatched" and len(calls) == 1 and "env" not in calls[0][1]
    with pytest.raises(Exception):
        Q.CodexDesktopQueue(binding(), census_dir="relative/census")
    assert os.path.exists(tmp_path) and not (tmp_path / "census").exists()

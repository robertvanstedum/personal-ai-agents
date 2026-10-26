import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "judgment"))
import judgment_store as J  # noqa: E402

GOOD = """Understood, I'll leave the side task for later.

```capture
when: 2026-10-25, c-test
task: finish the synthetic build
did: pulled an unrelated side task into the finish line
owner-said: "That side task belongs to another builder; leave it for later."
should-have: defer unrelated work and clarify ownership
tag: scope
source: robert
```

Captured 1 correction pairs"""


def make_db(path, messages):
    con = sqlite3.connect(path)
    con.execute("create table transcript_events (session_id text, seq integer, event_json text, created_at integer)")
    con.execute("create table session_conversations (session_id text, conversation_id text, role text, route_context_json text, first_seen_at integer, last_seen_at integer)")
    con.execute("insert into session_conversations values ('s1','c-test','primary',null,0,0)")
    for i, (role, text) in enumerate(messages, 1):
        ev = {"type": "message", "id": f"e{i}", "message": {"role": role, "content": [{"type": "text", "text": text}] if role == "assistant" else text}}
        con.execute("insert into transcript_events values ('s1', ?, ?, 0)", (i, json.dumps(ev)))
    con.commit()
    con.close()


@pytest.fixture
def env(tmp_path):
    return tmp_path / "s.sqlite", tmp_path / "store"


def run(env, *args):
    db, store = env
    return J.main(["--db", str(db), "--store", str(store), *args])


def test_collect_dry_run_writes_nothing(env, capsys):
    make_db(env[0], [("user", "x"), ("assistant", GOOD)])
    run(env, "collect")
    out = json.loads(capsys.readouterr().out)
    assert out["pairs_collected"] == 1 and out["applied"] is False
    assert not (env[1] / "correction-pairs.md").exists()


def test_collect_appends_verbatim_with_provenance_and_is_idempotent(env, capsys):
    make_db(env[0], [("user", "x"), ("assistant", GOOD)])
    run(env, "collect", "--apply")
    text = (env[1] / "correction-pairs.md").read_text()
    assert 'owner-said: "That side task belongs to another builder; leave it for later."' in text
    assert "conversation=c-test session=s1 seq=2 event=e2" in text
    capsys.readouterr()
    run(env, "collect", "--apply")
    assert json.loads(capsys.readouterr().out)["pairs_collected"] == 0
    assert (env[1] / "correction-pairs.md").read_text() == text


def test_receipt_mismatch_and_malformed_block_are_logged_not_dropped(env, capsys):
    bad = "```capture\nwhen: now\ntask: t\n```\nCaptured 2 correction pairs"
    make_db(env[0], [("assistant", bad)])
    run(env, "collect", "--apply")
    out = json.loads(capsys.readouterr().out)
    assert out["pairs_collected"] == 0 and out["failures"] == 2
    log = (env[1] / "capture-failures.md").read_text()
    assert "malformed block: missing did" in log and "receipt says 2 but 0" in log
    assert "when: now" in log                                   # the raw text is preserved


def test_block_without_receipt_is_a_failure(env, capsys):
    make_db(env[0], [("assistant", GOOD.replace("Captured 1 correction pairs", ""))])
    run(env, "collect")
    out = json.loads(capsys.readouterr().out)
    assert out["pairs_collected"] == 1 and any("no receipt" in f for f in out["detail"]["failures"])


def test_user_messages_never_collected(env, capsys):
    make_db(env[0], [("user", GOOD)])
    run(env, "collect")
    assert json.loads(capsys.readouterr().out)["pairs_collected"] == 0


def test_defect_receipt_goes_to_defects(env, capsys):
    make_db(env[0], [("assistant", "The tool refused twice. +1 defect noted: permission loop on board read")])
    run(env, "collect", "--apply")
    assert "permission loop" in (env[1] / "defects.md").read_text()


def test_db_is_opened_read_only(env):
    make_db(env[0], [("assistant", GOOD)])
    con = J.open_ro(env[0])
    with pytest.raises(sqlite3.OperationalError):
        con.execute("insert into transcript_events values ('x',1,'{}',0)")


def test_adopt_requires_robert_and_records_hash_then_receipt_confirms(env, tmp_path, capsys):
    card = tmp_path / "card.md"
    card.write_text("Before re-reporting a blocker, re-diagnose.")
    with pytest.raises(SystemExit, match="Robert's explicit act"):
        run(env, "adopt", "--card", str(card), "--name", "stall-handling", "--approved-by", "codex", "--approval", "ok", "--apply")
    run(env, "adopt", "--card", str(card), "--name", "stall-handling", "--approved-by", "robert", "--approval", "adopt it", "--apply")
    r = json.loads(capsys.readouterr().out)
    h = r["sha256"][:12]
    assert r["receipt_expected"] == f"Cards loaded: stall-handling@{h}"
    run(env, "status")
    assert json.loads(capsys.readouterr().out)["cards"]["stall-handling"]["loading_confirmed"] is False
    make_db(env[0], [("assistant", f"Cards loaded: stall-handling@{h}\nHello.")])
    run(env, "collect", "--apply")
    capsys.readouterr()
    run(env, "status")
    assert json.loads(capsys.readouterr().out)["cards"]["stall-handling"]["loading_confirmed"] is True


def test_stale_card_hash_is_not_confirmation(env, tmp_path, capsys):
    card = tmp_path / "card.md"
    card.write_text("v1")
    run(env, "adopt", "--card", str(card), "--name", "card-c", "--approved-by", "robert", "--approval", "yes", "--apply")
    old = json.loads(capsys.readouterr().out)["sha256"][:12]
    card.write_text("v2")
    run(env, "adopt", "--card", str(card), "--name", "card-c", "--approved-by", "robert", "--approval", "yes v2", "--apply")
    capsys.readouterr()
    make_db(env[0], [("assistant", f"Cards loaded: card-c@{old}")])
    run(env, "collect", "--apply")
    assert "NOT the current adopted version" in (env[1] / "adoption-receipts.md").read_text()


def test_freeze_snapshots_conversation_with_hash(env, capsys):
    make_db(env[0], [("user", "q"), ("assistant", "a")])
    run(env, "freeze", "--conversation", "c-test", "--label", "first", "--apply")
    r = json.loads(capsys.readouterr().out)
    d = env[1] / "scenarios" / r["id"]
    assert r["events"] == 2 and J.sha((d / "events.jsonl").read_bytes()) == r["events_sha256"]
    with pytest.raises(SystemExit, match="already frozen"):
        run(env, "freeze", "--conversation", "c-test", "--apply")


def test_excluded_test_session_is_skipped_and_remembered(env, capsys):
    make_db(env[0], [("assistant", GOOD)])
    run(env, "collect", "--apply", "--exclude-session", "s1")
    assert json.loads(capsys.readouterr().out)["pairs_collected"] == 0
    assert "s1" in json.loads((env[1] / ".collector-state.json").read_text())["excluded_sessions"]

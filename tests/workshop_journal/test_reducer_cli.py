"""What is open, what is closed and who may close it (acceptance rows D01, D02), and the command line (v0.6 section 13)."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import REPO, WORKSHOP, envelope, new_id, progress
from core.workshop_journal import reducer
from core.workshop_journal.journal import Journal

CLI = str(REPO / "scripts/workshop/workshop.py")


def need(text="Which backup destination should we use?", *, item="spec:backup", extra=None, **over):
    payload = {"reason_code": "owner_choice", "requested_action": "Pick a destination.", "incident_id": new_id(), **(extra or {})}
    return envelope(kind="needs_you", recipients=["robert"], stage="review", item=item, text=text, payload=payload, **over)


def decision(resolves, *, actor="robert", kind="approved-direct", item="spec:backup", **over):
    return envelope(actor=actor, kind="decision", recipients=[], stage="review", item=item, text="Decided.",
                    payload={"record_event_kind": kind, "resolves": resolves, "reason": "chosen"}, **over)


def owner_only(ev):
    """What the real resolver will check in Unit 4 (an owner-controlled origin); here the actor stands in for it."""
    return ev["actor"] == "robert"


def state_of(j):
    return j.state()[0]


# ── D01: an agent never closes a question meant for the owner ─────────────────────────────────────────────────────
@pytest.mark.parametrize("actor", ["codex", "claude-code", "claude-chat", "grok-cli", "grok-chat", "mc", "journeyman", "host"])
def test_D01_a_decision_written_by_an_agent_leaves_the_owner_question_open(root, actor):
    j = Journal(root, WORKSHOP, resolver=owner_only)
    n = j.append(need())
    assert n.committed
    assert j.append(decision([n.event_id], actor=actor)).committed
    assert len(state_of(j)["needs_you"]) == 1
    assert j.append(decision([n.event_id], actor="robert")).committed        # the owner's own decision does close it
    assert state_of(j)["needs_you"] == []


def test_D01_a_decision_with_no_resolver_never_closes_anything(root):
    j = Journal(root, WORKSHOP)
    n = j.append(need())
    j.append(decision([n.event_id]))
    assert len(state_of(j)["needs_you"]) == 1


def test_D01_only_an_exact_resolves_id_plus_an_approval_kind_plus_the_owner_resolver_closes_it(root):
    seen = []
    j = Journal(root, WORKSHOP, resolver=lambda ev: seen.append(ev["event_id"]) or True)
    first, second = j.append(need("first question", item="spec:one")), j.append(need("second question", item="spec:two"))
    other = new_id()
    j.append(decision([other], item="spec:one"))                           # resolves an ID that is not a need: closes nothing
    assert len(state_of(j)["needs_you"]) == 2
    j.append(decision([first.event_id], kind="approved-direct"))
    texts = [x["text"] for x in state_of(j)["needs_you"]]
    assert texts == ["second question"]
    assert seen                                                           # the resolver was actually consulted


def test_D01_a_decision_of_another_kind_does_not_count_as_approval(root):
    j = Journal(root, WORKSHOP, resolver=lambda ev: True)
    n = j.append(need())
    j.append(decision([n.event_id], kind="proposed"))
    assert len(state_of(j)["needs_you"]) == 1


def test_D01_a_resolver_that_raises_or_says_maybe_closes_nothing(root):
    n_id = None
    for resolver in (lambda ev: (_ for _ in ()).throw(RuntimeError("boom")), lambda ev: "yes", lambda ev: None, lambda ev: 1):
        r = root + "-" + str(id(resolver))
        import os
        os.mkdir(r, 0o700)
        j = Journal(r, WORKSHOP, resolver=resolver)
        n = j.append(need())
        j.append(decision([n.event_id]))
        assert len(state_of(j)["needs_you"]) == 1


def test_D01_every_open_question_stays_listed_not_just_the_latest_per_item(root):
    j = Journal(root, WORKSHOP)
    j.append(need("first"))
    j.append(need("second"))
    j.append(progress("some later work on another item"))
    assert [x["text"] for x in state_of(j)["needs_you"]] == ["first", "second"]


def test_D01_an_incident_id_closes_every_question_that_shares_it(root):
    inc = new_id()
    j = Journal(root, WORKSHOP, resolver=lambda ev: True)
    a = j.append(need("again", extra={"incident_id": inc}))
    b = j.append(need("still", extra={"incident_id": inc}, item="spec:other"))
    j.append(decision([inc]))
    assert state_of(j)["needs_you"] == [] and a.committed and b.committed


# ── D02: ordering follows the journal, not the clock ──────────────────────────────────────────────────────────────
def test_D02_the_latest_event_is_the_one_last_in_the_journal_even_if_its_clock_is_earlier(root):
    j = Journal(root, WORKSHOP)
    j.append(progress("later by the clock", at="2026-10-08T12:00:00Z"))
    j.append(progress("earlier by the clock", at="2026-10-08T09:00:00Z"))
    st = state_of(j)
    assert st["items"]["queue:146"]["text"] == "earlier by the clock" and st["last_event"]["text"] == "earlier by the clock"


def test_D02_an_item_whose_last_event_is_done_leaves_the_in_progress_list(root):
    j = Journal(root, WORKSHOP)
    j.append(progress("working"))
    assert len(state_of(j)["in_progress"]) == 1
    j.append(envelope(kind="done", recipients=[], stage="build", item="queue:146", text="done",
                      payload={"outcome": "reported_completed"}))
    assert state_of(j)["in_progress"] == []


def test_D02_reduce_is_a_pure_function_of_the_events(root):
    j = Journal(root, WORKSHOP)
    for i in range(5):
        j.append(progress(f"p{i}"))
    events = j.read().events
    a, b = reducer.reduce(WORKSHOP, events), reducer.reduce(WORKSHOP, list(events))
    a.pop("updated_at"), b.pop("updated_at")
    assert a == b and a["events"] == 5


# ── the command line ──────────────────────────────────────────────────────────────────────────────────────────────
def cli(home, *args, stdin=None):
    proc = subprocess.run([sys.executable, CLI, "--home", home, "--id", WORKSHOP, *args], input=stdin, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}, timeout=60)
    return proc


def write_env(tmp_path, body, name="e.json"):
    p = tmp_path / name
    p.write_text(json.dumps(body))
    return str(p)


def test_cli_append_prints_one_json_object_and_exits_zero(root, tmp_path):
    out = cli(root, "append", "--file", write_env(tmp_path, progress("from the command line")))
    assert out.returncode == 0 and out.stderr == ""
    doc = json.loads(out.stdout)
    assert doc["status"] == "committed" and doc["committed"] is True and doc["seq"] == 1
    assert out.stdout.count("\n") == 1


def test_cli_append_reads_stdin_and_a_retry_is_a_duplicate_with_exit_zero(root, tmp_path):
    body = progress("from stdin", event_id=new_id())
    first = cli(root, "append", "--file", "-", stdin=json.dumps(body))
    again = cli(root, "append", "--file", "-", stdin=json.dumps(body))
    assert (first.returncode, json.loads(first.stdout)["status"]) == (0, "committed")
    assert (again.returncode, json.loads(again.stdout)["status"]) == (0, "duplicate")


@pytest.mark.parametrize("body,reason", [
    ({"actor": "codex", "kind": "progress", "item": "queue:1", "text": "x", "payload": {"action": "x"}, "seq": 9}, "seq"),
    ({"actor": "nobody", "kind": "progress", "item": "queue:1", "text": "x", "payload": {"action": "x"}}, "actor"),
    ({"actor": "codex", "kind": "progress", "item": "queue:1", "text": "token sk-abcdefghijklmnopqrstuv", "payload": {"action": "x"}}, "text"),
])
def test_cli_invalid_input_exits_two_and_writes_nothing(root, tmp_path, body, reason):
    out = cli(root, "append", "--file", write_env(tmp_path, body))
    doc = json.loads(out.stdout)
    assert out.returncode == 2 and doc["status"] == "invalid_input" and reason in doc["reason"]
    assert not (tmp_path.parent / "x").exists()
    assert cli(root, "verify", "--json").returncode in (0, 8)


def test_cli_exit_codes_for_conflict_missing_and_corrupt(root, tmp_path):
    eid = new_id()
    assert cli(root, "append", "--file", write_env(tmp_path, progress("one", event_id=eid))).returncode == 0
    conflict = cli(root, "append", "--file", write_env(tmp_path, progress("different", event_id=eid), "c.json"))
    assert conflict.returncode == 3 and json.loads(conflict.stdout)["status"] == "id_conflict"
    assert cli(root, "get", "--event-id", eid).returncode == 0
    assert cli(root, "get", "--event-id", new_id()).returncode in (0, 8)
    path = f"{root}/{WORKSHOP}/events.jsonl"
    data = open(path, "rb").read()
    open(path, "wb").write(data + b"garbage line\n")
    bad = cli(root, "verify", "--json")
    assert bad.returncode == 5 and json.loads(bad.stdout)["ok"] is False
    held = cli(root, "append", "--file", write_env(tmp_path, progress("held"), "h.json"))
    assert held.returncode == 5 and json.loads(held.stdout)["status"] == "corrupt"


def test_cli_prepare_then_append_keeps_the_id_and_time(root, tmp_path):
    prepared = json.loads(cli(root, "prepare", "--file", write_env(tmp_path, progress("prepared"))).stdout)
    assert prepared["status"] == "prepared"
    resubmit = prepared["evidence"]["envelope"]
    done = json.loads(cli(root, "append", "--file", write_env(tmp_path, resubmit, "r.json")).stdout)
    assert done["event_id"] == prepared["event_id"] and done["status"] == "committed"
    j = Journal(root, WORKSHOP)
    assert j.get(done["event_id"]).evidence["event"]["at"] == resubmit["at"]


def test_cli_a_bad_workshop_id_is_refused_with_fixed_text(root, tmp_path):
    out = subprocess.run([sys.executable, CLI, "--home", root, "--id", "../escape", "verify", "--json"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)}, timeout=60)
    assert out.returncode == 2 and "../escape" not in out.stdout


def test_cli_an_oversized_envelope_is_refused_before_parsing(root, tmp_path):
    big = tmp_path / "big.json"
    big.write_text("{" + '"x":"' + "a" * 70000 + '"}')
    out = cli(root, "append", "--file", str(big))
    assert out.returncode == 2 and json.loads(out.stdout)["reason"] == "envelope_too_large"


def test_cli_repair_is_a_dry_run_unless_applied(root, tmp_path):
    assert cli(root, "append", "--file", write_env(tmp_path, progress("one"))).returncode == 0
    path = f"{root}/{WORKSHOP}/events.jsonl"
    open(path, "ab").write(b'{"v":2,"cut')
    size = len(open(path, "rb").read())
    dry = cli(root, "repair")
    assert dry.returncode == 0 and json.loads(dry.stdout)["status"] == "would_repair" and len(open(path, "rb").read()) == size
    done = cli(root, "repair", "--apply")
    assert json.loads(done.stdout)["status"] == "repaired" and cli(root, "verify", "--json").returncode == 0


# ── inbox and artifact commands ───────────────────────────────────────────────────────────────────────────────────
def test_cli_inbox_is_a_dry_run_until_applied_and_artifact_open_returns_the_exact_text(root, tmp_path):
    from test_inbox import HEADER, NAME
    assert cli(root, "append", "--file", write_env(tmp_path, progress("seed"))).returncode == 0
    folder = f"{root}/{WORKSHOP}/inbox"
    import os
    os.mkdir(folder, 0o700)
    open(f"{folder}/{NAME}", "w").write(HEADER)
    dry = json.loads(cli(root, "inbox", "--settle", "0").stdout)
    assert dry["apply"] is False and dry["counts"] == {"would_ingest": 1}
    assert Journal(root, WORKSHOP).read().events[-1]["kind"] == "progress" and len(Journal(root, WORKSHOP).read().events) == 1
    done = cli(root, "inbox", "--apply", "--settle", "0")
    out = json.loads(done.stdout)
    assert done.returncode == 0 and out["counts"] == {"ingested": 1}
    sha = out["files"][0]["retained_sha256"]
    opened = json.loads(cli(root, "artifact", "open", "--sha256", sha).stdout)
    assert opened["text"] == HEADER and opened["size"] == len(HEADER.encode())
    assert json.loads(cli(root, "artifact", "report").stdout)["status"] == "ok"
    missing = cli(root, "artifact", "open", "--sha256", "0" * 64)
    assert missing.returncode == 8 and json.loads(missing.stdout)["status"] == "artifact_missing"
    open(f"{folder}/notes.md", "w").write("not a drop-off\n")
    assert cli(root, "inbox", "--apply", "--settle", "0").returncode == 8                           # a held file is visible in the exit code

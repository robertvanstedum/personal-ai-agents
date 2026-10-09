"""What is open, what is closed and who may close it (acceptance rows D01, D02), and the command line (v0.6 section 13)."""
from __future__ import annotations

import json
import os
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


def decision(resolves, *, actor="robert", kind="approved-direct", item="spec:backup", authority=True, **over):
    if authority and kind != "proposed":
        over.setdefault("authority_ref", {"type": "owner-control", "ref": "synthetic:test"})
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


# ── workflow shorthands, brief and history on the command line ────────────────────────────────────────────────────
def test_cli_workflow_round_trip_with_dry_runs_and_stable_ids(root, tmp_path):
    def run(*args, stdin=None):
        out = cli(root, *args, stdin=stdin)
        return out.returncode, json.loads(out.stdout) if out.stdout.strip().startswith("{") else out.stdout
    req = {"actor": "claude-code", "kind": "request", "item": "spec:backup", "topic": "backup", "recipients": ["codex"], "text": "Review the plan.",
           "payload": {"action": "review", "expected_result": "Numbered findings."}}
    code, doc = run("append", "--file", write_env(tmp_path, req))
    assert code == 0
    rid = doc["event_id"]
    code, pending = run("pending", "--for", "codex", "--json")
    assert code == 0 and [r["request_id"] for r in pending["requests"]] == [rid] and pending["requests"][0]["status"] == "pending"
    assert run("pending", "--for", "grok-cli", "--json")[1]["requests"] == []
    code, dry = run("receipt", "--request", rid, "--actor", "codex", "--dry-run")
    assert code == 0 and dry["status"] == "would_commit" and len(Journal(root, WORKSHOP).read().events) == 1
    eid = new_id()
    assert run("receipt", "--request", rid, "--actor", "codex", "--event-id", eid)[1]["status"] == "committed"
    assert run("receipt", "--request", rid, "--actor", "codex", "--event-id", eid)[1]["status"] == "duplicate"
    assert run("pending", "--for", "codex", "--json")[1]["requests"][0]["status"] == "received"
    code, refused = run("receipt", "--request", rid, "--actor", "grok-cli")
    assert code == 2 and refused["reason"] == "recipient_not_on_the_request"
    code, claim = run("claim", "--request", rid, "--actor", "codex", "--resource", "checkout-a", "--expected-generation", "1")
    assert code == 0
    code, clash = run("claim", "--request", rid, "--actor", "codex", "--resource", "checkout-a", "--expected-generation", "2")
    assert code == 3 and clash["status"] == "claim_conflict"
    code, rel = run("release", "--claim", claim["event_id"], "--actor", "codex", "--generation", "1", "--stopped", "--reason", "finished")
    assert code == 0
    result_file = write_env(tmp_path, {"outcome": "completed", "limitations": ["did not run the build"], "text": "Two findings.",
                                       "test_summary": {"reported": 3, "run": 3, "passed": 3, "failed": 0, "not_run": 0}}, "r.json")
    code, res = run("result", "--request", rid, "--actor", "codex", "--file", result_file)
    assert code == 0 and res["status"] == "committed"
    assert run("pending", "--for", "codex", "--json")[1]["requests"] == []
    code, brief = run("brief", "--topic", "backup", "--now", "2026-10-09T00:00:00Z")
    assert code == 0 and brief["requests"][0]["state"] == "closed" and brief["test_reports"][0]["summary"]["passed"] == 3
    md = cli(root, "brief", "--topic", "backup", "--format", "md")
    assert md.returncode == 0 and md.stdout.startswith("# Brief: workshop-neubau / backup") and 'codex: result at ' in md.stdout and '"Two findings."' in md.stdout
    hist = json.loads(cli(root, "history", "--topic", "backup", "--through-seq", "2").stdout)
    assert [e["kind"] for e in hist["events"]] == ["request", "receipt"]
    assert cli(root, "history", "--topic", "backup", "--format", "md").stdout.count("\n") == 5


def test_cli_brief_and_history_refuse_a_damaged_or_missing_journal(root, tmp_path):
    assert cli(root, "brief").returncode == 8 and cli(root, "history").returncode == 8
    assert cli(root, "append", "--file", write_env(tmp_path, progress("x"))).returncode == 0
    open(f"{root}/{WORKSHOP}/events.jsonl", "ab").write(b"garbage\n")
    out = cli(root, "brief")
    assert out.returncode == 5 and json.loads(out.stdout)["status"] == "refused"
    assert cli(root, "history").returncode == 5 and cli(root, "pending", "--for", "codex", "--json").returncode == 5


# ── Codex R8: a history or brief export never looks complete when the journal is not ──────────────────────────────
def test_R8_history_and_brief_say_so_and_exit_incomplete_when_the_journal_has_a_partial_tail(root, tmp_path):
    assert cli(root, "append", "--file", write_env(tmp_path, progress("one"))).returncode == 0
    open(f"{root}/{WORKSHOP}/events.jsonl", "ab").write(b'{"v":2')
    md = cli(root, "history", "--format", "md")
    assert md.returncode == 8 and md.stdout.startswith("NOTICE: incomplete.") and "torn_tail" in md.stdout and "one" in md.stdout
    js = json.loads(cli(root, "history").stdout)
    assert js["complete"] is False and js["status"] == "torn_tail" and cli(root, "history").returncode == 8
    bmd = cli(root, "brief", "--format", "md")
    assert bmd.returncode == 8 and bmd.stdout.startswith("NOTICE: incomplete.")
    bjs = cli(root, "brief")
    assert json.loads(bjs.stdout)["complete"] is False and bjs.returncode == 8
    pend = cli(root, "pending", "--for", "codex", "--json")
    assert pend.returncode == 8 and json.loads(pend.stdout)["complete"] is False


def test_R8_a_live_writers_tail_is_reported_the_same_way(root, tmp_path):
    from test_safety import hold_lock
    assert cli(root, "append", "--file", write_env(tmp_path, progress("one"))).returncode == 0
    open(f"{root}/{WORKSHOP}/events.jsonl", "ab").write(b'{"v":2,"part')
    holder = hold_lock(root, 4)
    try:
        md = cli(root, "history", "--format", "md")
        assert md.returncode == 8 and "tail_in_progress" in md.stdout.splitlines()[0]
    finally:
        holder.kill()
        holder.wait()


def test_a_complete_journal_still_exits_zero_with_no_notice(root, tmp_path):
    assert cli(root, "append", "--file", write_env(tmp_path, progress("one"))).returncode == 0
    md = cli(root, "history", "--format", "md")
    assert md.returncode == 0 and "NOTICE" not in md.stdout and json.loads(cli(root, "history").stdout)["complete"] is True


def test_cli_notify_prints_the_frozen_notice_and_sends_nothing_and_routes_are_honest(root, tmp_path):
    req = {"actor": "claude-code", "kind": "request", "item": "spec:backup", "topic": "backup", "recipients": ["codex"], "text": "Review.",
           "payload": {"action": "review", "expected_result": "Findings."}}
    rid = json.loads(cli(root, "append", "--file", write_env(tmp_path, req)).stdout)["event_id"]
    out = cli(root, "notify", "--request", rid, "--to", "codex")
    assert out.returncode == 0 and out.stdout.startswith("WORKSHOP NOTICE v2\nSource: agent-authored message from claude-code.") and f"--home {root}" in out.stdout
    assert f"Request: {rid}\n" in out.stdout and "Review." not in out.stdout
    bad = cli(root, "notify", "--request", rid, "--to", "grok-cli")
    assert bad.returncode == 2 and json.loads(bad.stdout)["reason"] == "recipient is not on the request"
    assert cli(root, "notify", "--request", new_id(), "--to", "codex").returncode == 2
    table = json.loads(cli(root, "routes").stdout)["routes"]
    assert all(r["sends_automatically"] is False for r in table) and {r["actor"]: r["status"] for r in table}["grok-cli"] == "unverified"
    assert json.loads(cli(root, "routes", "--actor", "host").stdout)["routes"][0]["status"] == "unsupported"


def test_a_forgotten_id_never_falls_back_to_a_default_workshop(root, tmp_path):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
    for args in (["append", "--file", write_env(tmp_path, progress("x"))], ["brief"], ["history"], ["checkin"], ["verify", "--json"],
                 ["pending", "--for", "codex", "--json"]):
        out = subprocess.run([sys.executable, CLI, "--home", root, *args], capture_output=True, text=True, env=env, timeout=60)
        doc = json.loads(out.stdout)
        assert out.returncode == 2 and doc["reason"] == "workshop_id_required", args
    assert sorted(os.listdir(root)) == []                                                                    # nothing was created anywhere
    ok = subprocess.run([sys.executable, CLI, "--home", root, "append", "--file", write_env(tmp_path, progress("x"))], capture_output=True, text=True,
                        env={**env, "MINIMOI_WORKSHOP_ID": WORKSHOP}, timeout=60)
    assert ok.returncode == 0 and os.listdir(root) == [WORKSHOP]                                            # the environment variable still works


def test_the_old_commands_keep_their_default_workshop(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, CLI, "--home", str(tmp_path), "event", "--actor", "codex", "--kind", "progress", "--item", "queue:1", "--text", "x"],
                         capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0 and (tmp_path / "mac" / "events.jsonl").exists()


def test_brief_since_lists_what_changed_after_the_entry_you_last_saw(root, tmp_path):
    for text in ("one", "two"):
        assert cli(root, "append", "--file", write_env(tmp_path, progress(text), f"{text}.json")).returncode == 0
    req = {"actor": "claude-code", "kind": "request", "item": "spec:x", "topic": "x", "recipients": ["codex"], "text": "Review this.",
           "payload": {"action": "review", "expected_result": "Findings."}}
    assert cli(root, "append", "--file", write_env(tmp_path, req, "req.json")).returncode == 0
    q = {"actor": "claude-code", "kind": "needs_you", "item": "spec:x", "topic": "x", "recipients": ["robert"], "text": "Which one?",
         "payload": {"reason_code": "owner_choice", "requested_action": "Pick.", "incident_id": new_id()}}
    assert cli(root, "append", "--file", write_env(tmp_path, q, "q.json")).returncode == 0
    doc = json.loads(cli(root, "brief", "--since-seq", "2").stdout)["changed_since"]
    assert doc["since_seq"] == 2 and doc["entries"] == 2 and doc["by_kind"] == {"needs_you": 1, "request": 1}
    assert [x["text"] for x in doc["new_questions_for_robert"]] == ["Which one?"] and [x["text"] for x in doc["new_requests"]] == ["Review this."]
    md = cli(root, "brief", "--since-seq", "2", "--format", "md").stdout
    assert "## Since entry 2: 2 new (1 needs_you, 1 request)" in md and 'new question for Robert, entry 4' in md
    nothing = cli(root, "brief", "--since-seq", "4", "--format", "md").stdout
    assert "## Since entry 4: nothing new" in nothing
    assert "changed_since" not in json.loads(cli(root, "brief").stdout)

"""``moi list`` (owner terminal only) and ``moi designate`` for several records with one typed yes."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.memory_shelf import cli, record, ulid

from .test_cli import NO, REPO, World, YES
from .test_claude_ai_tree import ROOT, conversation, m
from .test_inbox import conv, make_zip


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    c1 = conversation("c-1", [m("r", ROOT, "human", "SECRET-BODY one", 1), m("a", "r", "assistant", "reply", 2)], name="First Plan")
    c2 = conversation("c-2", [m("r", ROOT, "human", "SECRET-BODY two", 1), m("a", "r", "assistant", "reply", 2),
                              m("b", "r", "assistant", "retry", 3)], name="Second Plan")
    c2["created_at"] = "2026-09-03T10:00:00Z"
    make_zip(world.inbox, "export.zip", [c1, c2])
    (world.inbox / "notes.txt").write_text("Human: pasted question\nClaude: pasted answer")
    world.run("dry-run", "inbox")
    world.run("approve-source", "inbox")
    assert world.run("inbox")[0] == 0
    return world


def events_of(w, rid):
    return record.load(w.shelf.find_record(rid).read_text())[0]["events"]


def ids(w, provider):
    return [e["id"] for e in w.shelf.list_records() if e["provider"] == provider]


def test_list_shows_titles_ids_dates_counts_state_and_editions_but_never_body_text(w):
    code, text = w.run("list", "claude-ai")
    assert code == 0 and "SECRET-BODY" not in text and "reply" not in text and "retry" not in text
    rows = [l.split("\t") for l in text.splitlines() if not l.startswith("#")]
    assert len(rows) == 2
    first, second = sorted(rows, key=lambda r: r[1])
    assert first[1:] == ["first-plan", "2026-09-01", "2", "raw", "deliberation", "1"]
    assert second[1] == "second-plan" and second[2] == "2026-09-03" and second[3] == "3"          # three messages, one a branch
    assert all(len(r[0]) == 8 for r in rows) and "# 2 records" in text
    assert "pasted" not in text                                                                    # filtered to the source
    assert "2 records" not in w.run("list")[1] and "# 3 records" in w.run("list")[1]


def test_list_state_follows_the_events_and_the_edition_count(w):
    rid = ids(w, "claude-ai")[0]
    w.run("designate", ulid.short(rid))
    assert "designated,deliberation" in w.run("list", "claude-ai")[1]
    w.run("approve", rid)
    assert "designated,approved" in w.run("list", "claude-ai")[1]
    c1 = conversation("c-1", [m("r", ROOT, "human", "SECRET-BODY one", 1), m("a", "r", "assistant", "reply", 2),
                              m("h", "a", "human", "more", 3)], name="First Plan")
    make_zip(w.inbox, "later.zip", [c1])
    w.run("dry-run", "inbox")
    w.run("approve-source", "inbox")
    w.run("inbox")
    assert any(r.endswith("\t2") for r in w.run("list", "claude-ai")[1].splitlines() if "first-plan" in r)


def test_list_refuses_without_a_real_terminal_and_prints_no_title(w):
    code, text = w.run("list", "claude-ai", tty=False)
    assert code == cli.REFUSED and "not_interactive" in text and "first-plan" not in text and "second-plan" not in text
    run = subprocess.run([sys.executable, "-m", "core.memory_shelf.cli", "--config", str(w.cfg_path), "list", "claude-ai"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)})
    assert run.returncode == cli.REFUSED and "not_interactive" in run.stdout and "first-plan" not in run.stdout


def test_list_excludes_canaries_unless_asked_and_rejects_an_unknown_source(w):
    w.run("canary", "emit")
    w.run("inbox")
    assert "canary" not in w.run("list")[1]
    assert "# 1 records" in w.run("list", "--canary")[1]
    assert w.run("list", "nonsense")[0] == cli.USAGE


def test_designate_several_records_with_one_yes_each_gets_its_own_event(w):
    a, b = ids(w, "claude-ai")
    prompts = []

    def once(prompt):
        prompts.append(prompt)
        return True
    code, text = w.run("designate", ulid.short(a), b, confirm=once)
    assert code == 0 and len(prompts) == 1 and "2 records" in prompts[0] and ulid.short(a) in prompts[0] and ulid.short(b) in prompts[0]
    for rid in (a, b):
        events = events_of(w, rid)
        assert [e["kind"] for e in events] == ["designated-curated"] and events[0]["via"] == "moi-designate"
    assert text.count("designated\t") == 2


def test_designate_skips_unknown_ambiguous_duplicate_and_already_designated_ids_without_guessing(w):
    a, b = ids(w, "claude-ai")
    assert w.run("designate", ulid.short(a))[0] == 0
    code, text = w.run("designate", ulid.short(a), ulid.short(b), "zzzzzzzz", ulid.short(b)[:6], ulid.short(b), "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert code == cli.REFUSED                                                  # something was skipped: the exit code says so
    assert f"skipped\t{ulid.short(a)}\talready_designated" in text and "skipped\tzzzzzzzz\tunknown_id" in text
    assert f"skipped\t{ulid.short(b)[:6]}\tunknown_id" in text and "skipped\t01ARZ3NDEKTSV4RRFFQ69G5FAV\tunknown_id" in text
    assert f"designated\t{ulid.short(b)}" in text and [e["kind"] for e in events_of(w, b)] == ["designated-curated"]
    assert len(events_of(w, a)) == 1                                           # not designated twice


def test_designate_declined_or_without_a_terminal_changes_nothing_and_never_approves(w):
    a, b = ids(w, "claude-ai")
    assert w.run("designate", a, b, confirm=NO)[0] == cli.REFUSED
    assert events_of(w, a) == [] and events_of(w, b) == []
    run = subprocess.run([sys.executable, "-m", "core.memory_shelf.cli", "--config", str(w.cfg_path), "designate", a, b],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)})
    assert run.returncode == cli.REFUSED and events_of(w, a) == []
    w.run("designate", a, b)
    assert all(e["kind"] == "designated-curated" for rid in (a, b) for e in events_of(w, rid))        # designation only


def test_designate_with_nothing_valid_does_not_even_ask(w):
    asked = []
    code, text = w.run("designate", "zzzzzzzz", confirm=lambda p: asked.append(p) or True)
    assert code == cli.REFUSED and asked == [] and "no record to designate" in text


def test_designate_still_takes_a_candidate_from_review(w):
    (w.inbox / "p2.txt").write_text("Human: file this please\nClaude: ok")
    w.run("dry-run", "inbox")
    w.run("approve-source", "inbox")
    w.run("inbox")
    cand = next(l for l in w.run("review")[1].splitlines() if l.startswith("designation-candidate")).split("\t")[1]
    assert w.run("designate", cand)[0] == 0 and "designation-candidate\t" not in w.run("review")[1]

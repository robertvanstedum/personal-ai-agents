"""``moi`` (R2, R4): review, approve (owner-only), approve-source, canary, ledger, fidelity."""
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.memory_shelf import cli, record, review, weight
from core.memory_shelf.shelf import Shelf

from .helpers import FAKE_KEY, cc_lines, write_tree
from .test_inbox import conv, make_zip

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[2]
YES, NO = (lambda prompt: True), (lambda prompt: False)


class World:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.cc = tmp_path / "cc"
        write_tree(self.cc, {"proj/s-1.jsonl": cc_lines("s-1", ("human", "SECRET-CLI-CONTENT"), ("assistant", "ok"))})
        self.inbox = tmp_path / "inbox"
        self.cfg_path = tmp_path / "cfg.json"
        self.cfg_path.write_text(json.dumps({
            "schema_version": 1, "shelf_root": str(tmp_path / "shelf"), "inbox_root": str(self.inbox),
            "repo_root": str(tmp_path / "repo"), "headroom": {"min_free_bytes": 0},
            "sources": {"claude-code": {"kind": "claude-code", "root": str(self.cc)}}}))
        write_tree(tmp_path / "repo", {"docs/DECISIONS.md": "# d\n"})

    def run(self, *argv, confirm=YES):
        buf = io.StringIO()
        code = cli.main(["--config", str(self.cfg_path), *argv], confirm=confirm, out=buf, now=NOW)
        return code, buf.getvalue()

    @property
    def shelf(self):
        return Shelf(self.tmp / "shelf", min_free_bytes=0)


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def captured(w):
    assert w.run("dry-run", "claude-code")[0] == 0
    assert w.run("approve-source", "claude-code")[0] == 0
    assert w.run("watch", "claude-code")[0] == 0
    return w.shelf


def test_source_flow_dry_run_then_approval_then_capture(w):
    code, text = w.run("watch", "claude-code")
    assert "dry_run_only" in text and w.shelf.list_records() == []
    code, text = w.run("review")
    assert "dry-run\tclaude-code\tnot_approved" in text and "SECRET-CLI-CONTENT" not in text
    assert w.run("approve-source", "claude-code", confirm=NO)[0] == cli.REFUSED
    assert w.run("watch", "claude-code")[1].startswith("watch\tclaude-code\tnot_approved")
    assert w.run("approve-source", "claude-code")[0] == 0
    code, text = w.run("watch", "claude-code")
    assert '"captured": 1' in text and len(w.shelf.list_records()) == 1
    assert "dry-run\tclaude-code\tapproved" in w.run("review")[1]


def test_approve_unknown_source_and_missing_dry_run_are_refused(w):
    assert w.run("approve-source", "nonsense")[0] == cli.USAGE
    code, text = w.run("approve-source", "claude-code")
    assert code == cli.REFUSED and "no dry-run listing" in text


def test_approve_record_needs_confirmation_and_only_then_adds_the_event(w):
    shelf = captured(w)
    rid = shelf.list_records()[0]["id"]
    assert w.run("approve", rid, confirm=NO)[0] == cli.REFUSED
    meta, _ = record.load(shelf.main_path(shelf.list_records()[0]).read_text())
    assert meta["events"] == []
    code, text = w.run("approve", rid)
    assert code == 0 and "approved-direct" in text
    meta, _ = record.load(shelf.main_path(shelf.list_records()[0]).read_text())
    assert [e["kind"] for e in meta["events"]] == ["approved-direct"] and weight.derive(meta)["weight"] == "approved"
    mandate = "0" * 25 + "1"
    assert w.run("approve", rid, "--under", mandate)[0] == 0 and w.run("approve", rid, "--under", "bad")[0] == cli.REFUSED
    assert w.run("approve", "NOTAREALID")[0] == cli.REFUSED


def test_there_is_no_yes_flag_and_a_non_tty_shell_cannot_approve(w):
    shelf = captured(w)
    rid = shelf.list_records()[0]["id"]
    with pytest.raises(SystemExit):
        cli.main(["--config", str(w.cfg_path), "approve", rid, "--yes"], out=io.StringIO())
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    for args in (["approve", rid], ["approve-source", "claude-code"], ["designate", rid]):
        run = subprocess.run([sys.executable, "-m", "core.memory_shelf.cli", "--config", str(w.cfg_path), *args],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, cwd=REPO)
        assert run.returncode == cli.REFUSED and "not_interactive_or_declined" in run.stdout
    meta, _ = record.load(shelf.main_path(shelf.list_records()[0]).read_text())
    assert meta["events"] == []


def test_review_lists_candidates_flags_and_refusals_metadata_only(w):
    w.inbox.mkdir()
    (w.inbox / "p1.txt").write_text("Human: file this, SECRET-PASTE-TEXT\nClaude: ok")
    (w.inbox / "junk.bin").write_bytes(b"\x00\x01")
    make_zip(w.inbox, "e.zip", [conv("u-1", "Chat", [("human", "file this, SECRET-PASTE-TEXT"), ("assistant", "x")])])
    assert w.run("inbox")[0] == 0
    code, text = w.run("review")
    assert "designation-candidate\t" in text and "possible-same\t" in text and "refused\tjunk.bin\tunsupported_type" in text
    assert "SECRET-PASTE-TEXT" not in text
    cand = next(l for l in text.splitlines() if l.startswith("designation-candidate")).split("\t")[1]
    assert w.run("designate", cand, confirm=NO)[0] == cli.REFUSED
    assert w.run("designate", cand)[0] == 0
    assert "designation-candidate\t" not in w.run("review")[1]


def test_canary_end_to_end_and_ledger(w):
    assert w.run("canary", "emit")[1].startswith("canary\temitted\tcanary-2026-10-03.canary.json")
    assert w.run("inbox")[0] == 0
    assert w.run("canary", "check")[0] == 0 and "ok=1" in w.run("canary", "check")[1]
    code, text = w.run("ledger")
    assert code == 0 and text.strip().endswith("OK") and "inbox" not in text                 # canary not in ordinary totals
    code, text = w.run("ledger", "--canary")
    assert code == 0 and "inbox\texpected=1\tcaptured=1" in text
    assert w.run("fidelity", "--canary")[0] == 0


def test_ledger_exit_code_flags_missing_and_names_it(w):
    from core.memory_shelf import codes, ledger
    ledger.record(w.shelf, "claude-code", "k1", codes.DISCOVERED)
    code, text = w.run("ledger")
    assert code == cli.REFUSED and "MISSING 1" in text and '"discovered": 1' in text


def test_fidelity_sample_and_drain_and_backfill_dry_run(w):
    captured(w)
    code, text = w.run("fidelity", "--sample", "3")
    assert code == 0 and "checked=1\tok=1\tfailed=0" in text
    assert w.run("drain")[0] == 0
    code, text = w.run("dry-run", "backfill-guild")
    assert code == 0 and "would_import" in text and w.run("review")[1].count("backfill-guild") == 1


def test_bad_config_is_a_clean_failure(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    buf = io.StringIO()
    assert cli.main(["--config", str(bad), "review"], out=buf) == cli.USAGE and "config problem" in buf.getvalue()

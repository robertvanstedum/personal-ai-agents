import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.mc_notes import register as R

REPO = Path(__file__).resolve().parents[2]
NOV = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)


def at(day, hour=12, month=11):
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


def test_notes_are_lines_in_one_monthly_file(tmp_path):
    a = R.add(str(tmp_path), by="claude-code", text="first", topic="rooms archive", ref="#523", now=NOV)
    b = R.add(str(tmp_path), by="codex", text="second", now=NOV)
    path = tmp_path / "notes-2026-11-mac.jsonl"
    assert [p.name for p in tmp_path.iterdir()] == [path.name]
    lines = path.read_bytes().splitlines()
    assert [json.loads(x)["id"] for x in lines] == [a["id"], b["id"]]
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert a["prev"] == "" and b["prev"] == R._sha(lines[0] + b"\n")
    assert a["said_by"] == "robert" and a["for"] == "remember" and a["host"] == "mac"


def test_verify_detects_an_edited_or_removed_line(tmp_path):
    for i in range(3):
        R.add(str(tmp_path), by="mc", text=f"note {i}", now=NOV)
    assert R.verify(str(tmp_path))["ok"]
    path = tmp_path / "notes-2026-11-mac.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(lines[0] + lines[1].replace(b"note 1", b"note X") + lines[2])
    rep = R.verify(str(tmp_path))
    assert not rep["ok"] and "line 3: chain broken" in rep["files"][path.name]["problems"]
    path.write_bytes(lines[0] + lines[2])
    assert not R.verify(str(tmp_path))["ok"]


def test_new_month_seals_the_previous_month(tmp_path):
    R.add(str(tmp_path), by="mc", text="oct", now=at(31, month=10))
    old = tmp_path / "notes-2026-10-mac.jsonl"
    assert old.stat().st_mode & 0o200
    R.add(str(tmp_path), by="mc", text="nov", now=at(1))
    assert oct(old.stat().st_mode & 0o777) == "0o400"
    with pytest.raises(R.Refused) as e:
        R.add(str(tmp_path), by="mc", text="late", now=at(30, month=10))
    assert e.value.code == "sealed"
    rep = R.verify(str(tmp_path))
    assert rep["ok"] and rep["files"][old.name]["sealed"]


def test_hosts_write_separate_files_and_reads_merge(tmp_path):
    a = R.add(str(tmp_path), by="claude-code", text="mac", host="mac", now=at(12, 9))
    b = R.add(str(tmp_path), by="mc", text="prod", host="prod", now=at(12, 10))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes-2026-11-mac.jsonl", "notes-2026-11-prod.jsonl"]
    assert [n["id"] for n in R.all_notes(str(tmp_path))] == [a["id"], b["id"]]


def test_host_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMOI_MC_NOTES_HOST", "prod")
    R.add(str(tmp_path), by="mc", text="x", now=NOV)
    assert (tmp_path / "notes-2026-11-prod.jsonl").is_file()


def test_replaces_keeps_history_and_current_view(tmp_path):
    a = R.add(str(tmp_path), by="codex", text="first", now=at(12, 9))
    b = R.add(str(tmp_path), by="codex", text="changed mind", replaces=[a["id"]], now=at(12, 14))
    notes = R.all_notes(str(tmp_path))
    assert [n["id"] for n in notes] == [a["id"], b["id"]]
    assert [n["id"] for n in R.current(notes)] == [b["id"]]


def test_replaces_may_name_a_note_not_yet_copied(tmp_path):
    n = R.add(str(tmp_path), by="grok", text="x", replaces=["n-20261111T000000Z-abcdef"])
    assert [x["id"] for x in R.current(R.all_notes(str(tmp_path)))] == [n["id"]]


@pytest.mark.parametrize("kw,code", [
    ({"by": "someone"}, "unknown_writer"),
    ({"said_by": "nobody"}, "unknown_speaker"),
    ({"what_for": "later"}, "unknown_for"),
    ({"text": "   "}, "empty"),
    ({"text": "x" * (R.TEXT_MAX + 1)}, "too_long"),
    ({"replaces": ["r-123"]}, "bad_replaces"),
    ({"host": "Bad Host"}, "bad_host"),
])
def test_refusals_write_nothing(tmp_path, kw, code):
    with pytest.raises(R.Refused) as e:
        R.add(str(tmp_path), **{"by": "claude-code", "text": "ok", **kw})
    assert e.value.code == code
    assert not list(tmp_path.glob("notes-*"))


def test_text_is_cleaned_and_one_line(tmp_path):
    n = R.add(str(tmp_path), by="mc", text="a\x07b\r\nc", topic="  two   words ", now=NOV)
    assert n["text"] == "ab\nc" and n["topic"] == "two words"
    assert len((tmp_path / "notes-2026-11-mac.jsonl").read_bytes().splitlines()) == 1


def test_bad_lines_skipped_on_read(tmp_path):
    good = R.add(str(tmp_path), by="mc", text="good", now=NOV)
    with open(tmp_path / "notes-2026-11-mac.jsonl", "ab") as fh:
        fh.write(b"not json\n")
    assert [n["id"] for n in R.all_notes(str(tmp_path))] == [good["id"]]
    assert not R.verify(str(tmp_path))["ok"]


def test_root_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMOI_MC_NOTES_DIR", str(tmp_path / "env-root"))
    R.add(None, by="robert", text="from env", now=NOV)
    assert (tmp_path / "env-root" / "notes-2026-11-mac.jsonl").is_file()


def test_cli_add_list_show_verify(tmp_path):
    run = lambda *a, **k: subprocess.run([sys.executable, str(REPO / "scripts/mc_notes.py"), "--root", str(tmp_path), *a],
                                         capture_output=True, text=True, **k)
    r = run("add", "--by", "claude-code", "--topic", "hosts", "--text", "-", input="Three tiers, restated.\n")
    assert r.returncode == 0
    nid = r.stdout.strip()
    r2 = run("add", "--by", "claude-code", "--for", "remind", "--replaces", nid, "--text", "Updated.")
    assert r2.returncode == 0
    listed = run("list").stdout
    assert r2.stdout.strip() in listed and not listed.startswith(nid)
    assert nid in run("list", "--all").stdout
    assert "Three tiers" in run("show", nid).stdout
    v = run("verify")
    assert v.returncode == 0 and "2 notes, open" in v.stdout
    bad = run("add", "--by", "claude-code", "--text", "")
    assert bad.returncode == 2 and "refused: empty" in bad.stderr

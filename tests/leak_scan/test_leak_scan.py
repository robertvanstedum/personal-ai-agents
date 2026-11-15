"""The leak scan stays on this machine and does not repeat a matched span on stdout."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core import leak_scan as scan
from utils.credential_scrub import SECRET


EIGHT = "amber lark dock north soft river quiet stone"
NAME = "Lark Harbor"


def _git(path: Path, *args: str) -> None:
    subprocess.check_call(["git", "-C", str(path), *args], stdout=subprocess.DEVNULL)


def _repo(path: Path) -> None:
    subprocess.check_call(["git", "init", "-b", "main", str(path)], stdout=subprocess.DEVNULL)
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")


def _commit(path: Path, files: dict[str, str], message: str = "add") -> None:
    for name, text in files.items():
        dest = path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    _git(path, "add", ".")
    _git(path, "commit", "-m", message)


def _private(path: Path, prose: dict[str, str], voice: str = "", identifiers: str = "") -> None:
    for name, text in prose.items():
        dest = path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    folder = path / "leak-scan"
    folder.mkdir()
    (folder / "voice.txt").write_text(voice)
    (folder / "identifiers.txt").write_text(identifiers)


def test_a_copied_passage_and_a_repeated_name_are_hits(tmp_path):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    _commit(public, {"notes.md": f"Intro {EIGHT} end.\nMeet {NAME} once.\n"})
    _private(private, {"note.md": f"{EIGHT}\n" + "\n".join([NAME] * 3) + "\n"})
    summary = scan.run(public, private, "baseline", github=False)
    assert summary.by_check["passage"] == 1
    assert summary.by_check["name"] == 1
    assert summary.notified is False
    report = summary.report.read_text()
    assert EIGHT in report
    assert NAME in report
    assert "telegram: no" in report


def test_boilerplate_product_names_and_bare_decision_lines_are_not_hits(tmp_path):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    repeated = " ".join([EIGHT] * 10)
    gap = "alpha bravo charlie delta echo foxtrot golf hotel"
    _commit(public, {"notes.md": f"{repeated}\nClaude Code owns the review.\nRobert decided the window stays open today.\n"})
    _private(private, {"note.md": f"{repeated}\n{gap}\n" + "\n".join(["Claude Code"] * 4) + "\n"})
    summary = scan.run(public, private, "baseline", github=False)
    assert summary.hits == 0


def test_a_byte_copy_of_a_public_file_is_not_a_source(tmp_path):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    same = f"Shared file. {EIGHT}.\n"
    _commit(public, {"shared.md": same, "other.md": "nothing here"})
    _private(private, {"shared.md": same})
    summary = scan.run(public, private, "baseline", github=False)
    assert summary.by_check["passage"] == 0


def test_voice_starts_empty_and_an_added_phrase_hits(tmp_path):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    _commit(public, {"notes.md": "Please keep the quiet window please for now.\n"})
    _private(private, {"note.md": "unrelated words only here today friend"})
    assert scan.run(public, private, "baseline", github=False).by_check["voice"] == 0
    (private / "leak-scan" / "voice.txt").write_text("quiet window please\n")
    summary = scan.run(public, private, "baseline", github=False)
    assert summary.by_check["voice"] == 1


def test_identifiers_match_whole_tokens_and_short_lines_are_refused(tmp_path):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    _commit(public, {"notes.md": "account 9911223344 and also 991122334455\n"})
    _private(private, {"note.md": "no overlap with the number today friend extra"}, identifiers="12\n9911223344\n")
    summary = scan.run(public, private, "baseline", github=False)
    assert summary.by_check["identifier"] == 1
    report = summary.report.read_text()
    assert "9911223344" not in report
    assert "identifiers_rejected: 1" in report


def test_a_key_hit_records_the_place_and_not_the_value(tmp_path):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    _commit(public, {"notes.md": "token TESTKEY-AAAAAAAA sits here\n"})
    _private(private, {"note.md": "nothing private in this short note today"})
    key_re = __import__("re").compile(r"TESTKEY-[A-Z]{8}")
    summary = scan.run(public, private, "baseline", github=False, key_re=key_re)
    assert summary.by_check["key"] == 1
    assert "TESTKEY-AAAAAAAA" not in summary.report.read_text()
    assert scan.SECRET is SECRET


def test_watch_hides_allowlisted_spans_and_notifies_without_the_span(tmp_path):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    _commit(public, {"notes.md": f"{EIGHT}\n"})
    _private(private, {"note.md": f"{EIGHT}\nlocal margin stays here today\n"})
    first = scan.run(public, private, "baseline", github=False)
    digest = scan.span_hash(EIGHT)
    folder = private / "leak-scan"
    (folder / "allowlist.jsonl").write_text(json.dumps({"check": "passage", "hash": digest, "note": "accepted"}) + "\n")
    sent = []
    summary = scan.run(public, private, "watch", github=False, telegram=sent.append, mc_note=sent.append)
    assert summary.hits == 0
    assert summary.suppressed == 1
    assert summary.notified is False
    assert sent == []
    (folder / "allowlist.jsonl").unlink()
    sent.clear()
    summary = scan.run(public, private, "watch", github=False, telegram=sent.append, mc_note=sent.append)
    assert summary.hits == 1
    assert summary.notified is True
    assert len(sent) == 2
    assert all(EIGHT not in item for item in sent)
    assert all(item.startswith("Leak scan: 1 new hits. Report: ") for item in sent)


def test_another_branch_is_scanned(tmp_path):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    _commit(public, {"readme.md": "public readme only\n"})
    _git(public, "checkout", "-b", "other")
    _commit(public, {"side.md": f"Side note {EIGHT}.\n"})
    _private(private, {"note.md": f"{EIGHT}\n"})
    summary = scan.run(public, private, "baseline", github=False)
    assert summary.by_check["passage"] == 1
    assert "blob:side.md@" in summary.report.read_text()


def test_an_unpushed_branch_is_not_part_of_the_public_corpus(tmp_path):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    _commit(public, {"readme.md": "public readme only\n"})
    _git(public, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(public, "checkout", "-b", "local-only")
    _commit(public, {"side.md": f"Side note {EIGHT}.\n"})
    _private(private, {"note.md": f"{EIGHT}\nlocal margin stays here today\n"})
    summary = scan.run(public, private, "baseline", github=False)
    assert summary.by_check["passage"] == 0


def test_a_shallow_clone_is_refused(tmp_path):
    origin, shallow, private = tmp_path / "origin", tmp_path / "shallow", tmp_path / "private"
    _repo(origin)
    _commit(origin, {"readme.md": "hello\n"})
    subprocess.check_call(["git", "clone", "--depth", "1", origin.as_uri(), str(shallow)], stdout=subprocess.DEVNULL)
    _private(private, {"note.md": "hello there friend today"})
    with pytest.raises(scan.ScanError):
        scan.run(shallow, private, "baseline", github=False)


def test_a_missing_private_tree_is_not_copied(tmp_path, capsys):
    public = tmp_path / "public"
    _repo(public)
    _commit(public, {"readme.md": "hello\n"})
    code = scan.main(["--public", str(public), "--private", str(tmp_path / "missing"), "--baseline", "--no-github"])
    assert code == 2
    assert "not copied" in capsys.readouterr().err


def test_stdout_is_counts_only(tmp_path, capsys):
    public, private = tmp_path / "public", tmp_path / "private"
    _repo(public)
    _commit(public, {"notes.md": f"{EIGHT}\n"})
    _private(private, {"note.md": f"{EIGHT}\nlocal margin stays here today\n"})
    code = scan.main(["--public", str(public), "--private", str(private), "--baseline", "--no-github"])
    assert code == 0
    out = capsys.readouterr().out
    assert "passage=1" in out
    assert EIGHT not in out

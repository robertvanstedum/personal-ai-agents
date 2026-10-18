"""The exporter's ``--stdout-tar`` mode (17 Oct 2026): the production MC capture path reads the export from the portal container's stdout,
so no plaintext lands in the container's filesystem. Synthetic stores only (test_mc_capture's World: the real conversation store on a tmp
folder and a SQLite floor built from the portal's own migration)."""
from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path

import pytest

from core.memory_sync import mc_batch as MB
from core.memory_sync import mc_export as MX
from tests.memory_shelf.test_mc_capture import OTHER, PRINCIPAL, World, X


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def populate(world):
    keep = world.conversation(PRINCIPAL, ("owner", "STREAM-ALPHA question"), ("mc", "STREAM-ALPHA answer"))
    gone = world.conversation(PRINCIPAL, ("owner", "STREAM-BETA question"), ("mc", "STREAM-BETA answer"))
    world.store.set_archived(gone["id"], PRINCIPAL, True)
    world.conversation(OTHER, ("owner", "VISITOR-SECRET words"), ("mc", "VISITOR-REPLY words"))
    thread = world.store.get("shop-floor-thread", PRINCIPAL)
    world.say(thread, ("owner", "THREAD-OWNER words"), ("mc", "THREAD-MC words"))
    from minimoi_portal.guild_ui.stores import Author
    world.say(thread, (Author(OTHER, "owner", "Visitor"), "THREAD-VISITOR words"))
    hidden = world.conversation(PRINCIPAL, ("owner", "OFFREC-SECRET words"))
    path = world.guild / "conversations" / f"{hidden['id']}.json"
    data = json.loads(path.read_text())
    data["record_mode"] = "off_record"                               # a record that ever says off_record is refused whole
    path.write_text(json.dumps(data))
    return keep, gone, hidden


def members(stream: bytes) -> list:
    with tarfile.open(fileobj=io.BytesIO(stream), mode="r:") as tar:
        return [(m.name, m.mode, m.mtime, tar.extractfile(m).read()) for m in tar]


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_stream_holds_exactly_the_folder_modes_files_and_the_same_rules(world, tmp_path):
    keep, gone, hidden = populate(world)
    folder = X.export(guild_dir=world.guild, out=tmp_path / "folder", principal=PRINCIPAL, instance="production", floor_for=world.floor)
    buf = io.BytesIO()
    streamed = X.export(guild_dir=world.guild, out=None, principal=PRINCIPAL, instance="production", floor_for=world.floor, tar_out=buf)
    got = members(buf.getvalue())
    files = {p.name: p.read_bytes() for p in (tmp_path / "folder").iterdir() if MB.FILE.match(p.name)}
    assert len(files) == 3                                           # keep, gone (archived) and the owner's Shop floor thread
    assert [n for n, *_ in got[:-1]] == sorted(files) and {n: b for n, _m, _t, b in got[:-1]} == files    # the same canonical bytes
    assert all(mode == 0o600 and mtime == 0 for _n, mode, mtime, _b in got)
    name, _m, _t, last = got[-1]
    assert name == X.EXPORT_DOC == MX.EXPORT_DOC
    doc = json.loads(last)
    assert doc == {"status": "ok", "counts": streamed["counts"]}
    assert doc["counts"]["exported"] == 3 and doc["counts"]["other_principal"] == folder["counts"]["other_principal"] == 2
    assert doc["counts"]["off_record"] == 1 and doc["counts"]["other_owner_note"] == 1
    blob = buf.getvalue()
    for secret in (b"VISITOR", b"OFFREC-SECRET", PRINCIPAL.encode(), hidden["id"].encode()):
        assert secret not in blob                                    # another principal's words, off-record content, the account name
    assert b"STREAM-ALPHA" in blob and keep["id"].encode() in blob and gone["id"].encode() in blob


def test_stdout_tar_writes_nothing_to_any_folder_and_counts_go_to_stderr(world, tmp_path, monkeypatch, capsys):
    populate(world)
    monkeypatch.setattr(X, "database_floor", lambda: world.floor)
    monkeypatch.chdir(tmp_path)
    before = tree(tmp_path)
    stream = io.BytesIO()
    rc = X.main(["--stdout-tar", "--principal", PRINCIPAL, "--instance", "production", "--guild-dir", str(world.guild)], stream=stream)
    assert rc == 0
    assert tree(tmp_path) == before                                 # no --out, no status file, no temp file
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("export\tok\t") and "STREAM" not in err
    assert [n for n, *_ in members(stream.getvalue())][-1] == "EXPORT.json"


def test_stdout_tar_refuses_an_out_folder_or_a_dry_run(world, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(X, "database_floor", lambda: world.floor)
    for extra in (["--out", str(tmp_path / "o")], ["--dry-run"]):
        stream = io.BytesIO()
        rc = X.main(["--stdout-tar", "--principal", PRINCIPAL, "--instance", "production", "--guild-dir", str(world.guild), *extra],
                    stream=stream)
        assert rc == 2 and stream.getvalue() == b"" and not (tmp_path / "o").exists()
        out, err = capsys.readouterr()
        assert out == "" and err.startswith("export\tusage")


def test_a_partial_export_streams_what_it_could_and_says_so(world, monkeypatch, capsys):
    world.conversation(PRINCIPAL, ("owner", "small one"))
    world.conversation(PRINCIPAL, *[("owner", f"n{i}") for i in range(5)])
    monkeypatch.setattr(X, "MAX_NOTES", 3)
    monkeypatch.setattr(X, "database_floor", lambda: world.floor)
    stream = io.BytesIO()
    rc = X.main(["--stdout-tar", "--principal", PRINCIPAL, "--instance", "production", "--guild-dir", str(world.guild)], stream=stream)
    got = members(stream.getvalue())
    assert rc == 1 and len(got) == 2
    doc = json.loads(got[-1][3])
    assert doc["status"] == "partial" and doc["counts"]["too_large"] == 1 and doc["counts"]["exported"] == 1
    capsys.readouterr()


def test_the_folder_mode_is_unchanged(world, tmp_path):
    populate(world)
    first = world.export()
    assert first["counts"]["new"] == 3 and "exported" not in first["counts"]
    assert (world.out / X.STATUS_FILE).is_file()
    assert world.export()["counts"]["unchanged"] == 3


def test_a_conversation_whose_turns_are_all_blank_is_empty_in_both_modes(world, tmp_path):
    """The shelf reader's own rule (core/memory_shelf/mc.py skips a blank turn; no turn left is ``no_turns``): a file it can never record
    is never exported, or it would be re-sent forever (review MEDIUM-2). One real turn among blank ones still exports."""
    blank = world.conversation(PRINCIPAL, ("owner", "   "), ("owner", "\n\t "))
    mixed = world.conversation(PRINCIPAL, ("owner", "  "), ("owner", "MIXED real words"))
    folder = X.export(guild_dir=world.guild, out=tmp_path / "folder", principal=PRINCIPAL, instance="production", floor_for=world.floor)
    buf = io.BytesIO()
    streamed = X.export(guild_dir=world.guild, out=None, principal=PRINCIPAL, instance="production", floor_for=world.floor, tar_out=buf)
    names = [n for n, *_ in members(buf.getvalue())]
    assert f"production--{mixed['id']}.json" in names and not any(blank["id"] in n for n in names)
    assert not any(blank["id"] in p.name for p in (tmp_path / "folder").iterdir())
    assert folder["counts"]["empty"] == streamed["counts"]["empty"] == 2               # the blank one and the owner's empty Shop floor thread
    assert streamed["counts"]["exported"] == 1 and folder["counts"]["new"] == 1


STRAY = r'''
import os, sys
from pathlib import Path
sys.path.insert(0, os.getcwd())
from tests.memory_shelf.test_mc_capture import PRINCIPAL, World, X
w = World(Path(sys.argv[1]))
w.conversation(PRINCIPAL, ("owner", "STREAM-GAMMA words"), ("mc", "STREAM-GAMMA reply"))
X.database_floor = lambda: w.floor
real = X.export
def noisy(**kw):
    print("STRAY-PRINT")                    # a stray print in the exporter's process
    os.write(1, b"STRAY-FD\n")              # and a raw write to descriptor 1 (a library, a child process)
    return real(**kw)
X.export = noisy
sys.exit(X.main(["--stdout-tar", "--principal", PRINCIPAL, "--instance", "production", "--guild-dir", str(w.guild)]))
'''


def test_a_stray_print_cannot_corrupt_the_real_stdout_stream(tmp_path):
    """Review LOW-3: in the real process (no ``stream=``), the tar owns a duplicate of the real stdout and descriptor 1 / ``sys.stdout``
    point at stderr, so anything else written to stdout lands on stderr and the stream stays a well-formed tar."""
    import subprocess
    import sys
    (tmp_path / "w").mkdir()
    done = subprocess.run([sys.executable, "-I", "-c", STRAY, str(tmp_path / "w")], cwd=str(Path(__file__).resolve().parents[2]),
                          capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode()[-2000:]
    got = members(done.stdout)
    assert [n for n, *_ in got][-1] == "EXPORT.json" and len(got) == 2 and b"STREAM-GAMMA" in got[0][3]
    assert b"STRAY" not in done.stdout
    err = done.stderr.decode()
    assert "STRAY-PRINT" in err and "STRAY-FD" in err and "export\tok\t" in err

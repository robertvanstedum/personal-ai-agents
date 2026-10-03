"""T3: selection (agent-memory v0.4 §3.3, §3.5)."""
import os

import pytest

from core.agent_memory import selection as sel
from core.agent_memory.selection import Rules, classify_path, select
from core.agent_memory.sources import DirectorySource, TarSource

from agent_memory.agent_memory_helpers import make_tar, write_tree


def test_defaults_copy_only_memory_files():
    files = {"MEMORY.md": b"a", "memory/x.md": b"b", "memory/deep/y.md": b"c",
             "notes.md": b"d", "other/z.md": b"e"}
    got = select(files, Rules())
    assert sorted(got.files) == ["MEMORY.md", "memory/deep/y.md", "memory/x.md"]
    assert got.skipped[sel.NOT_SELECTED] == 2


def test_persona_files_only_when_listed_in_include():
    files = {"SOUL.md": b"s", "AGENTS.md": b"a", "memory/IDENTITY.md": b"i", "skills/x/SKILL.md": b"k",
             "MEMORY.md": b"m"}
    assert sorted(select(files, Rules()).files) == ["MEMORY.md"]
    got = select(files, Rules(include=("SOUL.md",)))
    assert sorted(got.files) == ["MEMORY.md", "SOUL.md"]
    assert got.skipped[sel.PERSONA_NOT_INCLUDED] == 3


def test_never_copy_is_applied_first_and_counted_not_named():
    files = {"memory/private.md": b"p", "memory/diary/a.md": b"d", "memory/ok.md": b"o", "MEMORY.md": b"m"}
    got = select(files, Rules(never_copy=("memory/private.md", "diary")))
    assert sorted(got.files) == ["MEMORY.md", "memory/ok.md"]
    assert dict(got.skipped) == {sel.NEVER_COPY: 2}


@pytest.mark.parametrize("name,reason", [
    (".env", sel.HIDDEN), (".env.local", sel.HIDDEN), (".hidden.md", sel.HIDDEN),
    ("memory/.git/x.md", sel.HIDDEN),
    ("auth.json", sel.DENIED_NAME), ("memory/auth-notes.md", sel.DENIED_NAME),
    ("memory/my_credentials.md", sel.DENIED_NAME), ("memory/token-ideas.md", sel.DENIED_NAME),
    ("openclaw.json", sel.DENIED_EXT), ("x.sqlite", sel.DENIED_EXT), ("x.sqlite3", sel.DENIED_EXT),
    ("a.db", sel.DENIED_EXT), ("log.jsonl", sel.DENIED_EXT), ("k.key", sel.DENIED_EXT), ("c.pem", sel.DENIED_EXT),
    ("sessions/a.md", sel.SESSIONS), ("memory/sessions/b.md", sel.SESSIONS),
    ("notes.txt", sel.NOT_MARKDOWN), ("../x.md", sel.UNSAFE_PATH), ("/etc/passwd.md", sel.UNSAFE_PATH),
    ("memory/../../x.md", sel.UNSAFE_PATH), ("memory\\x.md", sel.UNSAFE_PATH),
])
def test_refusals(name, reason):
    assert classify_path(name, Rules(include=("**/*.md",)))[1] == reason


def test_refusals_cannot_be_overridden_by_include():
    got = select({"auth.json": b"{}", ".env": b"K=1", "sessions/a.md": b"x"}, Rules(include=("*", "**/*")))
    assert got.files == {}


def test_size_and_encoding_limits():
    files = {"memory/big.md": b"x" * (600 * 1024), "memory/bin.md": b"\xff\xfe\x00bad", "memory/ok.md": b"fine",
             "memory/edge.md": b"y" * sel.MAX_BYTES}
    got = select(files, Rules())
    assert sorted(got.files) == ["memory/edge.md", "memory/ok.md"]
    assert got.skipped[sel.TOO_LARGE] == 1 and got.skipped[sel.NOT_UTF8] == 1


def test_directory_source_refuses_symlinks_and_never_reads_junk(tmp_path):
    base = tmp_path / "ws"
    write_tree(base, {"MEMORY.md": b"m", "memory/a.md": b"a", ".env": b"K=v", "auth.json": b"{}",
                      "sessions/a.md": b"s", ".hidden/x.md": b"h", "memory/big.md": b"x" * 600_000,
                      "x.sqlite": b"db", ".dotfile.md": b"d"})
    outside = tmp_path / "outside.md"
    outside.write_text("secret outside")
    os.symlink(outside, base / "memory" / "link.md")
    os.symlink(tmp_path, base / "linkdir")
    seen = []
    scan = DirectorySource(base).read(lambda rel: (seen.append(rel), sel.path_reason(rel, Rules()))[1])
    assert scan.complete
    assert sorted(scan.files) == ["MEMORY.md", "memory/a.md"]
    assert scan.rejected["memory/link.md"] == sel.SYMLINK and scan.rejected["linkdir"] == sel.SYMLINK
    assert scan.rejected["memory/big.md"] == sel.TOO_LARGE
    assert scan.rejected[".hidden/"] == sel.HIDDEN
    assert "outside.md" not in scan.files and b"secret outside" not in b"".join(scan.files.values())
    assert ".hidden/x.md" not in seen


def test_tar_source_refuses_unsafe_and_linked_members():
    import io, tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in {"ws/MEMORY.md": b"ok", "../evil.md": b"x", "/abs.md": b"y"}.items():
            info = tarfile.TarInfo(name); info.size = len(data); tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("ws/memory-link.md"); link.type = tarfile.SYMTYPE; link.linkname = "/etc/hosts"
        tar.addfile(link)
        junk = tarfile.TarInfo("ws/auth.json"); junk.size = 2; tar.addfile(junk, io.BytesIO(b"{}"))
    scan = TarSource(lambda: buf.getvalue()).read(lambda rel: sel.path_reason(rel, Rules()))
    assert scan.complete and sorted(scan.files) == ["MEMORY.md"]
    reasons = sorted(scan.rejected.values())
    assert reasons == sorted([sel.UNSAFE_PATH, sel.UNSAFE_PATH, sel.SYMLINK, sel.DENIED_NAME])

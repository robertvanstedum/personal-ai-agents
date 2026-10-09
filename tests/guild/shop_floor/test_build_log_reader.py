"""Guild 1.1 slice 2 (spec §4.1-4.3): the reader's new fields, and the spec
author, which is read from the spec's own author line at read time, blank
when there is none, never inferred, and never looked up in _working."""
from __future__ import annotations

import os

import pytest

from minimoi_portal.guild_ui.adapters import spec_author as sa
from minimoi_portal.guild_ui.adapters.queue_reader import normalize


@pytest.fixture
def docs(tmp_path, monkeypatch):
    root = tmp_path / "repo" / "docs"
    for sub in ("specs", "design"):
        (root / sub).mkdir(parents=True)
    (tmp_path / "repo" / "_working").mkdir()
    monkeypatch.setattr(sa, "DOCS_ROOT", root)
    sa._cache.clear()
    return root


def test_the_author_line_is_read_from_the_first_30_lines(docs):
    (docs / "specs" / "a.md").write_text("# Spec A\n\n**Author:** Claude Code\n")
    (docs / "specs" / "b.md").write_text("# B\nDrafted for Robert. **Prepared by:** Codex. Reviewed later.\n")
    (docs / "specs" / "late.md").write_text("# Late\n" + "line\n" * 30 + "**Author:** Too late\n")
    (docs / "specs" / "none.md").write_text("# No author here\n**Status:** draft\n")
    assert sa.spec_author("a.md") == "Claude Code"
    assert sa.spec_author("b.md") == "Codex"
    assert sa.spec_author("late.md") == ""
    assert sa.spec_author("none.md") == ""


def test_lookup_order_is_specs_then_design_then_docs_and_never_working(docs):
    (docs / "design" / "d.md").write_text("**Author:** Design author\n")
    (docs / "top.md").write_text("**Author:** Docs author\n")
    (docs / "specs" / "both.md").write_text("**Author:** From specs\n")
    (docs / "design" / "both.md").write_text("**Author:** From design\n")
    (docs.parent / "_working" / "w.md").write_text("**Author:** Working\n")
    assert sa.spec_author("d.md") == "Design author"
    assert sa.spec_author("top.md") == "Docs author"
    assert sa.spec_author("both.md") == "From specs"
    assert sa.spec_author("w.md") == ""


@pytest.mark.parametrize("name", [None, "", "missing.md", "../_working/w.md", "specs/a.md", "..", 12])
def test_anything_but_a_resolvable_basename_is_blank(docs, name):
    (docs / "specs" / "a.md").write_text("**Author:** A\n")
    (docs.parent / "_working" / "w.md").write_text("**Author:** Working\n")
    assert sa.spec_author(name) == ""


def test_the_cache_follows_the_files_mtime(docs):
    path = docs / "specs" / "c.md"
    path.write_text("**Author:** First\n")
    assert sa.spec_author("c.md") == "First"
    path.write_text("**Author:** Second\n")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert sa.spec_author("c.md") == "Second"


def test_normalize_carries_the_new_fields(docs):
    (docs / "specs" / "s.md").write_text("**Author:** Codex\n")
    row = normalize({"id": 9, "spec_title": "T", "status": "rework", "spec_file": "s.md", "priority": "high",
                     "notes": "next: verify", "owner_rank": 2, "trouble_reason": "fix it",
                     "trouble_from": "in_build", "trouble_since": "2026-09-30T10:00:00+00:00"})
    assert row["status_known"] and row["status"] == "rework" and row["trouble"] is True
    assert (row["priority"], row["notes"], row["owner_rank"], row["spec_author"]) == ("high", "next: verify", 2, "Codex")
    assert (row["trouble_reason"], row["trouble_from"]) == ("fix it", "in_build")
    legacy = normalize({"id": 10, "spec_title": "L", "status": "blocked", "blocked_reason": "old"})
    assert legacy["trouble"] and legacy["trouble_reason"] == "old" and legacy["spec_author"] == ""
    plain = normalize({"id": 11, "spec_title": "P", "status": "design", "blocked_reason": "stale legacy text"})
    assert plain["trouble"] is False and plain["trouble_reason"] == ""


@pytest.mark.parametrize("bad", [0, 9007199254740992, -1, "1", True, 1.5])
def test_a_bad_owner_rank_makes_only_that_row_unknown(bad):
    row = normalize({"id": 12, "spec_title": "R", "status": "design", "owner_rank": bad})
    assert row["status_known"] is False and "owner_rank" in row["field_problems"] and row["owner_rank"] is None


def test_an_overlong_or_unreadable_spec_file_leaves_the_author_blank(tmp_path):
    """#286 review: is_file() can raise (name too long, unreadable dir); never a 500."""
    from minimoi_portal.guild_ui.adapters import spec_author
    assert spec_author.resolve("x" * 300 + ".md", docs_root=tmp_path) is None

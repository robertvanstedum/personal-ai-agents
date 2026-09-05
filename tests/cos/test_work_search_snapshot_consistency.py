"""A search result describes one snapshot of one file.

A search used to touch a candidate twice: once to take its text, and again —
or not at all, in the approved projection — to settle its digest. Between
those two moments a file can change. The excerpt then came from one version
and the reported hash from another, and in the approved projection changed,
no-longer-approved text could come back carrying the old approved digest and
its disposition. The byte budget had the same seam: it was charged with a size
observed before the read, so a file that grew in the window could be examined
beyond the allowance.

So each candidate is now read exactly once, through the confined primitive,
under the remaining byte allowance — and those bytes are what get scored,
decoded, hashed, checked against the disposition pin, and charged. These tests
force a change into the window deliberately, by wrapping the read primitive
itself, and hold the invariants that follow.

All fixture content is synthetic.
"""

from __future__ import annotations

import hashlib
from importlib import import_module
from pathlib import Path

import pytest

from domains.cos.work import retrieval
from domains.cos.work.confine import ConfinedFile, read_bytes, read_bytes_capped
from domains.cos.work.envelope import SEARCH_TRUNCATED
from domains.cos.work.records import StaleContext
from domains.cos.work.retrieval import Accumulation, approved_root_ref
from domains.cos.work.roots import ENV_SOURCE_ROOTS, ENV_WORK_ROOT, load_root_configuration

#: ``domains.cos.work`` re-exports the ``confine()`` function under that name,
#: so the module itself is fetched explicitly rather than by attribute.
confine_module = import_module("domains.cos.work.confine")

APPROVED = approved_root_ref("career")

REPLACEMENT = "reconciliation appears in the replacement text\n"


def digest(data: bytes | str) -> str:
    """The digest of exactly these bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def swap_on_read(monkeypatch):
    """Change a file in the window between enumeration and the confined read.

    The wrapper stands where the race is: it runs *after* the walk has named
    the candidate (and, for the approved projection, after its pin has been
    verified) and *before* a single byte is read. Whatever the read then
    returns is the only version this search ever saw.
    """
    calls: list[dict] = []

    def install(target: Path, replacement: str) -> list[dict]:
        state = {"swapped": False}

        def wrapper(confined: ConfinedFile, *, cap: int, **kwargs):
            if not state["swapped"] and Path(confined.root) / confined.relative_path == target:
                state["swapped"] = True
                target.write_text(replacement)
            result = read_bytes_capped(confined, cap=cap, **kwargs)
            calls.append(
                {
                    "relative_path": confined.relative_path,
                    "cap": cap,
                    "returned": None if result is None else len(result),
                }
            )
            return result

        monkeypatch.setattr(retrieval, "read_bytes_capped", wrapper)
        return calls

    return install


@pytest.fixture
def record_budgets(monkeypatch):
    """Capture every traversal budget a search creates."""
    budgets: list[retrieval._Budget] = []
    original = retrieval._Budget

    class Recording(original):
        def __init__(self, max_files: int, max_bytes: int) -> None:
            super().__init__(max_files, max_bytes)
            budgets.append(self)

    monkeypatch.setattr(retrieval, "_Budget", Recording)
    return budgets


# -- a configured source root ------------------------------------------------


def test_configured_hit_excerpt_and_hash_describe_the_same_bytes(
    career_accumulation: Accumulation, career_sources: Path, swap_on_read
):
    """a file changed inside the window yields one consistent snapshot"""
    target = career_sources / "resumes" / "current-resume.md"
    original = target.read_text()
    swap_on_read(target, REPLACEMENT)

    outcome = career_accumulation.search_sources("career", ["resumes"], "reconciliation")

    assert outcome.hits
    for hit in outcome.hits:
        assert hit.sha256 == digest(REPLACEMENT)
        assert hit.sha256 != digest(original)
        assert hit.excerpt in REPLACEMENT
        assert hit.excerpt not in original


def test_no_hit_reports_a_digest_its_excerpt_does_not_belong_to(
    career_accumulation: Accumulation, career_sources: Path, swap_on_read
):
    """the invariant, stated over the whole root rather than one file"""
    target = career_sources / "other-responses" / "answer-01.md"
    swap_on_read(target, REPLACEMENT)

    outcome = career_accumulation.search_sources("career", None, "reconciliation")

    assert outcome.hits
    for hit in outcome.hits:
        if hit.root_ref == APPROVED:
            base = career_accumulation._approved_base("career")
        else:
            base = career_accumulation.configuration.resolve("career", hit.root_ref).path
        # The scored snapshot is the only version the search saw. Either the
        # file still holds it, or it was the version the swap wrote — never a
        # digest belonging to bytes nobody read.
        on_disk = (Path(base) / hit.relative_path).read_bytes()
        assert hit.sha256 in {digest(on_disk), digest(REPLACEMENT)}
        assert hit.excerpt in on_disk.decode("utf-8") or hit.excerpt in REPLACEMENT


# -- the approved projection -------------------------------------------------


def approved_artifact_path(work_root: Path) -> Path:
    """The one disposition-pinned artifact in the synthetic work tree."""
    return next(
        (work_root / "subjects" / "career" / "work").glob(
            "approved-coauthored*/artifacts/0002-letter.md"
        )
    )


def test_approved_bytes_changed_inside_the_window_never_surface(
    career_accumulation: Accumulation, private_work_root: Path, swap_on_read
):
    """changed approved material is reported, not returned under its old pin"""
    target = approved_artifact_path(private_work_root)
    pinned = digest(target.read_bytes())
    swap_on_read(target, REPLACEMENT)

    outcome = career_accumulation.search_sources("career", [APPROVED], "reconciliation")

    changed = str(target.relative_to(career_accumulation._approved_base("career")))
    assert not any(hit.relative_path == changed for hit in outcome.hits)
    assert not any("replacement" in hit.excerpt.casefold() for hit in outcome.hits)
    assert not any(hit.sha256 == pinned for hit in outcome.hits)

    stale = [issue for issue in outcome.issues if issue.code == "stale_context"]
    assert len(stale) == 1
    assert stale[0].root_ref == APPROVED
    assert stale[0].relative_path.endswith("artifacts/0002-letter.md")
    assert "replacement" not in stale[0].message.casefold()


def test_approved_search_hit_hashes_the_bytes_it_scored(
    career_accumulation: Accumulation, private_work_root: Path
):
    """with nothing changed, the reported digest is still of the scored bytes"""
    target = approved_artifact_path(private_work_root)
    outcome = career_accumulation.search_sources("career", [APPROVED], "reconciliation")

    approved_hits = [hit for hit in outcome.hits if hit.root_ref == APPROVED]
    assert approved_hits
    base = career_accumulation._approved_base("career")
    for hit in approved_hits:
        on_disk = (base / hit.relative_path).read_bytes()
        assert hit.sha256 == digest(on_disk)
        assert hit.excerpt in on_disk.decode("utf-8")
    wanted = str(target.relative_to(base))
    assert any(
        hit.relative_path == wanted and hit.sha256 == digest(target.read_bytes())
        for hit in approved_hits
    )


def test_approved_read_hashes_the_bytes_it_returns(
    career_accumulation: Accumulation, private_work_root: Path, monkeypatch
):
    """a direct approved read compares its own bytes with the pin"""
    target = approved_artifact_path(private_work_root)
    relative = str(target.relative_to(career_accumulation._approved_base("career")))

    outcome = career_accumulation.read_source("career", APPROVED, relative)
    assert outcome.sha256 == digest(outcome.content)
    assert outcome.bytes == len(target.read_bytes())

    # Change the bytes in the window after the projection is derived and
    # before they are read: the read must refuse rather than return them.
    original_read = read_bytes
    state = {"swapped": False}

    def wrapper(confined: ConfinedFile, **kwargs):
        if not state["swapped"] and confined.relative_path == relative:
            state["swapped"] = True
            target.write_text(REPLACEMENT)
        return original_read(confined, **kwargs)

    monkeypatch.setattr(retrieval, "read_bytes", wrapper)
    with pytest.raises(StaleContext) as excinfo:
        career_accumulation.read_source("career", APPROVED, relative)
    assert excinfo.value.code == "stale_context"
    assert "replacement" not in excinfo.value.message.casefold()


# -- the byte allowance ------------------------------------------------------


def test_a_file_that_grows_cannot_overrun_the_remaining_allowance(
    workspace: Path, private_work_root: Path, swap_on_read, record_budgets
):
    """growth inside the window spends the allowance; it does not exceed it"""
    directory = workspace / "growing"
    directory.mkdir(mode=0o700)
    small = directory / "a-small.md"
    small.write_text("reconciliation is mentioned here\n")
    grows = directory / "b-grows.md"
    grows.write_text("reconciliation once\n")
    for path in (small, grows):
        path.chmod(0o600)

    accumulation = Accumulation(
        load_root_configuration(
            {
                ENV_WORK_ROOT: str(private_work_root),
                ENV_SOURCE_ROOTS: (
                    '{"career": {"growing": {"path": "%s", '
                    '"context_class": "robert_source"}}}' % directory
                ),
            }
        )
    )

    allowance = len(small.read_bytes()) + 40
    calls = swap_on_read(grows, "reconciliation " * 400 + "\n")

    outcome = accumulation.search_sources(
        "career", ["growing"], "reconciliation", max_bytes_examined=allowance
    )

    assert grows.stat().st_size > allowance
    assert outcome.truncated is True
    assert [issue.code for issue in outcome.issues] == [SEARCH_TRUNCATED]

    # Nothing was read from the grown file, and no hit came out of it.
    assert calls[-1]["relative_path"] == "b-grows.md"
    assert calls[-1]["returned"] is None
    assert all(hit.relative_path == "a-small.md" for hit in outcome.hits)

    # Bytes charged are exactly the bytes read, and never more than allowed.
    read_total = sum(call["returned"] or 0 for call in calls)
    budget = record_budgets[0]
    assert budget.bytes == read_total
    assert budget.bytes <= allowance
    for call in calls:
        assert call["cap"] <= allowance
        assert call["returned"] is None or call["returned"] <= call["cap"]


def test_the_read_primitive_refuses_to_transfer_past_its_cap(
    workspace: Path, monkeypatch
):
    """the ceiling lives inside the read, not in the caller's arithmetic"""
    directory = workspace / "capped"
    directory.mkdir(mode=0o700)
    note = directory / "note.md"
    body = "x" * 5000 + "\n"
    note.write_text(body)
    note.chmod(0o600)
    confined = confine_module.confine(directory, "note.md")
    size = len(body.encode("utf-8"))

    transferred: list[int] = []
    real_os = confine_module.os

    class CountingOs:
        def __getattr__(self, name):
            return getattr(real_os, name)

        def read(self, handle, count):
            chunk = real_os.read(handle, count)
            transferred.append(len(chunk))
            return chunk

    monkeypatch.setattr(confine_module, "os", CountingOs())

    assert confine_module.read_bytes_capped(confined, cap=size) == body.encode("utf-8")
    assert sum(transferred) == size

    transferred.clear()
    assert confine_module.read_bytes_capped(confined, cap=size - 1) is None
    assert sum(transferred) <= size - 1

    transferred.clear()
    assert confine_module.read_bytes_capped(confined, cap=0) is None
    assert sum(transferred) == 0

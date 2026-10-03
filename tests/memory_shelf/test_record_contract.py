"""Shelf record contract (v0.5 B2; amendment v0.5.1 R2, R3). Pure logic, no model."""
import ast
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.memory_shelf import editions, events as ev, record, ulid, weight

NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=timezone.utc)
OWNER = ev.OwnerAuthority("moi-approve")
H = hashlib.sha256(b"original").hexdigest()


def meta(**kw):
    base = dict(kind="session", chair="Claude Code", source="claude-code:abc", source_hash=H,
                created="2026-10-03T17:00:00Z", title="Memory design")
    base.update(kw)
    return record.new_front_matter(**base)


def at(day, kind, **kw):
    return ev.make_event(kind, kw.pop("by", "Robert"), now=datetime(2026, 10, day, 12, tzinfo=timezone.utc), **kw)


# ── ULID and filenames ────────────────────────────────────────────────────────

def test_ulid_shape_order_and_suffix():
    a, b = ulid.new_ulid(1_000), ulid.new_ulid(2_000)
    assert ulid.is_ulid(a) and len(a) == 26 and a < b
    assert ulid.short(a) == a[-8:].lower()
    assert ulid.filename("The Memory Spec: v0.5!", a) == f"the-memory-spec-v0-5--{ulid.short(a)}.md"
    assert ulid.new_ulid(1_000) != a                         # randomness differs at the same millisecond
    with pytest.raises(ValueError):
        ulid.short("not-a-ulid")


# ── events: closed vocabulary, authority ──────────────────────────────────────

def test_the_vocabulary_is_closed():
    with pytest.raises(ev.EventRefused):
        ev.make_event("endorsed", "Robert", now=NOW)
    with pytest.raises(ev.EventRefused):
        ev.make_event("proposed", "Codex", now=NOW, mood="happy")


@pytest.mark.parametrize("kind", ["approved-direct", "approved-under-mandate"])
def test_no_text_or_agent_can_create_an_approval(kind):
    for by in ("Robert", "Codex", "CoS"):
        with pytest.raises(ev.EventRefused):
            ev.make_event(kind, by, now=NOW)                  # no authority token
    with pytest.raises(ev.EventRefused):
        ev.make_event("approved-direct", "Codex", now=NOW, authority=OWNER)   # authority is the owner's, not Codex's
    with pytest.raises(ev.EventRefused):
        ev.OwnerAuthority("a-transcript-said-so")             # not an owner entry point


def test_an_owner_approval_records_how_it_happened():
    e = ev.make_event("approved-direct", "robert", now=NOW, authority=OWNER)
    assert e["via"] == "moi-approve" and e["kind"] == "approved-direct"
    mandate = ulid.new_ulid(1)
    m = ev.make_event("approved-under-mandate", "robert", now=NOW, authority=OWNER, mandate=mandate)
    assert m["via"] == f"mandate:{mandate}"
    with pytest.raises(ev.EventRefused):
        ev.make_event("approved-under-mandate", "robert", now=NOW, authority=OWNER)   # names no mandate


def test_parsers_never_touch_approval_authority():
    """The shelf code that reads text never imports OwnerAuthority or builds approvals."""
    src = Path(record.__file__).parent
    for name in ("editions.py", "ulid.py", "weight.py"):
        tree = ast.parse((src / name).read_text())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert "OwnerAuthority" not in names and "make_event" not in names


# ── weight is derived, not stored ─────────────────────────────────────────────

def test_no_event_is_deliberation_and_says_so():
    info = weight.derive(meta())
    assert info["weight"] == "deliberation" and "deliberation (not reviewed)" in info["line"]


def test_approved_outranks_proposed_and_verified_adds_evidence_not_approval():
    m = meta()
    m["events"] = [at(5, "proposed", by="Claude Code")]
    assert weight.derive(m)["weight"] == "proposed"
    m["events"].append(at(6, "verified"))
    info = weight.derive(m)
    assert info["weight"] == "proposed" and info["evidence"] == ["10-06"]   # verified never approves
    m["events"].append(ev.make_event("approved-direct", "robert", now=datetime(2026, 10, 7, tzinfo=timezone.utc), authority=OWNER))
    info = weight.derive(m)
    assert info["weight"] == "approved" and "approved by Robert (direct) 2026-10-07" in info["line"]
    assert "verified 10-06" in info["line"]


def test_a_mandate_counts_only_while_its_own_record_is_approved():
    mandate = ulid.new_ulid(5)
    m = meta()
    m["events"] = [ev.make_event("approved-under-mandate", "robert", now=NOW, authority=OWNER, mandate=mandate)]
    assert weight.derive(m, lambda i: "approved")["weight"] == "approved"
    withdrawn = weight.derive(m, lambda i: "withdrawn")
    assert withdrawn["weight"] == "proposed" and "mandate is not in force" in withdrawn["line"]
    assert weight.derive(m)["weight"] == "proposed"           # unknown mandate: not in force


def test_rejected_and_superseded_stay_visible_under_their_successor():
    old, new, other = ulid.new_ulid(1), ulid.new_ulid(2), ulid.new_ulid(3)
    mo, mn = meta(), meta()
    mo["events"] = [at(5, "superseded", successor=new)]
    mn["events"] = [ev.make_event("approved-direct", "robert", now=NOW, authority=OWNER)]
    mother = meta(); mother["events"] = [at(5, "proposed", by="Codex")]
    items = [(old, weight.derive(mo)), (other, weight.derive(mother)), (new, weight.derive(mn))]
    order = [rid for rid, _ in weight.order(items)]
    assert order == [new, old, other]                          # old sits right under its successor
    assert sorted(order) == sorted([old, new, other])          # nothing dropped
    rejected = meta(); rejected["events"] = [at(5, "rejected")]
    assert weight.derive(rejected)["weight"] == "rejected"


def test_a_withdrawn_approval_stops_counting():
    m = meta()
    m["events"] = [ev.make_event("approved-direct", "robert", now=NOW, authority=OWNER), at(8, "withdrawn")]
    assert weight.derive(m)["weight"] == "withdrawn"


# ── records: contract, round trip, append-only ────────────────────────────────

def test_round_trip_is_stable_and_validated(tmp_path):
    m = meta(tags=["memory", "design"])
    path = tmp_path / ulid.filename("Memory design", m["id"])
    record.write(path, m, "Body text.")
    loaded, body = record.load(path.read_text())
    assert loaded == m and body.strip() == "Body text."
    first = path.read_bytes()
    record.write(path, loaded, body)
    assert path.read_bytes() == first                          # same record, same bytes


@pytest.mark.parametrize("change", [
    {"chair": "claude-opus-5-5"}, {"tier": "secret"}, {"scope": "guest"}, {"source_hash": "abc"},
    {"id": "nope"}, {"created": "yesterday"},
])
def test_the_contract_refuses_bad_records(change):
    m = meta()
    m.update(change)
    with pytest.raises(record.InvalidRecord):
        record.check(m)


def test_append_event_only_adds(tmp_path):
    m = meta()
    path = tmp_path / "r.md"
    record.write(path, m, "x")
    record.append_event(path, at(5, "proposed", by="Claude Code"))
    record.append_event(path, at(6, "verified"))
    loaded, _ = record.load(path.read_text())
    assert [e["kind"] for e in loaded["events"]] == ["proposed", "verified"]
    with pytest.raises(record.InvalidRecord):
        bad = dict(loaded); bad["events"] = [loaded["events"][1], loaded["events"][0]]   # out of order
        record.check(bad)


# ── editions: immutable, idempotent, no silent merge ──────────────────────────

def test_editions_are_never_overwritten_and_repeats_are_a_noop(tmp_path):
    first = editions.add_edition(tmp_path, b"pasted thread", "txt")
    again = editions.add_edition(tmp_path, b"pasted thread", "txt")
    assert first.added and not again.added and first.path == again.path and first.number == again.number == 1
    export = editions.add_edition(tmp_path, b"fuller export", "json")
    assert export.added and export.number == 2
    assert first.path.read_bytes() == b"pasted thread"          # the original is still there, untouched
    assert len(editions.list_editions(tmp_path)) == 2


def test_same_source_needs_an_exact_conversation_id_never_title_or_overlap():
    a = {"provider": "claude-ai", "source_id": "conv-1", "title": "Memory", "text": "same words"}
    assert editions.same_source(a, {**a})
    assert not editions.same_source(a, {**a, "source_id": "conv-2"})          # same title and text, different id
    assert not editions.same_source({**a, "source_id": None}, {**a, "source_id": None})   # paste: possible match only
    assert not editions.same_source(a, {**a, "provider": "codex"})

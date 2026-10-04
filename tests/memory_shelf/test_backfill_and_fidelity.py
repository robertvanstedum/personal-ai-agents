"""Backfill dry-run listing (B4) and the fidelity sample F2 (R4). Synthetic trees only."""
import json
import zipfile
from datetime import datetime, timezone

import pytest

from core.memory_shelf import approvals, backfill, canary, codes, events as ev, fidelity, inbox, ledger, record, render, watchers
from core.memory_shelf.config import Config, SourceCfg

from .helpers import FAKE_KEY, cc_lines, codex_lines, make_shelf, write_tree, process
from .test_inbox import conv, make_zip

OWNER = ev.OwnerAuthority("moi-approve")
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


# ── backfill ──────────────────────────────────────────────────────────────────

def repo(tmp_path):
    root = tmp_path / "repo"
    write_tree(root, {
        "docs/DECISIONS.md": "# Decisions\n- a decision\n",
        "docs/decision-records/2026-09-01-one.md": "# One\nbody\n",
        "docs/decision-records/two.private.md": "# Two\nsecret\n",
        "docs/decision-records/three.md": "[private]\n# Three\n",
        "data/cos_memory.md": "note: remember this\n",
        "data/curator_archive/curator_2026-09-01.json": "{\"a\": 1}",
        "data/curator_archive/curator_2026-09-02.json": "{\"a\": 2}",
        "data/curator_archive/readme.txt": "undated file",
    })
    return root


def test_backfill_listing_is_metadata_only_with_hashes(tmp_path):
    shelf, root = make_shelf(tmp_path), repo(tmp_path)
    listing = backfill.dry_run(shelf, "backfill-guild", root, now=NOW)
    paths = {f["path"]: f for f in listing["files"]}
    assert paths["docs/DECISIONS.md"]["decision"] == "would-import" and len(paths["docs/DECISIONS.md"]["sha256"]) == 64
    assert paths["docs/decision-records/two.private.md"] == {"path": "docs/decision-records/two.private.md", "decision": "skip",
                                                             "reason": "private", "bytes": 13, "date": paths["docs/decision-records/two.private.md"]["date"]}
    assert paths["docs/decision-records/three.md"]["reason"] == "private" and "sha256" not in paths["docs/decision-records/three.md"]
    assert listing["counts"]["skipped"] == {"private": 2} and listing["counts"]["would_import"] == 3
    blob = json.dumps(listing)
    for text in ("a decision", "remember this", "secret", "body"):
        assert text not in blob
    assert shelf.list_records() == [] and shelf.pending() == []                 # nothing imported


def test_curator_listing_uses_filename_dates_and_skips_undated(tmp_path):
    shelf, root = make_shelf(tmp_path), repo(tmp_path)
    listing = backfill.dry_run(shelf, "backfill-curator", root, never_copy=("curator_2026-09-02.json",), now=NOW)
    rows = {f["path"].split("/")[-1]: f for f in listing["files"]}
    assert rows["curator_2026-09-01.json"]["date"] == "2026-09-01" and rows["curator_2026-09-01.json"]["decision"] == "would-import"
    assert rows["curator_2026-09-02.json"]["reason"] == "never_copy" and rows["readme.txt"]["reason"] == "undated"


def test_backfill_never_imports_even_when_approved(tmp_path):
    shelf, root = make_shelf(tmp_path), repo(tmp_path)
    assert backfill.import_status(shelf, "backfill-guild", root) == approvals.NO_DRY_RUN
    backfill.dry_run(shelf, "backfill-guild", root, now=NOW)
    assert backfill.import_status(shelf, "backfill-guild", root) == codes.NOT_APPROVED
    approvals.approve_source(shelf, "backfill-guild", backfill.fingerprint("backfill-guild", root, ()), OWNER)
    assert backfill.import_status(shelf, "backfill-guild", root) == backfill.NOT_BUILT
    assert backfill.import_status(shelf, "backfill-guild", root, never_copy=("x",)) == approvals.STALE
    assert shelf.list_records() == []


# ── fidelity ──────────────────────────────────────────────────────────────────

def world(tmp_path):
    """A shelf filled through the real pipelines: a Claude Code session, a Codex session, an export, a paste, a canary."""
    shelf = make_shelf(tmp_path)
    cc_root, cx_root, box = tmp_path / "cc", tmp_path / "cx", tmp_path / "inbox"
    write_tree(cc_root, {"p/s-1.jsonl": cc_lines("s-1", ("human", "one"), ("assistant", f"two {FAKE_KEY}"), ("human", "three"),
                                                    ("assistant", "four"))})
    write_tree(cx_root, {"2026/10/03/rollout-x-0199aaaa-bbbb-cccc-dddd-eeeeffff0001.jsonl":
                         codex_lines("0199aaaa-bbbb-cccc-dddd-eeeeffff0001", ("human", "cq"), ("assistant", "ca"))})
    cfg = Config(shelf_root=shelf.root, inbox_root=box, sources={"claude-code": SourceCfg("claude-code", "claude-code", cc_root),
                                                                 "codex": SourceCfg("codex", "codex", cx_root)})
    for src in cfg.sources.values():
        watchers.dry_run(shelf, src, NOW)
        approvals.approve_source(shelf, src.name, src.fingerprint(), OWNER)
        watchers.run_source(shelf, src, now=NOW)
    make_zip(box, "e.zip", [conv("u-1", "Chat", [("human", "q"), ("assistant", "a"), ("human", "q2"), ("assistant", "a2")])])
    (box / "p.txt").write_text("Human: pasted q\nClaude: pasted a\nHuman: more\nClaude: more a")
    canary.emit(box, NOW)
    process(shelf, box, now=NOW)
    return shelf, cfg


def main_for(shelf, key):
    return shelf.main_path(shelf.index()[key])


def test_clean_pipeline_passes_for_every_source_and_the_canary(tmp_path):
    shelf, cfg = world(tmp_path)
    out = fidelity.sample(shelf, cfg, n=10, seed=1)
    assert out["checked"] == 4 and out["ok"] == 4 and out["failed"] == 0 and out["skipped"] == {}
    assert fidelity.sample(shelf, cfg, canary=True)["ok"] == 1
    assert len(shelf.list_records()) == 4                                          # the canary is not in the ordinary sample


def rewrite_turns(main, transform):
    meta, body = record.load(main.read_text())
    turns = render.parse_body(body)
    transform(turns)
    from core.memory_shelf.sessions import Parsed
    p = Parsed("claude-code", "Claude Code", turns=turns)
    record.write(main, meta, render.render_body(p))


def kinds(result):
    return [(f["kind"], f["position"]) for f in result["findings"]]


def test_dropped_turn_is_found_by_position(tmp_path):
    shelf, cfg = world(tmp_path)
    main = main_for(shelf, "claude-code:s-1")
    rewrite_turns(main, lambda t: t.pop(2))
    r = fidelity.check_record(cfg, main)
    assert r["status"] == "failed" and ("dropped", 3) in kinds(r)


def test_duplicated_turn_is_found(tmp_path):
    shelf, cfg = world(tmp_path)
    main = main_for(shelf, "claude-code:s-1")
    rewrite_turns(main, lambda t: t.insert(2, t[1]))
    r = fidelity.check_record(cfg, main)
    assert r["status"] == "failed" and any(k == "duplicated" for k, _ in kinds(r))


def test_reordered_turns_are_found(tmp_path):
    shelf, cfg = world(tmp_path)
    main = main_for(shelf, "claude-code:s-1")
    from dataclasses import replace

    def swap(t):
        a, b = t[0], t[2]
        t[0], t[2] = replace(b, ordinal=a.ordinal), replace(a, ordinal=b.ordinal)
    rewrite_turns(main, swap)
    r = fidelity.check_record(cfg, main)
    assert r["status"] == "failed" and {k for k, _ in kinds(r) if not k.startswith("edition")} == {"reordered"}
    assert [p for k, p in kinds(r) if k == "reordered"] == [1, 3]


def test_misattributed_turn_is_found(tmp_path):
    shelf, cfg = world(tmp_path)
    main = main_for(shelf, "claude-code:s-1")
    from dataclasses import replace
    rewrite_turns(main, lambda t: t.__setitem__(1, replace(t[1], speaker="human")))
    r = fidelity.check_record(cfg, main)
    assert r["status"] == "failed" and ("misattributed", 2) in kinds(r)


def test_altered_text_is_unexpected_plus_dropped(tmp_path):
    shelf, cfg = world(tmp_path)
    main = main_for(shelf, "claude-code:s-1")
    from dataclasses import replace
    rewrite_turns(main, lambda t: t.__setitem__(3, replace(t[3], text="a different fourth turn")))
    r = fidelity.check_record(cfg, main)
    assert {"dropped", "unexpected"} <= {k for k, _ in kinds(r)}


def test_diffs_show_positions_and_hashes_never_text(tmp_path):
    shelf, cfg = world(tmp_path)
    main = main_for(shelf, "claude-code:s-1")
    from dataclasses import replace
    rewrite_turns(main, lambda t: t.__setitem__(3, replace(t[3], text="LEAKY-REPLACEMENT-TEXT")))
    blob = json.dumps(fidelity.check_record(cfg, main))
    for leak in ("LEAKY-REPLACEMENT-TEXT", "four", "one", FAKE_KEY, "credential removed"):
        assert leak not in blob
    assert all(len(f["expected"] or "x" * 12) == 12 for f in json.loads(blob)["findings"])


def test_scrubbed_text_is_not_reported_as_damage(tmp_path):
    shelf, cfg = world(tmp_path)
    main = main_for(shelf, "claude-code:s-1")
    assert FAKE_KEY not in main.read_text() and fidelity.check_record(cfg, main)["status"] == "ok"


def test_tampered_edition_file_is_an_edition_mismatch(tmp_path):
    shelf, cfg = world(tmp_path)
    main = main_for(shelf, "claude-code:s-1")
    ed = next((main.parent / "editions").iterdir())
    lines = ed.read_text().splitlines()
    ed.write_text("\n".join(lines[:-1]) + "\n")                                    # silently drop the last turn from the edition
    r = fidelity.check_record(cfg, main)
    assert r["status"] == "failed" and any(f["kind"].startswith("edition_mismatch") for f in r["findings"])


def test_corrupting_each_other_source_kind_is_caught(tmp_path):
    shelf, cfg = world(tmp_path)
    for key in ("codex:0199aaaa-bbbb-cccc-dddd-eeeeffff0001", "claude-ai:u-1", "canary:canary-2026-10-03"):
        main = main_for(shelf, key)
        assert fidelity.check_record(cfg, main)["status"] == "ok"
        rewrite_turns(main, lambda t: t.pop(0))
        assert fidelity.check_record(cfg, main)["status"] == "failed"
    paste = next(e for k, e in shelf.index().items() if k.startswith("paste:"))
    main = shelf.main_path(paste)
    rewrite_turns(main, lambda t: t.pop(1))
    assert fidelity.check_record(cfg, main)["status"] == "failed"


def test_a_source_that_grew_or_vanished_is_skipped_not_failed(tmp_path):
    shelf, cfg = world(tmp_path)
    src = cfg.sources["claude-code"].root / "p/s-1.jsonl"
    src.write_text(src.read_text() + cc_lines("s-1", ("human", "five")))
    r = fidelity.check_record(cfg, main_for(shelf, "claude-code:s-1"))
    assert r == {"record": r["record"], "status": "skipped", "reason": fidelity.SOURCE_CHANGED}
    src.unlink()
    assert fidelity.check_record(cfg, main_for(shelf, "claude-code:s-1"))["reason"] == fidelity.SOURCE_MISSING


def test_diff_unit_cases():
    e = [(1, "human", "a" * 64), (2, "assistant", "b" * 64), (3, "human", "c" * 64)]
    assert fidelity.diff(e, list(e)) == []
    assert [f["kind"] for f in fidelity.diff(e, e[:2])] == ["dropped"]
    assert [f["kind"] for f in fidelity.diff(e, e + [(4, "human", "c" * 64)])] == ["duplicated"]
    assert [f["kind"] for f in fidelity.diff(e, e + [(4, "human", "d" * 64)])] == ["unexpected"]
    assert [f["kind"] for f in fidelity.diff(e, [(1, "assistant", "a" * 64), *e[1:]])] == ["misattributed"]
    assert {f["kind"] for f in fidelity.diff(e, [(1, "human", "c" * 64), e[1], (3, "human", "a" * 64)])} == {"reordered"}
    assert [f["kind"] for f in fidelity.diff(e, [(2, *e[0][1:]), (3, *e[1][1:]), (4, *e[2][1:])])] == ["ordinals"]

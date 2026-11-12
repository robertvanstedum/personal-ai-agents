"""The ``cos`` watcher kind (#509): production CoS export files captured through the standard approval-gated watcher, read by exactly the
Dev feed's rules (``core/memory_shelf/cos.py``). Invented words only."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.memory_shelf import approvals, codes, cos, watchers
from core.memory_shelf import config as shelf_config
from core.memory_shelf import report
from core.memory_shelf import cli as shelf_cli
from core.memory_shelf.config import SourceCfg
from core.memory_sync import cos_batch as CB
from tests.memory_shelf.helpers import make_shelf
from tests.memory_shelf.test_mc_capture import OWNER
from tests.vault.cos_history import History, name

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


def place(watch: Path, h: History, cid: str, meta_change=None, environment="prod") -> Path:
    meta = json.loads(h.meta_path(cid).read_text())
    meta.update(meta_change or {})
    raw = h.turns_path(cid).read_bytes() if h.turns_path(cid).exists() else b""
    path = watch / name(environment, cid)
    path.write_bytes(CB.export_doc(environment, cid, json.dumps(meta, sort_keys=True).encode(), raw))
    return path


@pytest.fixture
def env(tmp_path):
    h = History(tmp_path / "history")
    watch = tmp_path / "cos-export"
    watch.mkdir()
    shelf = make_shelf(tmp_path)
    cfg = SourceCfg("minimoi-cos", "cos", watch, ())
    return h, watch, shelf, cfg


def approve(shelf, cfg):
    first = watchers.run_source(shelf, cfg, now=NOW)
    assert first["status"] in ("dry_run_only", codes.NOT_APPROVED)
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)


def test_the_kind_is_known_everywhere_the_mc_kind_is():
    assert "cos" in shelf_config.SOURCE_KINDS and watchers.normalizer_for("cos") == 1
    assert watchers.KIND_PROVIDER["cos"] == "minimoi-cos" and watchers.PARSERS["cos"] is cos.parse
    assert report.LABELS["minimoi-cos"] == "Chief of Staff" and "minimoi-cos" in shelf_cli.LIST_SOURCES
    parsed = shelf_config.parse({"schema_version": 1, "sources": {"minimoi-cos": {"kind": "cos", "root": "/tmp/x"}}})
    assert parsed.sources["minimoi-cos"].kind == "cos"


def test_only_top_level_export_names_are_discovered(env):
    h, watch, shelf, cfg = env
    cid = h.conversation(("KILN-SECRET when?", "KILN-SECRET Tuesday"))
    place(watch, h, cid)
    (watch / "notes.json").write_text("{}")
    (watch / "sub").mkdir()
    (watch / "sub" / name("prod", cid)).write_bytes(b"{}")
    (watch / f".{name('prod', cid)}.tmp-1-0a1b2c3d").write_bytes(b"{}")
    assert [c.rel for c in watchers.discover(cfg)] == [name("prod", cid)]


def test_capture_is_approval_gated_dedups_by_conversation_and_a_new_turn_adds_an_edition(env):
    h, watch, shelf, cfg = env
    a = h.conversation(("KILN-SECRET when?", "KILN-SECRET Tuesday"))
    b = h.conversation(("GLAZE-SECRET which?", None))
    place(watch, h, a)
    place(watch, h, b)
    listing = watchers.dry_run(shelf, cfg, NOW)
    assert listing["counts"]["would_copy"] == 2 and "KILN-SECRET" not in json.dumps(listing)
    assert watchers.run_source(shelf, cfg, now=NOW)["status"] == codes.NOT_APPROVED
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)
    assert watchers.run_source(shelf, cfg, now=NOW) == {"status": "ok", "counts": {codes.CAPTURED: 2}}
    assert {f"minimoi-cos:prod:{a}", f"minimoi-cos:prod:{b}"} <= set(shelf.index())
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {"skipped_unchanged": 2}
    h.say(a, "KILN-SECRET and Thursday?", "KILN-SECRET yes")
    place(watch, h, a)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {codes.EDITION_ADDED: 1, "skipped_unchanged": 1}


@pytest.mark.parametrize("change,code", [({"private": True}, "private"), ({"private": None}, "private"),
                                         ({"environment": "dev"}, "scope"), ({"data_scope": "work"}, "scope")])
def test_a_file_that_should_never_have_travelled_is_refused_at_capture(env, change, code):
    h, watch, shelf, cfg = env
    cid = h.conversation(("PRIVATE-SECRET words", "PRIVATE-SECRET reply"))
    place(watch, h, cid, meta_change=change)
    approve(shelf, cfg)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"].get(codes.CAPTURED, 0) == 0 and f"minimoi-cos:prod:{cid}" not in shelf.index()
    assert not any("PRIVATE-SECRET" in p.read_text("utf-8", "replace") for p in shelf.root.rglob("*") if p.is_file())
    blob = (Path(shelf.ledger_dir) / "ledger.jsonl").read_text()
    assert f'"reason": "{code}"' in blob and "PRIVATE-SECRET" not in blob


def test_a_damaged_export_is_a_failed_file_with_a_fixed_code(env):
    h, watch, shelf, cfg = env
    (watch / name("prod", "cosc-0123456789abcdef01234567")).write_text('{"schema": "something-else"}\n')
    approve(shelf, cfg)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"].get(codes.FAILED) == 1
    assert '"reason": "bad_export"' in (Path(shelf.ledger_dir) / "ledger.jsonl").read_text()


def test_the_export_reads_exactly_like_the_dev_feed_reads_the_folder(env):
    h, watch, shelf, cfg = env
    cid = h.conversation(("KILN-SECRET when?", "KILN-SECRET Tuesday"), ("second", None))
    direct, _raw = cos.read(h.root, cid, environment="prod")
    from_file = cos.parse([place(watch, h, cid).read_bytes()])
    assert [(t.speaker, t.text) for t in direct.turns] == [(t.speaker, t.text) for t in from_file.turns]
    assert direct.source_id == from_file.source_id and direct.manifest == from_file.manifest

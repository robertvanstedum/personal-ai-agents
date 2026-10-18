"""MC conversation capture (watcher kind ``mc``, provider ``minimoi-mc``): the portal exporter and the shelf watcher.

Synthetic stores only: the conversation store and the floor store are the real classes on a tmp folder and a SQLite
floor database built from the portal's own migration. No real conversation is ever read.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.memory_index import indexer, policy, search
from core.memory_shelf import approvals, codes, config as shelf_config, events as ev, fidelity, ledger, mc, record, \
    render, report, sessions, watchers
from core.memory_shelf import cli as shelf_cli
from core.memory_shelf.config import SourceCfg
from minimoi_portal.guild_ui.conversations import ConversationStore
from minimoi_portal.guild_ui.stores import MASTER_CRAFTSMAN, Author
from tests.guild.shop_floor.floor_db_helpers import SqliteFloor
from tests.memory_index.helpers import FakeEmbedder

from .helpers import make_shelf

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "vault" / "export_mc_conversations.py"
OWNER = ev.OwnerAuthority("moi-approve")
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
PRINCIPAL = "ownerzed"
OTHER = "visitorq"


def _exporter():
    spec = importlib.util.spec_from_file_location("export_mc_conversations", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


X = _exporter()


class World:
    def __init__(self, tmp_path: Path):
        self.guild = tmp_path / "guild"
        self.guild.mkdir()
        self.store = ConversationStore(str(self.guild), base_floor="guild")
        self.db = SqliteFloor(tmp_path / "floor")
        self.out = tmp_path / "export"
        self.n = 0

    def floor(self, key: str):
        return self.db.store(key)

    def conversation(self, principal=PRINCIPAL, *turns, scope=None) -> dict:
        self.n += 1
        conv, _ = self.store.create(principal, key=f"key-{self.n:08d}", scope=scope)
        self.say(conv, *turns, principal=principal)
        return conv

    def say(self, conv, *turns, principal=PRINCIPAL):
        floor = self.floor(conv["notes_floor"])
        for who, text in turns:
            self.n += 1
            author = {"owner": Author(principal, "owner", "Owner Person"), "mc": MASTER_CRAFTSMAN,
                      "platform": Author("guild_platform", "platform", "Guild platform")}[who] if isinstance(who, str) else who
            request_id = f"req-{self.n:08d}"
            if who == "mc" and getattr(self, "last_owner", None):     # like the portal: MC replies under _reply_key(owner note)
                request_id = X.reply_key(self.last_owner)
            elif who == "owner":
                self.last_owner = request_id
            assert floor.add_note(request_id, text, author).outcome == "kept"

    def export(self, **kw):
        return X.export(guild_dir=self.guild, out=self.out, principal=PRINCIPAL, instance="dev", floor_for=self.floor,
                        now=NOW, **kw)

    def files(self) -> dict[str, dict]:
        return {p.name: json.loads(p.read_text()) for p in sorted(self.out.glob("*.json")) if mc.FILE.match(p.name)}

    def stamps(self) -> dict[str, int]:
        return {p.name: p.stat().st_mtime_ns for p in self.out.glob("*.json") if mc.FILE.match(p.name)}


def source_cfg(world: World) -> SourceCfg:
    return SourceCfg("minimoi-mc", "mc", world.out, ())


def approved(shelf, cfg):
    watchers.run_source(shelf, cfg, now=NOW)                       # first run: the dry-run listing only
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# ── the exporter ─────────────────────────────────────────────────────────────

def test_owner_conversations_are_exported_archived_flagged_and_another_principal_is_not(world):
    keep = world.conversation(PRINCIPAL, ("owner", "PLAN-ALPHA sourdough timing?"), ("mc", "REPLY-ALPHA: overnight."))
    gone = world.conversation(PRINCIPAL, ("owner", "PLAN-BETA old idea"), ("mc", "REPLY-BETA noted"))
    world.store.set_archived(gone["id"], PRINCIPAL, True)
    world.conversation(OTHER, ("owner", "VISITOR-SECRET text"), ("mc", "VISITOR-REPLY"))
    empty = world.conversation(PRINCIPAL)                          # a conversation nobody wrote in: nothing to keep
    result = world.export()
    assert result["status"] == "ok"
    files = world.files()
    assert set(files) == {f"dev--{keep['id']}.json", f"dev--{gone['id']}.json"}
    # empty: that conversation and each account's Shop floor thread (written by the store's own migration on create)
    assert result["counts"] == {"active": 1, "archived": 1, "empty": 2, "new": 2, "notes": 4, "other_principal": 2}
    doc = files[f"dev--{gone['id']}.json"]
    assert doc["archived"] is True and doc["source_id"] == f"dev:{gone['id']}" and doc["schema"] == mc.SCHEMA
    turns = files[f"dev--{keep['id']}.json"]["turns"]
    assert [(t["author_kind"], t["who"], t["text"]) for t in turns] == [
        ("owner", "owner", "PLAN-ALPHA sourdough timing?"), ("agent", "master_craftsman", "REPLY-ALPHA: overnight.")]
    blob = "".join(p.read_text() for p in world.out.iterdir())
    assert "VISITOR" not in blob and PRINCIPAL not in blob           # another account's words and the account name: never
    assert empty["id"] not in blob
    for p in world.out.iterdir():
        assert (p.stat().st_mode & 0o777) == 0o600
    assert (world.out.stat().st_mode & 0o777) == 0o700


def test_the_shop_floor_thread_is_exported_once_per_owner_and_another_owners_note_is_dropped(world):
    world.store.list(PRINCIPAL)                                    # the portal's own migration writes the legacy record
    world.store.list(OTHER)
    thread = world.store.get("shop-floor-thread", PRINCIPAL)
    world.say(thread, ("owner", "THREAD-OWNER words"), ("mc", "THREAD-MC words"))
    world.say(thread, (Author(OTHER, "owner", "Visitor"), "THREAD-VISITOR words"))   # the shared floor key (legacy)
    result = world.export()
    files = world.files()
    assert len(files) == 1 and result["counts"]["other_owner_note"] == 1 and result["counts"]["other_principal"] == 1
    (name, doc), = files.items()
    assert name.startswith("dev--legacy-") and doc["legacy"] is True
    assert [t["text"] for t in doc["turns"]] == ["THREAD-OWNER words", "THREAD-MC words"]


def test_off_record_content_cannot_reach_the_store_so_it_cannot_be_exported(world):
    conv = world.conversation(PRINCIPAL, ("owner", "ON-RECORD words"))
    # The floor table itself refuses anything but on_record (sql/001_floor_b1.sql CHECK): a Private turn has no row.
    con = sqlite3.connect(str(world.db.path))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO floor_messages (floor, request_id, author, author_kind, author_label, text, record_mode,"
                    " created_at) VALUES (?, 'x-1', ?, 'owner', 'O', 'PRIVATE words', 'off_record', '2026-10-17')",
                    (conv["notes_floor"], PRINCIPAL))
    con.close()
    # The conversation store has no record mode at all; a record that ever says off_record is refused whole.
    path = world.guild / "conversations" / f"{conv['id']}.json"
    data = json.loads(path.read_text())
    assert "record_mode" not in data
    data["record_mode"] = "off_record"
    path.write_text(json.dumps(data))
    result = world.export()
    assert result["counts"]["off_record"] == 1 and world.files() == {}


def test_an_unchanged_conversation_is_not_rewritten_and_a_changed_one_is(world):
    one = world.conversation(PRINCIPAL, ("owner", "first question"), ("mc", "first answer"))
    two = world.conversation(PRINCIPAL, ("owner", "second question"), ("mc", "second answer"))
    world.export()
    stamps = world.stamps()
    again = world.export()
    assert again["counts"]["unchanged"] == 2 and "new" not in again["counts"] and "changed" not in again["counts"]
    assert world.stamps() == stamps
    world.say(two, ("owner", "a follow-up"))
    third = world.export()
    assert third["counts"]["unchanged"] == 1 and third["counts"]["changed"] == 1
    assert (world.out / f"dev--{one['id']}.json").stat().st_mtime_ns == stamps[f"dev--{one['id']}.json"]


def test_a_floor_that_cannot_be_read_is_counted_and_its_earlier_file_stays(world):
    conv = world.conversation(PRINCIPAL, ("owner", "kept question"))
    world.export()
    before = (world.out / f"dev--{conv['id']}.json").read_bytes()

    class Down:
        def list_notes(self, **_):
            from minimoi_portal.guild_ui.adapters.contract import live_unknown
            return live_unknown("read_failed", "down", "test")
    result = X.export(guild_dir=world.guild, out=world.out, principal=PRINCIPAL, instance="dev", floor_for=lambda k: Down())
    assert result["status"] == "partial" and result["counts"]["notes_unavailable"] == 2      # it and the Shop floor thread
    assert (world.out / f"dev--{conv['id']}.json").read_bytes() == before


def test_notes_are_read_across_pages_in_store_order(world, monkeypatch):
    monkeypatch.setattr(X, "PAGE", 3)
    conv = world.conversation(PRINCIPAL, *[("owner" if i % 2 == 0 else "mc", f"note {i}") for i in range(10)])
    world.export()
    doc = world.files()[f"dev--{conv['id']}.json"]
    assert [t["text"] for t in doc["turns"]] == [f"note {i}" for i in range(10)]
    ids = [t["note_id"] for t in doc["turns"]]
    assert ids == sorted(ids) and len(set(ids)) == 10


def test_too_many_notes_is_refused_never_truncated(world, monkeypatch):
    monkeypatch.setattr(X, "MAX_NOTES", 3)
    world.conversation(PRINCIPAL, *[("owner", f"n{i}") for i in range(5)])
    result = world.export()
    assert result["counts"]["too_large"] == 1 and world.files() == {}


def test_dry_run_writes_nothing_and_reports_counts_by_month_only(world):
    world.conversation(PRINCIPAL, ("owner", "DRY-SECRET question"), ("mc", "DRY-SECRET answer"))
    result = X.export(guild_dir=world.guild, out=None, principal=PRINCIPAL, instance="dev", floor_for=world.floor,
                      dry_run=True)
    assert not world.out.exists()
    assert result["counts"]["would_export"] == 1 and sum(result["by_month"]["notes"].values()) == 2
    assert "DRY-SECRET" not in json.dumps(result)


def test_the_script_runs_by_path_without_pythonpath(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "DATABASE_URL", "GUILD_QUEUE_PATH")}
    done = subprocess.run([sys.executable, str(SCRIPT), "--principal", "p", "--instance", "dev", "--out", str(tmp_path / "o"),
                           "--guild-dir", str(tmp_path)], capture_output=True, text=True, env=env, cwd=str(tmp_path),
                          timeout=120)
    assert done.returncode == 2 and done.stdout.startswith("export\tdatabase_not_configured")
    assert not (tmp_path / "o").exists()


def test_schema_and_bounds_agree_with_the_shelf_reader():
    assert X.SCHEMA == mc.SCHEMA and X.MAX_BYTES == mc.MAX_BYTES <= watchers.DEFAULT_MAX_SOURCE_BYTES


def test_voice_transcript_sides_come_from_the_platform_note_and_other_platform_notes_are_system(world):
    from minimoi_portal.guild_ui.mc_voice_transcripts import AUTHOR_PREFIX, PAGE
    conv = world.conversation(PRINCIPAL, ("owner", "typed question"))
    floor = world.floor(conv["notes_floor"])
    voice = Author(AUTHOR_PREFIX + "0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b", "platform", "Voice transcript")
    floor.add_note("v-000001", "[Voice transcript: owner ASR; partial=false; part=1/1]\nspoken question", voice, page=PAGE)
    floor.add_note("v-000002", "[Voice transcript: assistant generated; playback unverified; partial=false; part=1/1]\n"
                               "spoken answer", voice, page=PAGE)
    world.say(conv, ("platform", "a receipt"))
    world.export()
    parsed = mc.parse([(world.out / f"dev--{conv['id']}.json").read_bytes()])
    assert [(t.speaker, t.attrs["kind"]) for t in parsed.turns] == [
        ("human", "owner"), ("human", "voice_asr"), ("assistant", "voice_generated"), ("system", "platform")]


# ── the shelf watcher ────────────────────────────────────────────────────────

def test_capture_dedups_by_conversation_and_a_change_adds_an_edition(world, tmp_path):
    a = world.conversation(PRINCIPAL, ("owner", "about the kiln schedule"), ("mc", "fire on Tuesday"))
    b = world.conversation(PRINCIPAL, ("owner", "about the glaze"), ("mc", "use the blue one"))
    world.export()
    shelf, cfg = make_shelf(tmp_path), source_cfg(world)
    first = watchers.run_source(shelf, cfg, now=NOW)
    assert first["status"] == "dry_run_only" and shelf.list_records() == []
    listing = json.loads(approvals.dry_run_path(shelf, cfg.name).read_text())
    assert "kiln" not in json.dumps(listing) and listing["counts"]["would_copy"] == 2
    approvals.approve_source(shelf, cfg.name, cfg.fingerprint(), OWNER)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out == {"status": "ok", "counts": {codes.CAPTURED: 2}}
    keys = set(shelf.index())
    assert keys == {f"minimoi-mc:dev:{a['id']}", f"minimoi-mc:dev:{b['id']}"}
    # Nothing changed: the export is not rewritten, so the watcher does not even open the files.
    world.export()
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {"skipped_unchanged": 2}
    # A new note: one new edition of the same record, the other untouched.
    world.say(b, ("owner", "and the rim?"), ("mc", "leave the rim bare"))
    assert world.export()["counts"]["changed"] == 1
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"] == {codes.EDITION_ADDED: 1, "skipped_unchanged": 1}
    assert set(shelf.index()) == keys
    main = shelf.main_path(shelf.index()[f"minimoi-mc:dev:{b['id']}"])
    meta, body = record.load(record.read(main))
    assert meta["edition"] == 2 and [e["kind"] for e in meta["events"]] == ["edition-added"]
    assert meta["chair"] == "MC" and meta["normalized"]["mc_archived"] is False and meta["normalized"]["turns"] == 4
    rep = ledger.report(shelf)["sources"]["minimoi-mc"]
    assert rep["captured"] == 2 and rep["missing"] == {}
    rows = (Path(shelf.ledger_dir) / "ledger.jsonl").read_text()
    assert '"outcome": "edition-added"' in rows and "kiln" not in rows and "glaze" not in rows


def test_archived_conversation_is_captured_and_flagged(world, tmp_path):
    conv = world.conversation(PRINCIPAL, ("owner", "retired topic"), ("mc", "archived answer"))
    world.store.set_archived(conv["id"], PRINCIPAL, True)
    world.export()
    shelf, cfg = make_shelf(tmp_path), source_cfg(world)
    approved(shelf, cfg)
    assert watchers.run_source(shelf, cfg, now=NOW)["counts"] == {codes.CAPTURED: 1}
    (entry,) = shelf.list_records()
    meta, _ = record.load(record.read(shelf.main_path(entry)))
    assert meta["normalized"]["mc_archived"] is True


def test_a_malformed_export_fails_with_a_fixed_code(world, tmp_path):
    world.out.mkdir(mode=0o700)
    (world.out / "dev--c-0123456789ab.json").write_text(json.dumps({"schema": "something/else", "SECRET": "words"}))
    shelf, cfg = make_shelf(tmp_path), source_cfg(world)
    approved(shelf, cfg)
    out = watchers.run_source(shelf, cfg, now=NOW)
    assert out["counts"] == {codes.FAILED: 1}
    blob = (Path(shelf.ledger_dir) / "ledger.jsonl").read_text()
    assert '"reason": "bad_export"' in blob and "SECRET" not in blob


def test_an_unknown_author_kind_is_a_flagged_system_turn():
    doc = {"schema": mc.SCHEMA, "instance": "dev", "conversation_id": "c-0123456789ab", "source_id": "dev:c-0123456789ab",
           "legacy": False, "archived": False, "scope": None, "created_at": "2026-10-01T10:00:00+00:00",
           "updated_at": "2026-10-01T10:00:00+00:00",
           "turns": [{"note_id": 1, "created_at": "2026-10-01T10:00:00+00:00", "author_kind": "robot", "who": "x",
                      "label": "X", "voice": None, "text": "hello"}]}
    parsed = mc.parse([json.dumps(doc).encode()])
    assert parsed.turns[0].speaker == "system" and parsed.coverage["flags"] == ["unknown_kind"]


# ── registered everywhere the shelf and the index look ───────────────────────

def test_mc_is_registered_in_every_closed_list():
    assert "MC" in record.CHAIRS and "mc" in shelf_config.SOURCE_KINDS
    assert sessions.NORMALIZER_VERSION["minimoi-mc"] == mc.NORMALIZER == watchers.normalizer_for("mc")
    assert sessions.RETRIEVAL_MIN_NORMALIZER["minimoi-mc"] == policy.MIN_NORMALIZER["minimoi-mc"] == 1
    assert policy.min_normalizer("minimoi-mc") == 1
    assert report.LABELS["minimoi-mc"] == "Master Craftsman" and "minimoi-mc" in shelf_cli.LIST_SOURCES
    spec = importlib.util.spec_from_file_location("daily_watch_for_mc", REPO / "scripts" / "memory" / "daily_watch.py")
    dw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dw)
    assert "minimoi-mc" in dw.OPTIONAL_SOURCES and "minimoi-mc" not in dw.SOURCES
    example = shelf_config.parse(json.loads((REPO / "config" / "memory_shelf.example.json").read_text()))
    assert example.sources["minimoi-mc"].kind == "mc"


def test_captured_records_pass_the_index_gates_are_searchable_and_replay_from_source(world, tmp_path):
    conv = world.conversation(PRINCIPAL, ("owner", "how long should the sourdough rise"),
                              ("mc", "let the sourdough rise overnight"))
    world.export()
    shelf, cfg = make_shelf(tmp_path), source_cfg(world)
    approved(shelf, cfg)
    watchers.run_source(shelf, cfg, now=NOW)
    (entry,) = shelf.list_records()
    main = shelf.main_path(entry)
    meta, body = record.load(record.read(main))
    assert policy.record_gate(meta) is None and policy.provider_of(meta) == "minimoi-mc"
    turns = render.parse_body(body)
    assert [t.speaker for t in policy.retrievable_turns(turns)] == ["human", "assistant"]
    emb = FakeEmbedder()
    assert indexer.update(shelf.root, emb, min_free_bytes=0)["code"] == "ok"
    found = search.search("sourdough", shelf_root=shelf.root, embedder=emb, caller=policy.Caller(tier="raw"))
    assert meta["id"] in {h["record_id"] for h in found["keyword"]}
    whole = shelf_config.Config(shelf_root=shelf.root, sources={cfg.name: cfg})
    assert fidelity.check_record(whole, main)["status"] == "ok"
    assert report._changed_sources(shelf, whole, cfg.name, cfg)["checked"] == 1
    assert conv["id"] in meta["source"]


# ── the shared Shop floor thread (review finding E1, 17 Oct) ─────────────────

def _n(nid, kind, who, request_id, text, page=None):
    return {"id": nid, "request_id": request_id, "who": who, "author_kind": kind, "author_label": kind,
            "text": text, "created_at": "2026-10-01T10:00:00Z", "context": {"page": page}}


def test_a_shared_legacy_floor_keeps_only_the_owners_notes_and_mcs_replies_to_them():
    from collections import Counter
    owner = "robertq"
    notes = [_n(1, "owner", owner, "r1", "mine"), _n(2, "agent", "mc", X.reply_key("r1"), "reply to mine"),
             _n(3, "owner", OTHER, "v1", "theirs"), _n(4, "agent", "mc", X.reply_key("v1"), "reply to theirs"),
             _n(5, "platform", "voice:abc", "t1", "a transcript nobody can attribute"),
             _n(6, "agent", "mc", "unrelated", "an agent note tied to no owner note")]
    counts = Counter()
    doc = X.document("dev", "legacy", {"legacy": True, "notes_floor": "guild"}, notes, owner, counts)
    assert [t["text"] for t in doc["turns"]] == ["mine", "reply to mine"]
    assert counts["other_owner_note"] == 1 and counts["shared_floor_unattributed_note"] == 3


def test_every_legacy_floor_is_attributable_only_even_with_no_other_typed_note_in_view():
    from collections import Counter
    owner = "robertq"
    notes = [_n(1, "owner", owner, "r1", "mine"), _n(2, "agent", "mc", X.reply_key("r1"), "reply"),
             _n(3, "platform", "voice:abc", "t1", "another user's spoken words"), _n(4, "agent", "mc", "x", "report")]
    counts = Counter()
    doc = X.document("dev", "legacy", {"legacy": True, "notes_floor": "guild"}, notes, owner, counts)
    assert [t["text"] for t in doc["turns"]] == ["mine", "reply"] and counts["shared_floor_unattributed_note"] == 2


def test_an_owned_conversation_keeps_every_note():
    from collections import Counter
    owner = "robertq"
    notes = [_n(1, "owner", owner, "r1", "mine"), _n(2, "agent", "mc", X.reply_key("r1"), "reply"),
             _n(3, "platform", "voice:abc", "t1", "transcript"), _n(4, "agent", "mc", "x", "report")]
    doc = X.document("dev", "c", {"notes_floor": "guild/c-000000000001"}, notes, owner, Counter())
    assert len(doc["turns"]) == 4


def test_the_reply_key_matches_the_portals():
    from minimoi_portal.guild_ui import api
    assert X.reply_key("note-123") == api._reply_key("note-123")

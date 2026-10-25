"""Domain behavior on disposable data; PostgreSQL repeats these in its own harness."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import os

import pytest
from jsonschema import Draft202012Validator

from floor_db_helpers import SqliteFloor
from minimoi_portal.guild_ui.board_actions import BoardActions, BoardContext, BoardRefused
from minimoi_portal.guild_ui.board_text import clean, TextRefused
from minimoi_portal.guild_ui.stores import Author, FloorStores
from domains.guild.board_operations import RESULT

CAPS = frozenset({"guild.board.notes.read", "guild.board.note.create", "guild.board.note.update",
                  "guild.board.operations.read", "guild.board.operation.undo"})
OWNER = Author("test-owner", "owner", "Test owner")


def uid():
    return str(uuid4())


@pytest.fixture
def board_case(tmp_path):
    dsn = os.environ.get("BOARD_ACTIONS_TEST_DSN")
    floor = "test-board-" + uuid4().hex
    if dsn:
        import psycopg2
        from psycopg2.extensions import parse_dsn
        assert parse_dsn(dsn)["dbname"].startswith("board_domain_test_"), "Disposable Board test DB required"

        class PgFixture:
            def rows(self, table):
                assert table in {"floor_postits", "board_operations", "floor_board_meta"}
                from psycopg2.extras import RealDictCursor
                conn = psycopg2.connect(dsn)
                try:
                    with conn.cursor(cursor_factory=RealDictCursor) as q:
                        q.execute("SELECT * FROM guild." + table + " WHERE floor=%s", [floor])
                        return list(q.fetchall())
                finally:
                    conn.close()
            def count(self, table): return len(self.rows(table))
        db, store = PgFixture(), FloorStores(lambda: dsn, floor=floor)
    else:
        db = SqliteFloor(tmp_path / "board-actions")
        store = db.store(floor)
    svc = BoardActions(store, owner="test-owner", environment="dev", cursor_key=b"test-only-cursor-key" * 2,
                       item_allowed=lambda owner, item: item == 12)
    ctx = BoardContext("test-owner", "dev", floor, "test-mc", "synthetic-message", CAPS)
    return db, store, svc, ctx


def created(svc, ctx, text="Morgen Bewerbung schreiben", **fields):
    result = svc.create(ctx, uid(), {"text": text, **fields})
    assert result["state"] == "verified", result
    Draft202012Validator(RESULT).validate(result)
    return result


def test_create_replay_deliberate_duplicate_and_payload_conflict(board_case):
    db, store, svc, ctx = board_case
    op = uid()
    first = svc.create(ctx, op, {"text": "One note"})
    again = svc.create(ctx, op, {"text": "One note"})
    assert first["receipt"] == again["receipt"] and again["replayed"]
    conflict = svc.create(ctx, op, {"text": "One note!"})
    assert conflict["state"] == "not_applied" and conflict["code"] == "operation_conflict"
    created(svc, ctx, "One note")
    assert db.count("floor_postits") == db.count("board_operations") == 2


def test_original_receipt_survives_edit_undo_and_purge(board_case):
    db, store, svc, ctx = board_case
    initial = created(svc, ctx)
    ref = initial["current"]["id"]
    update = svc.update(ctx, uid(), ref, 1, {"text": "Freitag", "label": "idea", "done": True})
    assert update["current"]["state"] == "done" and update["current"]["semantic_revision"] == 2
    old = svc.result(ctx, initial["receipt"]["operation_id"])
    assert old["receipt"] == initial["receipt"] and old["current"]["text"] == "Freitag"
    undo = svc.undo(ctx, uid(), update["receipt"]["operation_id"], 2)
    assert undo["code"] == "undone" and undo["current"]["text"] == initial["current"]["text"]
    assert undo["current"]["state"] == "active"
    assert svc.undo(ctx, uid(), initial["receipt"]["operation_id"], 1)["code"] == "revision_conflict"


def test_failed_undo_does_not_mark_original_as_undone(board_case):
    _, store, svc, ctx = board_case
    first = created(svc, ctx)
    op = first["receipt"]["operation_id"]
    assert svc.undo(ctx, uid(), op, 999)["code"] == "revision_conflict"
    undone = svc.undo(ctx, uid(), op, 1)
    assert undone["code"] == "undone" and undone["current"]["state"] == "binned"
    again = svc.undo(ctx, uid(), op, 1)
    assert again["code"] == "already_undone"
    assert svc.result(ctx, op)["receipt"] == first["receipt"]


def test_edit_then_revert_still_blocks_old_undo(board_case):
    _, _, svc, ctx = board_case
    first = created(svc, ctx, "Original")
    ref = first["current"]["id"]
    svc.update(ctx, uid(), ref, 1, {"text": "Edited"})
    svc.update(ctx, uid(), ref, 2, {"text": "Original"})
    assert svc.undo(ctx, uid(), first["receipt"]["operation_id"], 1)["code"] == "revision_conflict"
    assert svc.read(ctx, ref)["state"] == "active"


def test_noop_has_no_undo_and_stale_patch_changes_nothing(board_case):
    _, _, svc, ctx = board_case
    first = created(svc, ctx)
    ref = first["current"]["id"]
    noop = svc.update(ctx, uid(), ref, 1, {"done": False})
    assert noop["code"] == "unchanged" and noop["current"]["semantic_revision"] == 1
    assert svc.undo(ctx, uid(), noop["receipt"]["operation_id"], 1)["code"] == "undo_unavailable"
    bad = svc.update(ctx, uid(), ref, 20, {"text": "Lost update", "done": True})
    assert bad["code"] == "revision_conflict" and bad["current"] == first["current"]


@pytest.mark.parametrize("field,value", [("principal", "stranger"), ("board", "elsewhere"),
                                        ("environment", "prod"), ("capabilities", frozenset())])
def test_wrong_scope_refused_before_write(board_case, field, value):
    db, _, svc, ctx = board_case
    with pytest.raises(BoardRefused, match="scope_denied"):
        svc.create(replace(ctx, **{field: value}), uid(), {"text": "No"})
    assert db.count("floor_postits") == 0


def test_other_application_cannot_reuse_or_read_an_operation(board_case):
    db, _, svc, ctx = board_case
    first = created(svc, ctx)
    other = replace(ctx, executor="another-app")
    op = first["receipt"]["operation_id"]
    assert svc.result(other, op)["receipt"] is None
    assert svc.create(other, op, {"text": "No"})["code"] == "operation_conflict"
    assert svc.undo(other, uid(), op, 1)["code"] == "operation_not_found"
    assert svc.result(replace(ctx, executor=ctx.principal, owner_session=True), op)["receipt"] == first["receipt"]
    assert db.rows("floor_postits")[0]["binned_at"] is None


def test_concurrent_replays_create_once(board_case):
    db, _, svc, ctx = board_case
    op = uid()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: svc.create(ctx, op, {"text": "Once"}), range(4)))
    assert all(r["state"] == "verified" for r in results), results
    assert len({r["current"]["id"] for r in results}) == 1
    assert db.count("board_operations") == db.count("floor_postits") == 1


def test_receipt_insert_failure_rolls_back_note_and_revision(board_case, monkeypatch):
    db, store, svc, ctx = board_case
    original_run = store._run

    def fail_run(work, *, write):
        def wrap(q):
            def broken(sql, params=()):
                if sql.startswith("INSERT INTO guild.board_operations"):
                    raise RuntimeError("synthetic crash before receipt")
                return q(sql, params)
            return work(broken)
        return original_run(wrap, write=write)
    monkeypatch.setattr(store, "_run", fail_run)
    assert svc.create(ctx, uid(), {"text": "Atomic"})["state"] == "unknown"
    assert db.count("floor_postits") == db.count("board_operations") == db.count("floor_board_meta") == 0


def test_lost_commit_response_recovers_same_receipt(board_case, monkeypatch):
    db, store, svc, ctx = board_case
    real_connect = store._connect

    class LostReply:
        def __init__(self, conn): self.conn = conn
        def __getattr__(self, name): return getattr(self.conn, name)
        def commit(self):
            self.conn.commit()
            raise OSError("synthetic lost commit acknowledgement")
    monkeypatch.setattr(store, "_connect", lambda url: LostReply(real_connect(url)))
    op = uid()
    assert svc.create(ctx, op, {"text": "Actually saved"})["state"] == "unknown"
    monkeypatch.setattr(store, "_connect", real_connect)
    recovered = svc.result(ctx, op)
    assert recovered["state"] == "verified" and recovered["current"]["text"] == "Actually saved"
    assert svc.create(ctx, op, {"text": "Actually saved"})["replayed"]
    assert db.count("floor_postits") == 1


def test_query_pagination_literal_wildcards_and_cursor_scope(board_case):
    _, _, svc, ctx = board_case
    for text in ("100% done", "One", "Two"):
        created(svc, ctx, text)
    page = svc.query(ctx, limit=2)
    assert len(page["notes"]) == 2 and page["truncated"]
    last = svc.query(ctx, limit=2, cursor=page["next_cursor"])
    assert len(last["notes"]) == 1
    assert len({n["id"] for n in page["notes"] + last["notes"]}) == 3
    assert len(svc.query(ctx, query="%" )["notes"]) == 1
    for cursor in ("bad", page["next_cursor"] + "a", 123):
        with pytest.raises(BoardRefused, match="cursor_invalid"):
            svc.query(ctx, cursor=cursor)
    with pytest.raises(BoardRefused, match="cursor_invalid"):
        svc.query(ctx, query="different", cursor=page["next_cursor"])


def test_unicode_limit_and_privacy_scrub():
    assert clean("\r\nMorgen 👨‍👩‍👧‍👦\r\n").text == "Morgen 👨‍👩‍👧‍👦"
    assert clean("é").count == 1
    assert clean("e\u0301").count == 2
    assert clean("👨‍👩‍👧‍👦").count == 7
    assert clean("😀" * 280).count == 280
    for text, code in (("😀" * 281, "text_too_long"), ("\ud800", "invalid_text"), (" \n", "text_empty")):
        with pytest.raises(TextRefused, match=code):
            clean(text)
    result = clean("Email test@example.com")
    assert result.scrubbed and "test@example.com" not in result.text


def test_invalid_domain_values_write_nothing(board_case):
    db, _, svc, ctx = board_case
    for value in ({"text": "x", "item_ref": 13}, {"text": "x", "label": "invented"},
                  {"text": "x", "done": True}, {"text": "x", "url": "https://example.org"}):
        with pytest.raises(BoardRefused): svc.create(ctx, uid(), value)
    assert db.count("floor_postits") == 0


@pytest.mark.parametrize("writer", ["text", "label", "link", "done", "undone", "bin", "restore"])
def test_substantive_legacy_writers_advance_semantic_revision(board_case, writer):
    _, store, svc, ctx = board_case
    first = created(svc, ctx)
    pid = int(first["current"]["id"])
    if writer == "undone": store.set_done(pid, True, OWNER, expect_version=1, idempotency_key=uid())
    if writer == "restore": store.bin_postit(pid, OWNER, idempotency_key=uid())
    before = svc.read(ctx, str(pid))
    version = store._run(lambda q: store._postit_by_id(q, pid)["version"], write=False)
    if writer in {"bin", "restore"}:
        getattr(store, writer + "_postit")(pid, OWNER, idempotency_key=uid())
    elif writer in {"done", "undone"}:
        store.set_done(pid, writer == "done", OWNER, expect_version=version, idempotency_key=uid())
    else:
        value = {"text": "Changed", "label": "idea", "link": 12}[writer]
        getattr(store, "set_" + writer)(pid, value, OWNER, expect_version=version, idempotency_key=uid())
    assert svc.read(ctx, str(pid))["semantic_revision"] == before["semantic_revision"] + 1


def test_reorder_bulk_renumber_placement_resize_do_not_block_undo(board_case):
    _, store, svc, ctx = board_case
    a, b = created(svc, ctx, "A"), created(svc, ctx, "B")
    aid, bid = int(a["current"]["id"]), int(b["current"]["id"])
    rev = store.board().data["order_rev"]
    result = store.reorder(aid, before_id=bid, after_id=None, expect_order_rev=rev, by=OWNER, idempotency_key=uid())
    assert result.outcome == "moved" and result.value["renumbered"]
    rev = store.board().data["order_rev"]
    assert store.place([{"id": aid, "x": 10, "y": 10, "w": 300, "h": 220}],
                       expect_order_rev=rev, by=OWNER, idempotency_key=uid()).outcome == "moved"
    rev = store.board().data["order_rev"]
    assert store.reorder(aid, before_id=None, after_id=bid, expect_order_rev=rev, by=OWNER, idempotency_key=uid()).outcome == "moved"
    assert svc.read(ctx, str(aid))["semantic_revision"] == svc.read(ctx, str(bid))["semantic_revision"] == 1
    assert svc.undo(ctx, uid(), a["receipt"]["operation_id"], 1)["code"] == "undone"


def test_legacy_insert_and_outcome_initialize_revision_one(board_case):
    _, store, svc, ctx = board_case
    note = store.add_postit("Legacy UI", OWNER, idempotency_key=uid()).value
    assert svc.read(ctx, str(note["id"]))["semantic_revision"] == 1
    outcome = store.add_outcome(note["id"], note["version"], "Outcome", turn_key="synthetic-turn").value
    assert svc.read(ctx, str(outcome["id"]))["semantic_revision"] == 1


def test_scrub_is_reported_without_retaining_unscrubbed_payload(board_case):
    db, _, svc, ctx = board_case
    response = created(svc, ctx, "Contact test@example.com")
    assert response["receipt"]["text_scrubbed"]
    assert "test@example.com" not in repr(db.rows("board_operations"))


def test_photo_insert_initializes_revision_and_purge_removes_target_without_rewriting_receipt(board_case):
    _, store, svc, ctx = board_case
    from minimoi_portal.guild_ui.stores import utc_now
    asset = uid()
    store._run(lambda q: q("INSERT INTO media.assets (id,owner,kind,sha256,storage_key,created_at) "
                            "VALUES (%s,%s,'photo',%s,%s,%s)",
                            [asset, ctx.principal, asset.replace('-', '') * 2, 'test/' + asset, utc_now()]), write=True)
    photo = store.add_photo(asset, ctx.principal, None, OWNER, idempotency_key=uid()).value
    rev = store._run(lambda q: q("SELECT semantic_revision FROM guild.floor_postits WHERE id=%s", [photo['id']]), write=False)
    assert rev[0][0] == 1
    with pytest.raises(BoardRefused, match="note_not_found"):
        svc.read(ctx, str(photo['id']))
    original = created(svc, ctx)
    pid = int(original['current']['id'])
    svc.undo(ctx, uid(), original['receipt']['operation_id'], 1)
    current = store._run(lambda q: store._postit_by_id(q, pid), write=False)
    store.empty_trash(items=[{'id': pid, 'version': current['version']}],
                      trash_rev=store.board().data['trash_rev'], by=OWNER, idempotency_key=uid())
    result = svc.result(ctx, original['receipt']['operation_id'])
    assert result['receipt'] == original['receipt'] and result['current']['state'] == 'unavailable'


def test_restore_position_only_adds_one_substantive_revision(board_case):
    _, store, svc, ctx = board_case
    a, b = created(svc, ctx, 'A'), created(svc, ctx, 'B')
    aid, bid = int(a['current']['id']), int(b['current']['id'])
    def place(pid):
        store.place([{'id': pid, 'x': 20, 'y': 20}], expect_order_rev=store.board().data['order_rev'],
                    by=OWNER, idempotency_key=uid())
    place(aid)
    store.bin_postit(aid, OWNER, idempotency_key=uid())
    place(bid)
    before = svc.read(ctx, str(aid))['semantic_revision']
    store.restore_postit(aid, OWNER, idempotency_key=uid())
    assert svc.read(ctx, str(aid))['semantic_revision'] == before + 1
    row = store._run(lambda q: store._postit_by_id(q, aid), write=False)
    assert (row['pos_x'], row['pos_y']) != (20, 20)


def test_ui_retry_across_promotion_and_atomic_revision_bridge(board_case):
    db, store, svc, base = board_case
    ctx = replace(base, executor=base.principal, owner_session=True)
    old = store.add_postit("old request", OWNER, idempotency_key="old-key")
    replay = svc.ui_write(ctx, "old-key", {"text": "old request"})
    assert replay.repeated and replay.value["id"] == old.value["id"]
    assert db.count("board_operations") == 0
    first = svc.ui_write(ctx, "new-key", {"text": "new request"}, author=OWNER)
    assert first.outcome == "added" and first.value["author_label"] == OWNER.label
    replay = svc.ui_write(ctx, "new-key", {"text": "new request"})
    assert replay.repeated and replay.value["id"] == first.value["id"]
    assert svc.ui_write(ctx, "new-key", {"text": "different"}).outcome == "idempotency_mismatch"
    assert db.count("board_operations") == 1
    target = first.value["id"]
    changed = svc.ui_write(ctx, "edit-key", {"text": "changed"}, target=target, version=1)
    assert changed.outcome == "edited"
    stale = svc.ui_write(ctx, "stale-key", {"text": "must not overwrite"}, target=target, version=1)
    assert stale.outcome == "conflict" and stale.value["text"] == "changed"
    assert svc.ui_write(ctx, "edit-key", {"text": "changed"}, target=target, version=1).repeated
    before = svc.read(ctx, str(target))
    assert before["semantic_revision"] == 2
    assert db.count("board_operations") == 3


def test_ui_honest_unknown_and_scrub_disclosure(board_case, monkeypatch):
    db, store, svc, base = board_case
    ctx = replace(base, executor=base.principal, owner_session=True)
    first = svc.ui_write(ctx, "scrub", {"text": "Email me at user@example.com"})
    assert first.operation["receipt"]["text_scrubbed"]
    original = store._run
    def lost(work, *, write):
        original(work, write=write)
        from minimoi_portal.guild_ui.stores import FloorStoreUnavailable
        raise FloorStoreUnavailable("synthetic lost response")
    monkeypatch.setattr(store, "_run", lost)
    assert svc.ui_write(ctx, "lost-ui", {"text": "one only"}).outcome == "unknown"
    monkeypatch.setattr(store, "_run", original)
    recovered = svc.ui_write(ctx, "lost-ui", {"text": "one only"})
    assert recovered.repeated and recovered.outcome == "added"
    assert db.count("floor_postits") == 2

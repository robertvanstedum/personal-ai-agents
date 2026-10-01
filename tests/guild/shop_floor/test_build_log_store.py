"""Guild 1.1 slice 2 (spec §4.2-4.5, §11): the queue store learns rework and
the trouble fields, owner ranks behind a collection digest, a journal read
per item, and new items. The queue file stays a bare JSON list."""
from __future__ import annotations

import json

import pytest

from domains.guild import queue_store as qs


def _items():
    return [
        {"id": 1, "spec_title": "One", "status": "in_build", "blocked_reason": None},
        {"id": 2, "spec_title": "Two", "status": "spec_ready", "blocked_reason": None},
        {"id": 3, "spec_title": "Three", "status": "design", "blocked_reason": None},
        {"id": 4, "spec_title": "Four", "status": "idea", "blocked_reason": None},
        {"id": 5, "spec_title": "Five", "status": "backlog", "blocked_reason": None},
    ]


@pytest.fixture
def queue(tmp_path):
    folder = tmp_path / "state" / "guild"
    folder.mkdir(parents=True)
    path = folder / "build_queue.json"
    path.write_bytes(qs.serialize(_items()))
    return path


def store(queue):
    return qs.QueueStore(queue, in_container=False, code_root=queue.parent.parent.parent / "code")


def items(queue):
    data = json.loads(queue.read_text())
    assert isinstance(data, list)                       # always a bare list
    return {i["id"]: i for i in data}


def digest(queue, item_id):
    return qs.item_digest(items(queue)[item_id])


def journal(queue):
    path = queue.parent / qs.JOURNAL_NAME
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def status(s, queue, item_id, to, note=None, key=None):
    return s.save_status(item_id, to, expect_item_digest=digest(queue, item_id), principal="robert",
                         note=note, via="test", idempotency_key=key)


# ── §4.2 trouble transitions ──────────────────────────────────────────────────

def test_rework_is_a_status_and_nothing_is_renamed():
    assert "rework" in qs.STATUSES and "blocked" in qs.STATUSES
    assert qs.STATUSES[:10] == ("idea", "design", "backlog", "spec_ready", "in_build", "blocked",
                                "deferred", "cancelled", "superseded", "done")
    assert qs.TROUBLE == ("blocked", "rework")


@pytest.mark.parametrize("to", ["blocked", "rework"])
def test_entering_trouble_needs_a_reason_and_records_where_it_came_from(queue, to):
    s = store(queue)
    before = queue.read_bytes()
    for note in (None, "", "   "):
        assert status(s, queue, 1, to, note).result == qs.INVALID
    assert queue.read_bytes() == before                                  # nothing written
    assert status(s, queue, 1, to, "x" * 501).result == qs.INVALID       # at most 500 characters
    done = status(s, queue, 1, to, "  the gateway key is missing  ")
    assert done.ok
    it = items(queue)[1]
    assert it["status"] == to and it["trouble_reason"] == "the gateway key is missing"
    assert it["trouble_from"] == "in_build" and it["trouble_since"]
    assert it["blocked_reason"] == ("the gateway key is missing" if to == "blocked" else None)


def test_blocked_and_rework_keep_trouble_from_and_since_both_ways(queue):
    s = store(queue)
    assert status(s, queue, 1, "blocked", "waiting on Robert").ok
    first = items(queue)[1]
    assert status(s, queue, 1, "rework", "review found a gap").ok
    it = items(queue)[1]
    assert it["trouble_from"] == "in_build" and it["trouble_since"] == first["trouble_since"]
    assert it["blocked_reason"] is None                                  # legacy meaning: only while blocked
    assert it["trouble_reason"] == "review found a gap"
    assert status(s, queue, 1, "blocked", "blocked again").ok
    it = items(queue)[1]
    assert it["trouble_from"] == "in_build"                              # never overwritten with rework
    assert it["trouble_since"] == first["trouble_since"] and it["blocked_reason"] == "blocked again"


def test_a_reason_edit_keeps_the_old_reason_in_the_journal(queue):
    s = store(queue)
    assert status(s, queue, 2, "rework", "first reason").ok
    assert status(s, queue, 2, "rework", "second reason").ok
    assert items(queue)[2]["trouble_reason"] == "second reason"
    edit = [r for r in journal(queue) if r["kind"] == "intent"][-1]
    assert edit["reason_from"] == "first reason" and edit["reason_to"] == "second reason" and edit["reason_edit"]
    history = s.item_journal(2)
    assert history[-1]["reason_edit"] and history[-1]["reason_from"] == "first reason"
    assert history[0]["from"] == "spec_ready" and history[0]["to"] == "rework"


def test_leaving_trouble_is_explicit_clears_the_fields_and_any_status_is_allowed(queue):
    s = store(queue)
    assert status(s, queue, 1, "rework", "fix the drawer").ok
    # Nothing but a status Save takes it out: an edit keeps it in rework.
    assert s.edit_metadata(1, {"summary": "new summary"}, expect_item_digest=digest(queue, 1),
                           principal="robert").ok
    assert items(queue)[1]["status"] == "rework" and items(queue)[1]["trouble_reason"] == "fix the drawer"
    done = status(s, queue, 1, "backlog")                    # not the suggested trouble_from (in_build)
    assert done.ok
    it = items(queue)[1]
    assert it["status"] == "backlog" and it["blocked_reason"] is None
    assert not set(qs.TROUBLE_FIELDS) & set(it)
    left = [r for r in journal(queue) if r["kind"] == "intent"][-1]
    assert left["left_trouble_from"] == "in_build" and left["reason_from"] == "fix the drawer"


def test_a_legacy_blocked_item_without_trouble_fields_still_moves(queue):
    data = _items()
    data[0].update(status="blocked", blocked_reason="old style")
    queue.write_bytes(qs.serialize(data))
    s = store(queue)
    assert status(s, queue, 1, "rework", "now rework").ok
    it = items(queue)[1]
    assert it["status"] == "rework" and "trouble_from" not in it      # unknown, never invented
    assert s.item_journal(1)[-1]["reason_from"] == "old style"


# ── §4.3 ranks ────────────────────────────────────────────────────────────────

def rank(s, queue, item_id, value, key=None, expect=None):
    data = json.loads(queue.read_text())
    return s.set_rank(item_id, value, expect_rank_digest=expect or qs.rank_digest(data), principal="robert",
                      idempotency_key=key)


def ranks(queue):
    return {i: it.get("owner_rank") for i, it in items(queue).items() if it.get("owner_rank")}


def test_the_rank_digest_covers_every_ranked_pair():
    a = [{"id": 1, "owner_rank": 1}, {"id": 2}, {"id": 3, "owner_rank": 2}]
    b = [{"id": 3, "owner_rank": 2}, {"id": 1, "owner_rank": 1}, {"id": 2, "status": "x"}]
    assert qs.rank_digest(a) == qs.rank_digest(b)                          # order and other fields don't matter
    assert qs.rank_digest(a) != qs.rank_digest([{"id": 1, "owner_rank": 2}, {"id": 3, "owner_rank": 1}])
    assert qs.ranking([{"id": 1, "owner_rank": True}, {"id": 2, "owner_rank": 7}]) == []


def test_a_shift_moves_every_holder_down_and_clears_the_fourth_in_one_operation(queue):
    s = store(queue)
    for item_id, value in ((1, 1), (2, 2), (3, 3)):
        assert rank(s, queue, item_id, value).ok
    assert ranks(queue) == {1: 1, 2: 2, 3: 3}
    lines_before = len(journal(queue))
    done = rank(s, queue, 4, 1, key="rank-key-0001")
    assert done.ok and done.verified and done.receipt_id
    assert ranks(queue) == {4: 1, 1: 2, 2: 3}                              # 3 was pushed past 3: cleared
    new = journal(queue)[lines_before:]
    assert [r["kind"] for r in new] == ["intent", "completed"]           # one intent, one receipt
    intent = new[0]
    assert intent["op"] == "rank" and intent["item_id"] == 4
    assert intent["items"] == [{"id": 1, "from": 1, "to": 2}, {"id": 2, "from": 2, "to": 3},
                               {"id": 3, "from": 3, "to": None}, {"id": 4, "from": None, "to": 1}]
    assert intent["rank_before_digest"] != intent["rank_after_digest"]
    assert done.ranking == [[1, 2], [2, 3], [4, 1]] and done.rank_digest == intent["rank_after_digest"]
    # Every moved item's history shows the shift, with the same receipt.
    assert s.item_journal(3)[-1] == {**s.item_journal(3)[-1], "from": 3, "to": None, "shifted_by": 4,
                                     "receipt_id": done.receipt_id}


def test_moving_up_and_gaps_shift_only_as_far_as_needed(queue):
    s = store(queue)
    for item_id, value in ((1, 1), (2, 2), (3, 3)):
        assert rank(s, queue, item_id, value).ok
    assert rank(s, queue, 3, 1).ok
    assert ranks(queue) == {3: 1, 1: 2, 2: 3}
    assert rank(s, queue, 1, None).ok                                     # clear: only the target
    assert ranks(queue) == {3: 1, 2: 3}
    assert rank(s, queue, 5, 2).ok                                        # the gap at 2: nobody moves
    assert ranks(queue) == {3: 1, 5: 2, 2: 3}


def test_two_devices_ranking_different_items_the_second_gets_a_conflict(queue):
    s = store(queue)
    seen = qs.rank_digest(json.loads(queue.read_text()))                 # both devices read this
    assert rank(s, queue, 1, 1, expect=seen).ok
    before = queue.read_bytes()
    second = rank(s, queue, 2, 1, expect=seen)
    assert second.result == qs.CONFLICT
    assert second.ranking == [[1, 1]] and second.rank_digest == qs.rank_digest(json.loads(before))
    assert queue.read_bytes() == before and ranks(queue) == {1: 1}       # never overwritten


def test_two_devices_reordering_the_second_gets_a_conflict(queue):
    s = store(queue)
    for item_id, value in ((1, 1), (2, 2), (3, 3)):
        assert rank(s, queue, item_id, value).ok
    seen = qs.rank_digest(json.loads(queue.read_text()))
    assert rank(s, queue, 3, 1, expect=seen).ok
    assert rank(s, queue, 2, 1, expect=seen).result == qs.CONFLICT
    assert ranks(queue) == {3: 1, 1: 2, 2: 3}


def test_a_retried_rank_returns_the_same_receipt_and_writes_once(queue):
    s = store(queue)
    seen = qs.rank_digest(json.loads(queue.read_text()))
    first = rank(s, queue, 1, 2, key="rank-retry-01", expect=seen)
    after = queue.read_bytes()
    again = rank(s, queue, 1, 2, key="rank-retry-01", expect=seen)       # the same request, answer lost
    assert again.ok and again.repeated and again.receipt_id == first.receipt_id
    assert queue.read_bytes() == after
    other = rank(s, queue, 1, 3, key="rank-retry-01")
    assert other.result == qs.IDEMPOTENCY_MISMATCH and queue.read_bytes() == after


def test_bad_ranks_and_unknown_items_change_nothing(queue):
    s = store(queue)
    before = queue.read_bytes()
    for bad in (0, 4, True, "1"):
        assert rank(s, queue, 1, bad).result == qs.INVALID
    assert rank(s, queue, 99, 1).result == qs.NOT_FOUND
    assert s.set_rank(1, 1, expect_rank_digest=None, principal="r").result == qs.CONFLICT
    assert queue.read_bytes() == before and journal(queue) == []


def test_an_uncertain_rank_is_reconciled_as_one_operation(queue, monkeypatch):
    """The receipt line fails after the file was written (a crash between the
    two): the Save is uncertain, and the next write reconciles the whole shift
    from the file digests, with one recovered receipt for every moved item."""
    s = store(queue)
    for item_id, value in ((1, 1), (2, 2)):
        assert rank(s, queue, item_id, value).ok
    real = s._append

    def no_receipt(record):
        if record.get("kind") == "completed":
            raise OSError(28, "No space left on device")
        real(record)
    monkeypatch.setattr(s, "_append", no_receipt)
    out = rank(s, queue, 3, 1)
    assert out.result == qs.UNCERTAIN
    assert ranks(queue) == {3: 1, 1: 2, 2: 3}                             # the file holds the whole shift
    monkeypatch.setattr(s, "_append", real)
    assert s.reconcile()[0]["recovered"] is True
    for item_id in (1, 2, 3):
        assert s.item_journal(item_id)[-1]["recovered"] is True


def test_a_failed_write_leaves_every_rank_as_it_was(queue, monkeypatch):
    s = store(queue)
    assert rank(s, queue, 1, 1).ok
    before = queue.read_bytes()

    def boom(*_a):
        raise OSError(5, "I/O error")
    monkeypatch.setattr(qs, "_replace", boom)
    assert rank(s, queue, 2, 1).result == qs.FAILED
    assert queue.read_bytes() == before
    assert journal(queue)[-1]["outcome"] == qs.FAILED


# ── §4.5 new items and §4.4 the journal read ─────────────────────────────────

def test_create_item_takes_the_next_id_under_the_lock_and_is_journaled(queue):
    s = store(queue)
    made = s.create_item("Board photo notes", "design", principal="robert", idempotency_key="new-item-0001")
    assert made.ok and made.item_id == 6 and made.verified and made.receipt_id
    it = items(queue)[6]
    assert it["spec_title"] == "Board photo notes" and it["status"] == "design" and it["last_transition_at"]
    again = s.create_item("Board photo notes", "design", principal="robert", idempotency_key="new-item-0001")
    assert again.repeated and again.receipt_id == made.receipt_id and again.item_id == 6
    assert len(items(queue)) == 6                                         # the retry added nothing
    assert s.item_journal(6) == [{**s.item_journal(6)[0], "op": "create", "from": None, "to": "design",
                                  "principal": "robert"}]
    assert s.create_item("Next", "idea", principal="r").item_id == 7


@pytest.mark.parametrize("title,st", [("", "idea"), ("x" * 201, "idea"), ("Fine", "in_build"),
                                      ("Fine", "rework"), ("Fine", "done")])
def test_create_item_refuses_bad_titles_and_late_statuses(queue, title, st):
    s = store(queue)
    before = queue.read_bytes()
    assert s.create_item(title, st, principal="r").result == qs.INVALID
    assert queue.read_bytes() == before


def test_the_item_journal_is_the_real_principal_and_receipt(queue):
    s = store(queue)
    done = status(s, queue, 2, "in_build", key="journal-read-01")
    entry = s.item_journal(2)[-1]
    assert entry == {"from": "spec_ready", "to": "in_build", "op": "status", "principal": "robert", "via": "test",
                     "at": entry["at"], "receipt_id": done.receipt_id, "recovered": False,
                     "reason_from": None, "reason_to": None, "reason_edit": False}
    assert s.item_journal(1) == []

"""The queue store is reconciled once when /guild-next registers (spec §6.3
"at store start", review F10): a Save a crash left half-done shows as a
Check before the next write. Best effort: a failure is logged, never fatal."""
from __future__ import annotations

import json
import logging

from domains.guild import queue_store as qs

from floor_helpers import write_queue
from floor_helpers import load_portal  # noqa: F401  (pytest fixtures)


def _journal(path, lines):
    (path.parent / qs.JOURNAL_NAME).write_text("".join(json.dumps(l) + "\n" for l in lines))


def _read(path):
    return [json.loads(l) for l in (path.parent / qs.JOURNAL_NAME).read_text().splitlines() if l.strip()]


def test_an_unfinished_save_becomes_a_check_at_mount(load_portal, tmp_path):
    queue = write_queue(tmp_path / "state" / "guild" / "build_queue.json")
    op_id = "e" * 32
    _journal(queue, [{"kind": "intent", "op_id": op_id, "op": "status", "item_id": 12, "from": "in_build",
                      "to": "done", "before_digest": "1" * 64, "after_digest": "2" * 64,
                      "at": "2026-09-27T10:00:00+00:00"}])
    portal = load_portal(queue=queue)
    assert portal.module.GUILD_MOUNTS["guild_next"] == "on"
    outcome = [l for l in _read(queue) if l.get("op_id") == op_id and l.get("kind") == "outcome"]
    assert outcome and outcome[0]["outcome"] == "uncertain"
    needs = portal.owner().get("/guild-next/api/v1/floor").get_json()["needs"]
    assert any(n["tag"] == "Check" and n["op_id"] == op_id for n in needs["items"])


def test_a_save_that_did_land_is_recorded_as_completed_at_mount(load_portal, tmp_path):
    queue = write_queue(tmp_path / "state" / "guild" / "build_queue.json")
    digest = qs.sha256_bytes(queue.read_bytes())
    op_id = "f" * 32
    _journal(queue, [{"kind": "intent", "op_id": op_id, "op": "status", "item_id": 12, "from": "in_build",
                      "to": "done", "before_digest": "1" * 64, "after_digest": digest,
                      "at": "2026-09-27T10:00:00+00:00"}])
    load_portal(queue=queue)
    completed = [l for l in _read(queue) if l.get("op_id") == op_id and l.get("kind") == "completed"]
    assert completed and completed[0]["recovered"] is True


def test_a_failed_reconcile_is_logged_and_never_stops_the_mount(load_portal, monkeypatch, caplog):
    def broken(self):
        raise OSError(5, "I/O error")
    monkeypatch.setattr(qs.QueueStore, "reconcile", broken)
    with caplog.at_level(logging.ERROR, logger="minimoi_portal.guild_mounts"):
        portal = load_portal()
    assert portal.module.GUILD_MOUNTS["guild_next"] == "on"
    assert portal.owner().get("/guild-next/api/v1/floor").status_code == 200
    assert any("reconcile at start failed" in r.getMessage() for r in caplog.records)


def test_a_clean_journal_is_left_as_it_is(load_portal):
    portal = load_portal()
    assert not (portal.queue_path.parent / qs.JOURNAL_NAME).exists()

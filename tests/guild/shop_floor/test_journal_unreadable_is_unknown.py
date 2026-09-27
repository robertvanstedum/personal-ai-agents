"""An unreadable journal is unknown, never "Nothing needs you" (review F3):
an uncertain Save may be waiting in it."""
from __future__ import annotations

import os

import pytest

from domains.guild import queue_store as qs

from floor_helpers import load_portal  # noqa: F401  (pytest fixtures)

TEXT = "Needs you · unknown — journal unreadable"


def _calm(load_portal):
    return load_portal(items=[{"id": 1, "spec_title": "x", "status": "in_build",
                               "last_transition_at": "2026-09-20T10:00:00+00:00"}])


def _assert_unknown(client):
    floor = client.get("/guild-next/api/v1/floor").get_json()
    needs = floor["needs"]
    assert needs["status"] == "unknown" and needs["total"] is None
    assert needs["text"] == TEXT and "journal unreadable" in needs["error"]
    assert "needs you unknown" in floor["briefing"]["text"]
    assert "nothing needs you" not in floor["briefing"]["text"]
    page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert TEXT in page and "Nothing needs you" not in page
    queue = client.get("/guild-next/api/v1/queue").get_json()
    assert queue["checks"] is None and queue["checks_source"]["status"] == "unknown"
    assert queue["status"] == "ok"   # the queue itself still reads
    for url in ("/guild-next/guild/build/queue", "/guild-next/guild/build/items/1"):
        body = client.get(url).get_data(as_text=True)
        assert "Checks unknown — journal unreadable" in body, url
    bench = client.get("/guild-next/guild/build/bench").get_data(as_text=True)
    assert TEXT in bench


def test_a_journal_read_error_is_unknown(load_portal, monkeypatch):
    portal = _calm(load_portal)
    client = portal.owner()
    assert client.get("/guild-next/api/v1/floor").get_json()["needs"]["text"] == "Nothing needs you"

    def unreadable(self):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(qs.QueueStore, "unresolved_checks", unreadable)
    _assert_unknown(client)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads any file")
def test_a_journal_without_read_permission_is_unknown(load_portal):
    portal = _calm(load_portal)
    journal = portal.queue_path.parent / qs.JOURNAL_NAME
    journal.write_text("")
    journal.chmod(0)
    try:
        _assert_unknown(portal.owner())
    finally:
        journal.chmod(0o644)


def test_a_missing_journal_is_a_real_zero(load_portal):
    portal = _calm(load_portal)
    assert not (portal.queue_path.parent / qs.JOURNAL_NAME).exists()
    needs = portal.owner().get("/guild-next/api/v1/floor").get_json()["needs"]
    assert needs["status"] == "ok" and needs["total"] == 0 and needs["text"] == "Nothing needs you"

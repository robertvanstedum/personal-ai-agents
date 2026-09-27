"""The verified Save through the API (spec §6, S11; walkthrough W5): through
domains.guild.queue_store, with a receipt equal to the journal's, a conflict
that returns the current item, and every other result in plain words."""
from __future__ import annotations

import json
import threading

from domains.guild import queue_store as qs

from floor_helpers import write_headers
from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

URL = "/guild-next/api/v1/queue/items/{}/status"


def _digest(portal, item_id):
    item = next(i for i in qs.QueueStore(str(portal.queue_path)).read_items() if i["id"] == item_id)
    return qs.item_digest(item)


def _save(portal, client, token, item_id=12, to="done", digest=None, key=None, note=None):
    body = {"to": to, "expect_item_digest": digest or _digest(portal, item_id)}
    if key:
        body["idempotency_key"] = key
    if note is not None:
        body["note"] = note
    return client.post(URL.format(item_id), json=body, headers=write_headers(token))


def test_save_is_verified_with_the_journal_receipt(staging):
    client = staging.owner()
    token = staging.csrf(client)
    response = _save(staging, client, token, key="first-save-key")
    assert response.status_code == 200
    body = response.get_json()
    assert body["result"] == "saved" and body["verified"] is True
    assert body["message"] == f"Saved · verified · receipt {body['receipt_id']}"
    assert body["item"]["status"] == "done" and body["item"]["id"] == 12
    # The file, re-read, has the change; the journal's completed line has the same receipt.
    items = qs.QueueStore(str(staging.queue_path)).read_items()
    saved = next(i for i in items if i["id"] == 12)
    assert saved["status"] == "done"
    assert body["item_digest"] == qs.item_digest(saved)
    journal = staging.journal()
    completed = [l for l in journal if l.get("kind") == "completed"]
    intents = [l for l in journal if l.get("kind") == "intent"]
    assert [c["receipt_id"] for c in completed] == [body["receipt_id"]]
    assert intents[0]["via"] == "guild-next" and intents[0]["principal"] == "robert"
    assert body["audit"] in ("skipped", "none")


def test_repeating_the_same_request_returns_the_same_receipt(staging):
    client = staging.owner()
    token = staging.csrf(client)
    digest = _digest(staging, 12)
    first = _save(staging, client, token, digest=digest, key="repeat-key-123").get_json()
    again = _save(staging, client, token, digest=digest, key="repeat-key-123").get_json()
    assert again["result"] == "saved" and again["receipt_id"] == first["receipt_id"]
    assert len([l for l in staging.journal() if l.get("kind") == "completed"]) == 1


def test_conflict_returns_the_current_item_and_writes_nothing(staging):
    client = staging.owner()
    token = staging.csrf(client)
    stale = _digest(staging, 12)
    assert _save(staging, client, token, to="blocked", note="first").status_code == 200
    before = staging.queue_path.read_bytes()
    response = _save(staging, client, token, to="done", digest=stale)
    assert response.status_code == 409
    body = response.get_json()
    assert body["result"] == "conflict" and body["error"] == "conflict"
    assert body["message"] == ("#12 changed since you opened it. Here is the current state; "
                               "Save again if you still want it")
    assert body["item"]["status"] == "blocked" and body["item"]["blocked_reason"] == "first"
    assert body["item_digest"] == _digest(staging, 12)
    assert staging.queue_path.read_bytes() == before
    # Saving again from the current state works.
    retry = _save(staging, client, token, to="done", digest=body["item_digest"])
    assert retry.get_json()["result"] == "saved"


def test_a_change_to_another_item_is_not_a_conflict(staging):
    client = staging.owner()
    token = staging.csrf(client)
    digest_12 = _digest(staging, 12)
    assert _save(staging, client, token, item_id=7, to="in_build").status_code == 200
    assert _save(staging, client, token, item_id=12, to="done", digest=digest_12).status_code == 200


def test_busy_lock_answers_503_with_retry_and_writes_nothing(staging):
    services = staging.app.extensions["guild_ui_next"]["services"]
    services.store.lock_timeout_s = 0.2
    holder = qs.QueueStore(str(staging.queue_path))
    held = holder._acquire()
    try:
        client = staging.owner()
        token = staging.csrf(client)
        before = staging.queue_path.read_bytes()
        result = {}

        def run():
            result["r"] = _save(staging, client, token)
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(10)
        response = result["r"]
        assert response.status_code == 503
        body = response.get_json()
        assert body["result"] == "busy" and body["retry_after_s"] == 5
        assert response.headers["Retry-After"] == "5"
        assert staging.queue_path.read_bytes() == before
    finally:
        holder._release(held)


def test_invalid_not_found_and_stale_page(staging):
    client = staging.owner()
    token = staging.csrf(client)
    bad = client.post(URL.format(12), json={"to": "shipped", "expect_item_digest": _digest(staging, 12)},
                      headers=write_headers(token))
    assert bad.status_code == 422 and bad.get_json()["result"] == "invalid"
    missing = client.post(URL.format(999), json={"to": "done", "expect_item_digest": "0" * 64},
                          headers=write_headers(token))
    assert missing.status_code == 404 and missing.get_json()["message"] == "#999 is not in the queue. Nothing was saved"
    stale = client.post(URL.format(12), json={"to": "done"}, headers=write_headers(token))
    assert stale.status_code == 422 and "out of date" in stale.get_json()["message"]
    assert staging.journal() == []


def test_unreadable_queue_saves_nothing_and_says_so(staging):
    client = staging.owner()
    token = staging.csrf(client)
    digest = _digest(staging, 12)
    staging.queue_path.write_text("{ not json")
    response = _save(staging, client, token, digest=digest)
    assert response.status_code == 503
    assert response.get_json()["message"] == "The queue can't be read, so nothing was saved"
    assert staging.queue_path.read_text() == "{ not json"


def test_audit_failure_is_reported_never_a_condition(staging):
    services = staging.app.extensions["guild_ui_next"]["services"]

    def failing_audit(*_a):
        raise RuntimeError("database down")

    services.audit = failing_audit
    client = staging.owner()
    token = staging.csrf(client)
    body = _save(staging, client, token).get_json()
    assert body["result"] == "saved" and body["audit"] == "failed"
    assert body["message"].endswith("· history not recorded")


def test_uncertain_save_becomes_a_check_in_needs_you_and_can_be_marked(staging):
    op_id = "d" * 32
    journal = staging.queue_path.parent / qs.JOURNAL_NAME
    lines = [
        {"kind": "intent", "op_id": op_id, "op": "status", "item_id": 12, "from": "in_build", "to": "done",
         "before_digest": "1" * 64, "after_digest": "2" * 64, "at": "2026-09-27T10:00:00+00:00"},
        {"kind": "outcome", "op_id": op_id, "outcome": "uncertain", "item_id": 12,
         "at": "2026-09-27T10:00:01+00:00"},
    ]
    journal.write_text("".join(json.dumps(l) + "\n" for l in lines))
    client = staging.owner()
    floor = client.get("/guild-next/api/v1/floor").get_json()
    check = floor["needs"]["items"][0]
    assert check["tag"] == "Check" and check["item_id"] == 12 and check["op_id"] == op_id
    assert "Check #12" in floor["briefing"]["text"]
    page = client.get("/guild-next/guild/build/items/12").get_data(as_text=True)
    assert f'data-mark-checked="{op_id}"' in page
    token = staging.csrf(client)
    marked = client.post(f"/guild-next/api/v1/queue/journal/{op_id}/checked", json={},
                         headers=write_headers(token))
    assert marked.status_code == 200 and marked.get_json()["result"] == "checked"
    again = client.post(f"/guild-next/api/v1/queue/journal/{op_id}/checked", json={},
                        headers=write_headers(token))
    assert again.status_code == 404
    floor = client.get("/guild-next/api/v1/floor").get_json()
    assert all(i["tag"] != "Check" for i in floor["needs"]["items"])


def test_the_page_renders_the_digest_the_api_checks(staging):
    page = staging.owner().get("/guild-next/guild/build/queue").get_data(as_text=True)
    assert f'data-digest="{_digest(staging, 12)}"' in page
    assert 'data-item-id="12"' in page


def test_a_reused_key_for_a_different_change_is_refused_not_given_the_old_receipt(staging):
    client = staging.owner()
    token = staging.csrf(client)
    digest = _digest(staging, 12)
    first = _save(staging, client, token, to="done", digest=digest, key="reused-key-0001").get_json()
    assert first["result"] == "saved"
    before = staging.queue_path.read_bytes()
    other = _save(staging, client, token, to="deferred", digest=first["item_digest"], key="reused-key-0001")
    assert other.status_code == 409
    body = other.get_json()
    assert body["result"] == "idempotency_mismatch" and body["receipt_id"] is None
    assert body["message"].startswith("This form was already used to save a different change")
    assert staging.queue_path.read_bytes() == before


def test_an_os_error_while_writing_is_failed_and_the_queue_is_unchanged(staging, monkeypatch):
    client = staging.owner()
    token = staging.csrf(client)
    before = staging.queue_path.read_bytes()

    def no_space(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(qs, "_replace", no_space)
    response = _save(staging, client, token)
    assert response.status_code == 500
    body = response.get_json()
    assert body["result"] == "failed" and body["verified"] is False and body["receipt_id"] is None
    assert body["message"] == "The queue could not be written, so nothing was saved. The live queue is unchanged"
    assert staging.queue_path.read_bytes() == before

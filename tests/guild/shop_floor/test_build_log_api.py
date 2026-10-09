"""Guild 1.1 slice 2 (spec §4, §11): the Build Log page and its API. The
queue read with ?scope=all, the rank write behind the collection digest, the
file journal per item, new items, and rework through the existing guarded
Save; Needs you, the lights and the legacy pages learn rework. Every write
keeps the guards; nothing here calls a model or the network."""
from __future__ import annotations

import json
import sys

import pytest

from domains.guild import queue_store as qs

from floor_helpers import QUEUE_ITEMS, write_headers
from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)
from test_phone_briefing_no_paid_calls import (PROVIDER_MODULES, _mock_probe, assert_no_outbound,  # noqa: F401
                                               no_outbound)

API = "/guild-next/api/v1"


def _store(portal):
    return qs.QueueStore(str(portal.queue_path))


def _items(portal):
    data = json.loads(portal.queue_path.read_text())
    assert isinstance(data, list)                                   # the file stays a bare list
    return {i["id"]: i for i in data}


def _queue(client, scope="all"):
    return client.get(f"{API}/queue?scope={scope}").get_json()


def _rank(portal, client, token, item_id, rank, key, digest=None, mode="on_record"):
    body = {"rank": rank, "expect_rank_digest": digest or _queue(client)["rank_digest"], "idempotency_key": key}
    return client.post(f"{API}/queue/items/{item_id}/rank", json=body, headers=write_headers(token, mode=mode))


def _status(portal, client, token, item_id, to, note=None, key=None):
    body = {"to": to, "expect_item_digest": qs.item_digest(_items(portal)[item_id]),
            "idempotency_key": key or f"st-{item_id}-{to}-{len(note or '')}x"}
    if note is not None:
        body["note"] = note
    return client.post(f"{API}/queue/items/{item_id}/status", json=body, headers=write_headers(token))


# ── the page and the read ─────────────────────────────────────────────────────

def test_the_build_log_page_embeds_every_item_and_is_owner_only(staging):
    r = staging.owner().get("/guild-next/guild/build/log")
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "data-bl-matrix" in page and 'data-page="build_log"' in page
    start = page.index('<script type="application/json" id="build-log-data">') + len(
        '<script type="application/json" id="build-log-data">')
    data = json.loads(page[start:page.index("</script>", start)])
    assert sorted(i["id"] for i in data["items"]) == sorted(i["id"] for i in QUEUE_ITEMS)   # done included
    assert data["status"] == "ok" and len(data["rank_digest"]) == 64 and "rework" in data["statuses"]
    for view in ("next", "ready", "progress", "trouble", "roadmap", "all"):
        assert f'data-bl-view="{view}"' in page
    for client in (staging.guest(), staging.client()):
        assert client.get("/guild-next/guild/build/log").status_code in (302, 403)


def test_an_unreadable_queue_makes_the_build_log_unknown_not_empty(load_portal):
    portal = load_portal()
    portal.queue_path.write_text("{ not json")
    page = portal.owner().get("/guild-next/guild/build/log").get_data(as_text=True)
    assert "data-bl-unknown" in page and "<strong>unknown</strong>" in page
    assert '"items": null' in page and '"status": "unknown"' in page


def test_scope_all_active_and_the_unchanged_default(staging):
    client = staging.owner()
    every = _queue(client)
    assert every["scope"] == "all" and {i["id"] for i in every["items"]} == {7, 12, 31, 40}
    assert every["total"] == 4 and len(every["rank_digest"]) == 64
    active = _queue(client, "active")
    assert {i["id"] for i in active["items"]} == {7, 12}
    default = client.get(f"{API}/queue").get_json()
    assert {i["id"] for i in default["items"]} == {7, 12, 31, 40}           # as before slice 2 (compatibility)
    assert client.get(f"{API}/queue?scope=done").status_code == 422
    row = next(i for i in every["items"] if i["id"] == 12)
    for key in ("priority", "notes", "owner_rank", "spec_author", "trouble", "trouble_reason", "trouble_from"):
        assert key in row, key
    assert row["spec_author"] == ""                                           # no author line: blank


# ── rank ──────────────────────────────────────────────────────────────────────

def test_rank_is_saved_with_a_receipt_and_a_retry_returns_it(staging):
    client = staging.owner()
    token = staging.csrf(client)
    digest = _queue(client)["rank_digest"]
    first = _rank(staging, client, token, 12, 1, "rank-api-0001", digest)
    body = first.get_json()
    assert first.status_code == 200 and body["result"] == "saved" and body["receipt_id"]
    assert body["ranking"] == [[12, 1]] and body["rank_digest"] != digest
    assert _items(staging)[12]["owner_rank"] == 1
    again = _rank(staging, client, token, 12, 1, "rank-api-0001", digest).get_json()
    assert again["repeated"] and again["receipt_id"] == body["receipt_id"]
    assert [r["kind"] for r in staging.journal()] == ["intent", "completed"]


def test_a_second_device_with_an_old_ranking_gets_409_and_the_current_ranking(staging):
    client = staging.owner()
    token = staging.csrf(client)
    seen = _queue(client)["rank_digest"]
    assert _rank(staging, client, token, 12, 1, "device-a-0001", seen).status_code == 200
    before = staging.queue_path.read_bytes()
    other = _rank(staging, client, token, 7, 1, "device-b-0001", seen)
    assert other.status_code == 409
    body = other.get_json()
    assert body["result"] == "conflict" and body["ranking"] == [[12, 1]]
    assert body["rank_digest"] == _queue(client)["rank_digest"]
    assert staging.queue_path.read_bytes() == before


@pytest.mark.parametrize("rank", [0, -1, 1.5, True, "2", 9007199254740992])
def test_a_bad_rank_is_refused(staging, rank):
    client = staging.owner()
    token = staging.csrf(client)
    assert _rank(staging, client, token, 12, rank, "bad-rank-0001").status_code == 422
    assert staging.journal() == []


def test_rank_writes_keep_every_guard(staging):
    client = staging.owner()
    token = staging.csrf(client)
    before = staging.queue_path.read_bytes()
    assert _rank(staging, client, token, 12, 1, "off-record-01", mode="off_record").status_code == 409
    body = {"rank": 1, "expect_rank_digest": _queue(client)["rank_digest"], "idempotency_key": "no-csrf-0001"}
    assert client.post(f"{API}/queue/items/12/rank", json=body, headers={"X-Record-Mode": "on_record"}).status_code == 403
    assert client.post(f"{API}/queue/items/12/rank", json={"rank": 1}, headers=write_headers(token)).status_code == 422
    assert staging.guest().post(f"{API}/queue/items/12/rank", json=body).status_code in (401, 403)
    assert staging.queue_path.read_bytes() == before and staging.journal() == []


# ── rework through the existing Save, Needs you, lights, the journal ──────────

def test_rework_needs_a_reason_and_shows_in_needs_you_and_the_lights(staging):
    client = staging.owner()
    token = staging.csrf(client)
    refused = _status(staging, client, token, 12, "rework")
    assert refused.status_code == 422 and "reason" in refused.get_json()["message"]
    done = _status(staging, client, token, 12, "rework", "review found the drawer hides history")
    body = done.get_json()
    assert done.status_code == 200 and body["item"]["status"] == "rework" and body["item"]["trouble"]
    assert body["item"]["trouble_from"] == "in_build"
    floor = client.get(f"{API}/floor").get_json()
    rows = [(n["tag"], n["item_id"]) for n in floor["needs"]["items"]]
    assert ("Rework", 12) in rows and ("Decide", 31) in rows
    light = {l["id"]: l for l in floor["lights"]}["build_queue"]
    assert light["state"] == "red"                                         # #31 is still blocked: red wins
    assert any("1 in rework" in d for d in light["detail"])


def test_rework_alone_turns_the_queue_light_amber(load_portal):
    items = [dict(i) for i in QUEUE_ITEMS if i["status"] != "blocked"]
    items.append({"id": 33, "spec_title": "Sent back", "status": "rework", "trouble_reason": "fix it",
                  "trouble_from": "in_build", "last_transition_at": "2026-09-29T10:00:00+00:00"})
    floor = load_portal(items=items).owner().get(f"{API}/floor").get_json()
    light = {l["id"]: l for l in floor["lights"]}["build_queue"]
    assert light["state"] == "yellow" and "1 in rework" in light["word"] + " ".join(light["detail"])
    assert [n["tag"] for n in floor["needs"]["items"]] == ["Rework"]


def test_the_journal_endpoint_shows_the_real_principal_and_receipts(staging):
    client = staging.owner()
    token = staging.csrf(client)
    saved = _status(staging, client, token, 12, "blocked", "waiting on the key").get_json()
    _status(staging, client, token, 12, "blocked", "waiting on Robert")                 # a reason edit
    j = client.get(f"{API}/queue/items/12/journal").get_json()
    assert j["status"] == "ok" and len(j["journal"]) == 2
    first, edit = j["journal"]
    assert first["principal"] == "robert" and first["via"] == "guild-next" and first["receipt_id"] == saved["receipt_id"]
    assert (first["from"], first["to"]) == ("in_build", "blocked")
    assert edit["reason_edit"] and edit["reason_from"] == "waiting on the key" and edit["reason_to"] == "waiting on Robert"
    assert client.get(f"{API}/queue/items/7/journal").get_json()["journal"] == []


def test_an_unreadable_journal_is_unknown_never_empty(staging, monkeypatch):
    def boom(self, item_id):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(qs.QueueStore, "item_journal", boom)
    j = staging.owner().get(f"{API}/queue/items/12/journal").get_json()
    assert j["status"] == "unknown" and j["journal"] is None


# ── new items ─────────────────────────────────────────────────────────────────

def test_a_new_item_goes_through_the_store_with_the_next_id(staging):
    client = staging.owner()
    token = staging.csrf(client)
    body = {"spec_title": "Board photo notes", "status": "idea", "idempotency_key": "new-api-00001"}
    r = client.post(f"{API}/queue/items", json=body, headers=write_headers(token))
    out = r.get_json()
    assert r.status_code == 200 and out["result"] == "saved" and out["item_id"] == 41 and out["receipt_id"]
    assert out["item"]["title"] == "Board photo notes" and out["item"]["status"] == "idea"
    again = client.post(f"{API}/queue/items", json=body, headers=write_headers(token)).get_json()
    assert again["repeated"] and again["receipt_id"] == out["receipt_id"] and again["item_id"] == 41
    assert len(_items(staging)) == 5
    for bad in ({"spec_title": "", "status": "idea"}, {"spec_title": "x", "status": "in_build"},
                {"spec_title": "x" * 201, "status": "idea"}):
        assert client.post(f"{API}/queue/items", json={**bad, "idempotency_key": "new-bad-0001"},
                           headers=write_headers(token)).status_code == 422
    assert client.post(f"{API}/queue/items", json={**body, "idempotency_key": "new-off-0001"},
                       headers=write_headers(token, mode="off_record")).status_code == 409
    assert len(_items(staging)) == 5


# ── the legacy pages learn rework ─────────────────────────────────────────────

def test_legacy_pages_show_rework_instead_of_failing(load_portal):
    items = [dict(i) for i in QUEUE_ITEMS] + [{"id": 33, "spec_title": "Sent back", "status": "rework",
                                              "trouble_reason": "fix it", "last_transition_at": "2026-09-29T10:00:00+00:00"}]
    client = load_portal(items=items).owner()
    page = client.get("/guild/build?status=rework")
    assert page.status_code == 200 and "Sent back" in page.get_data(as_text=True)
    active = client.get("/guild/build").get_data(as_text=True)
    assert "Sent back" in active                                            # rework is not terminal
    queue = client.get("/guild/build/queue").get_data(as_text=True)
    assert 'value="rework"' in queue


# ── no model calls ────────────────────────────────────────────────────────────

def test_the_build_log_and_its_writes_make_no_model_or_network_call(load_portal, no_outbound):
    portal = load_portal(ops_url="http://ops.invalid:8768/status")
    _mock_probe(portal)
    client = portal.owner()
    token = portal.csrf(client)
    before = set(sys.modules)
    assert client.get("/guild-next/guild/build/log").status_code == 200
    assert client.get(f"{API}/queue?scope=all").status_code == 200
    assert _rank(portal, client, token, 12, 1, "no-model-0001").status_code == 200
    assert _status(portal, client, token, 7, "rework", "needs another pass").status_code == 200
    assert client.get(f"{API}/queue/items/7/journal").status_code == 200
    assert client.post(f"{API}/queue/items", json={"spec_title": "Quiet", "idempotency_key": "no-model-0002"},
                       headers=write_headers(token)).status_code == 200
    new = set(sys.modules) - before
    assert not [m for m in new if m.split(".")[0] in {p.split(".")[0] for p in PROVIDER_MODULES}]
    assert_no_outbound(no_outbound)


def test_shared_high_rank_round_trips_without_displacing_items(staging):
    client = staging.owner()
    token = staging.csrf(client)
    for item in (12, 7):
        assert _rank(staging, client, token, item, 12, f"group-rank-{item:04}").status_code == 200
    assert _items(staging)[12]["owner_rank"] == 12
    assert _items(staging)[7]["owner_rank"] == 12

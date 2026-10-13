"""The Board's click-to-edit and the change signal a second window (the pop-out) uses.

* POST /postits/<id>/text edits what a note says, behind the note's version, with the same
  guards, scrub and length rule as an add; a stale version is a conflict that changes nothing.
* GET /board/rev is a short string that differs after ANY change to the Board (add, edit,
  label, done, bin, restore, reorder) and is the same when nothing changed.
Both are owner-only. No model or network call."""
from __future__ import annotations

import pytest

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
CARD = "5555 5555 5555 4444"


def _post(client, token, path, mode="on_record", **body):
    return client.post(f"{API}{path}", json=keyed(**body), headers=write_headers(token, mode=mode))


def _add(client, token, text="a note", **kw):
    r = _post(client, token, "/postits", text=text, **kw)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["postit"]


def _board(client):
    return client.get(f"{API}/board").get_json()


def _rev(client):
    r = client.get(f"{API}/board/rev")
    assert r.status_code == 200, r.get_json()
    return r.get_json()["rev"]


def _edit(client, token, note, text, **extra):
    return _post(client, token, f"/postits/{note['id']}/text", text=text, version=note["version"], **extra)


def test_an_edit_changes_the_text_and_bumps_the_version(floored):
    client = floored.owner(); token = floored.csrf(client)
    note = _add(client, token, "first thought")
    r = _edit(client, token, note, "second thought")
    body = r.get_json()
    assert r.status_code == 200 and body["result"] == "edited" and body["postit"]["text"] == "second thought"
    assert body["postit"]["version"] == note["version"] + 1 and body["message"] == "Saved"
    assert [n["text"] for n in _board(client)["active"]] == ["second thought"]


def test_a_stale_version_is_a_conflict_that_changes_nothing(floored):
    client = floored.owner(); token = floored.csrf(client)
    note = _add(client, token, "first")
    assert _edit(client, token, note, "second").status_code == 200
    stale = _edit(client, token, note, "third")                      # still carries the old version
    body = stale.get_json()
    assert stale.status_code == 409 and body["error"] == "conflict" and body["postit"]["text"] == "second"
    assert [n["text"] for n in _board(client)["active"]] == ["second"]


def test_the_same_key_replays_and_a_different_change_with_it_is_refused(floored):
    client = floored.owner(); token = floored.csrf(client)
    note = _add(client, token, "first")
    first = _post(client, token, f"/postits/{note['id']}/text", text="edited", version=note["version"], idempotency_key="k" * 16)
    again = _post(client, token, f"/postits/{note['id']}/text", text="edited", version=note["version"], idempotency_key="k" * 16)
    assert first.status_code == 200 and again.status_code == 200 and again.get_json()["repeated"] is True
    assert again.get_json()["postit"]["version"] == first.get_json()["postit"]["version"]
    other = _post(client, token, f"/postits/{note['id']}/text", text="something else", version=note["version"], idempotency_key="k" * 16)
    assert other.status_code == 409 and other.get_json()["error"] == "idempotency_mismatch"
    assert [n["text"] for n in _board(client)["active"]] == ["edited"]


@pytest.mark.parametrize("text,why", [("", "empty"), ("   ", "blank"), ("x" * 281, "too long"), (None, "missing")],
                         ids=["empty", "blank", "too-long", "missing"])
def test_empty_or_too_long_text_is_refused_and_changes_nothing(floored, text, why):
    client = floored.owner(); token = floored.csrf(client)
    note = _add(client, token, "keep me")
    r = _edit(client, token, note, text)
    assert r.status_code == 422, why
    assert [n["text"] for n in _board(client)["active"]] == ["keep me"]


def test_payment_details_are_scrubbed_like_an_add(floored):
    client = floored.owner(); token = floored.csrf(client)
    note = _add(client, token, "plain")
    r = _edit(client, token, note, f"pay with {CARD} today")
    assert r.status_code == 200 and CARD not in r.get_json()["postit"]["text"]
    assert CARD not in str(_board(client))


def test_the_same_text_is_unchanged_and_a_binned_note_must_be_restored_first(floored):
    client = floored.owner(); token = floored.csrf(client)
    note = _add(client, token, "same")
    same = _edit(client, token, note, "same")
    assert same.get_json()["result"] == "unchanged" and same.get_json()["postit"]["version"] == note["version"]
    assert _post(client, token, f"/postits/{note['id']}/bin").status_code == 200
    binned = _board(client)["trash"][0]
    r = _post(client, token, f"/postits/{note['id']}/text", text="edit while binned", version=binned["version"])
    assert r.status_code == 409 and r.get_json()["error"] == "in_trash"
    assert _board(client)["trash"][0]["text"] == "same"


def test_text_edit_is_guarded_like_every_write(floored):
    client = floored.owner(); token = floored.csrf(client)
    note = _add(client, token, "guarded")
    url = f"{API}/postits/{note['id']}/text"
    body = keyed(text="x", version=note["version"])
    assert client.post(url, json=body, headers={"X-Record-Mode": "on_record"}).status_code == 403            # no CSRF token
    assert client.post(url, json=body, headers=write_headers(token, mode="off_record")).status_code == 409   # not on the record
    assert client.post(url, json=keyed(text="x"), headers=write_headers(token)).status_code == 422           # no version
    assert floored.guest().post(url, json=body, headers=write_headers(token)).status_code in (401, 403)
    assert floored.client().post(url, json=body, headers=write_headers(token)).status_code in (401, 403)
    assert [n["text"] for n in _board(client)["active"]] == ["guarded"]


def test_the_change_signal_differs_after_every_kind_of_change_and_not_otherwise(floored):
    client = floored.owner(); token = floored.csrf(client)
    seen = [_rev(client)]
    assert _rev(client) == seen[0]                                   # nothing changed: the same signal
    a = _add(client, token, "alpha"); seen.append(_rev(client))      # add
    b = _add(client, token, "beta"); seen.append(_rev(client))
    r = _edit(client, token, a, "alpha edited"); a = r.get_json()["postit"]; seen.append(_rev(client))                 # edit text
    r = _post(client, token, f"/postits/{a['id']}/label", label="idea", version=a["version"]); a = r.get_json()["postit"]; seen.append(_rev(client))   # label
    r = _post(client, token, f"/postits/{a['id']}/done", version=a["version"]); a = r.get_json()["postit"]; seen.append(_rev(client))                 # done
    assert _post(client, token, f"/postits/{b['id']}/bin").status_code == 200; seen.append(_rev(client))                # bin
    assert _post(client, token, f"/postits/{b['id']}/restore").status_code == 200; seen.append(_rev(client))           # restore
    assert len(set(seen)) == len(seen), seen                         # every step produced a new value
    assert _rev(client) == seen[-1]                                  # and reading it changes nothing


def test_the_change_signal_is_owner_only_and_carries_no_notes(floored):
    client = floored.owner(); token = floored.csrf(client)
    _add(client, token, "private thought")
    body = client.get(f"{API}/board/rev").get_json()
    assert set(body) >= {"rev"} and "private thought" not in str(body) and "active" not in body
    assert floored.guest().get(f"{API}/board/rev").status_code in (401, 403)
    assert floored.client().get(f"{API}/board/rev").status_code == 401

"""Review B1c #1, #2 and #7: every field is capped before the payment scrub
sees it, the new endpoints refuse a body over 32 KB with 413, text that the
scrub makes too long is a 422 in plain words (never "unavailable"), and the
platform's receipt-… keys cannot be taken by a client."""
from __future__ import annotations

import json
import time

from domains.guild import queue_store as qs
from minimoi_portal.guild_ui import api as floor_api
from minimoi_portal.guild_ui.payment_scrub import REMOVED

from floor_helpers import write_headers
from floor_helpers import load_portal  # noqa: F401  (pytest fixture)
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

API = "/guild-next/api/v1"
WRITES = [("POST", "/notes"), ("POST", "/postits"), ("POST", "/postits/1/bin"), ("POST", "/postits/1/restore"),
          ("PUT", "/continue")]


def test_a_one_megabyte_body_is_refused_fast_on_every_new_write(floored):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    big = json.dumps(keyed(text="a@" * 500_000, kind="item", ref=12))
    assert len(big) > 1_000_000
    for method, path in WRITES:
        start = time.perf_counter()
        response = client.open(f"{API}{path}", method=method, data=big, content_type="application/json",
                               headers=write_headers(token))
        assert time.perf_counter() - start < 0.5, path
        assert response.status_code == 413 and response.get_json()["error"] == "too_large", path
    assert db.all_text() == ""


def test_a_body_without_a_length_is_bounded_too(floored):
    """A body sent without Content-Length (chunked) is read only up to the
    limit, then refused. Driven through WSGI directly, as a server passes a
    chunked body (wsgi.input_terminated), which the test client cannot."""
    from werkzeug.test import EnvironBuilder, run_wsgi_app
    client = floored.owner()
    token = floored.csrf(client)
    cookie = client.get_cookie(floored.app.config["SESSION_COOKIE_NAME"])
    payload = json.dumps(keyed(text="x" * 40_000)).encode()
    builder = EnvironBuilder(path=f"{API}/postits", method="POST", data=payload, content_type="application/json",
                             headers={**write_headers(token), "Cookie": f"{cookie.key}={cookie.value}"})
    env = builder.get_environ()
    env.pop("CONTENT_LENGTH", None)
    env["wsgi.input_terminated"] = True
    env["HTTP_TRANSFER_ENCODING"] = "chunked"
    app_iter, status, _headers = run_wsgi_app(floored.app, env)
    body = json.loads(b"".join(app_iter))
    assert status.startswith("413") and body["error"] == "too_large", (status, body)
    assert floored.extra["floor"].count("floor_postits") == 0


def test_a_32k_context_field_is_cut_before_the_scrub(floored, monkeypatch):
    seen = []
    real = floor_api.scrub
    monkeypatch.setattr(floor_api, "scrub", lambda text: seen.append(len(text)) or real(text))
    client = floored.owner()
    token = floored.csrf(client)
    hostile = "a@" * 16_000                      # 32,000 characters, the email rule's worst case
    start = time.perf_counter()
    response = client.post(f"{API}/notes", json=keyed(text="short", context={"area": hostile, "page": "p" * 100}),
                           headers=write_headers(token))
    assert time.perf_counter() - start < 0.5
    assert response.status_code == 200, response.get_json()
    assert max(seen) <= 60                        # nothing longer than a field's limit reached the scrub
    note = response.get_json()["note"]
    assert len(note["context"]["area"]) <= 60 and len(note["context"]["page"]) <= 40


def test_over_long_text_is_refused_before_the_scrub(floored, monkeypatch):
    seen = []
    real = floor_api.scrub
    monkeypatch.setattr(floor_api, "scrub", lambda text: seen.append(len(text)) or real(text))
    client = floored.owner()
    token = floored.csrf(client)
    assert client.post(f"{API}/postits", json=keyed(text="a@" * 5_000), headers=write_headers(token)).status_code == 422
    assert client.post(f"{API}/notes", json=keyed(text="a@" * 5_000), headers=write_headers(token)).status_code == 422
    assert seen == []


def test_a_post_it_the_scrub_makes_too_long_is_a_422_not_an_outage(floored):
    db = floored.extra["floor"]
    client = floored.owner()
    token = floored.csrf(client)
    text = " ".join(f"see a{n}@ex.io" for n in range(12))[:140]   # 140 characters of emails between words
    assert len(text) <= 140
    response = client.post(f"{API}/postits", json=keyed(text=text), headers=write_headers(token))
    body = response.get_json()
    assert response.status_code == 422 and body["error"] == "invalid"
    assert "too long after removing payment details" in body["message"]
    assert "unavailable" not in body["message"].lower()
    assert db.count("floor_postits") == 0
    assert client.get(f"{API}/floor").get_json()["postits"]["state"] == "ok"   # the store is fine


def test_a_note_the_scrub_makes_too_long_is_a_422(floored):
    client = floored.owner()
    token = floored.csrf(client)
    text = " ".join(f"w a{n}@ex.io" for n in range(400))[:2000]
    response = client.post(f"{API}/notes", json=keyed(text=text), headers=write_headers(token))
    assert response.status_code == 422 and "too long after removing payment details" in response.get_json()["message"]


def test_adjacent_removals_read_as_one(floored):
    client = floored.owner()
    token = floored.csrf(client)
    text = " ".join(f"a{n}@ex.io" for n in range(10))
    body = client.post(f"{API}/postits", json=keyed(text=text), headers=write_headers(token)).get_json()
    assert body["postit"]["text"] == REMOVED


def test_receipt_keys_belong_to_the_platform(floored):
    client = floored.owner()
    token = floored.csrf(client)
    for body in ({"request_id": "receipt-q-20260927T000000Z-abcdef", "text": "x"},
                 {"idempotency_key": "receipt-12345678", "text": "x"}):
        response = client.post(f"{API}/notes", json=body, headers=write_headers(token))
        assert response.status_code == 422 and "receipt-" in response.get_json()["message"]


def test_a_receipt_line_that_is_not_kept_reports_failed(floored, monkeypatch):
    store = floored.app.extensions["guild_ui_next"]["services"].floor
    from minimoi_portal.guild_ui.stores import WriteResult
    monkeypatch.setattr(store, "add_note", lambda *a, **k: WriteResult("idempotency_mismatch", None, True))
    client = floored.owner()
    token = floored.csrf(client)
    item = next(i for i in qs.QueueStore(str(floored.queue_path)).read_items() if i["id"] == 12)
    body = client.post(f"{API}/queue/items/12/status",
                       json={"to": "done", "expect_item_digest": qs.item_digest(item), "idempotency_key": "lim-save-01"},
                       headers=write_headers(token)).get_json()
    assert body["result"] == "saved" and body["notes_line"] == "failed"

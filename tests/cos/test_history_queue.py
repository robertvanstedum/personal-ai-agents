"""Messages Robert sends while CoS works wait in a line kept with the history (Robert, 11 Nov 2026: as MC)."""
from __future__ import annotations

import pytest

from domains.cos.history import QUEUE_MAX, History, HistoryRefused, turn_id_for
from tests.cos.test_history import Clock, send

OWNER = "7"
RID = "11111111-1111-4111-8111-11111111111{}"


@pytest.fixture
def hist(tmp_path):
    folder = tmp_path / "conversations"
    folder.mkdir()
    return History(folder, "dev", clock=Clock())


def test_a_message_waits_in_order_and_only_in_its_own_chat(hist):
    a, b = hist.ensure_default()["conversation_id"], hist.create("Other")["conversation_id"]
    assert hist.queue_add(OWNER, conversation_id=a, request_id=RID.format(1), text="first")[0] == "queued"
    hist.queue_add(OWNER, conversation_id=b, request_id=RID.format(2), text="elsewhere")
    _, items = hist.queue_add(OWNER, conversation_id=a, request_id=RID.format(3), text="second",
                              attachments=[{"file_id": "f1", "name": "plan.pdf"}])
    assert [i["text"] for i in items] == ["first", "second"]
    assert items[1]["attachments"] == [{"file_id": "f1", "name": "plan.pdf"}]
    assert [i["text"] for i in hist.queue(OWNER)] == ["first", "elsewhere", "second"]
    assert hist.queue("someone-else") == []


def test_a_repeated_add_is_the_same_entry_and_a_changed_one_is_refused(hist):
    a = hist.ensure_default()["conversation_id"]
    hist.queue_add(OWNER, conversation_id=a, request_id=RID.format(1), text="first")
    assert hist.queue_add(OWNER, conversation_id=a, request_id=RID.format(1), text="first")[0] == "replayed"
    with pytest.raises(HistoryRefused) as e:
        hist.queue_add(OWNER, conversation_id=a, request_id=RID.format(1), text="changed")
    assert e.value.code == "request_mismatch" and len(hist.queue(OWNER)) == 1


def test_the_line_holds_five_and_refuses_what_it_cannot_keep(hist):
    a = hist.ensure_default()["conversation_id"]
    for n in range(QUEUE_MAX):
        hist.queue_add(OWNER, conversation_id=a, request_id=RID.format(n), text=f"m{n}")
    for kwargs, code in ((dict(request_id=RID.format(9), text="six"), "queue_full"),):
        with pytest.raises(HistoryRefused) as e:
            hist.queue_add(OWNER, conversation_id=a, **kwargs)
        assert e.value.code == code
    hist.queue_remove(OWNER, RID.format(0))
    for kwargs, code in ((dict(request_id=RID.format(9), text=""), "empty"),
                         (dict(request_id="not-a-uuid", text="x"), "invalid_request"),
                         (dict(request_id=RID.format(9), text="x" * 20_001), "too_long")):
        with pytest.raises(HistoryRefused) as e:
            hist.queue_add(OWNER, conversation_id=a, **kwargs)
        assert e.value.code == code
    with pytest.raises(HistoryRefused) as e:
        hist.queue_add(OWNER, conversation_id="cosc-000000000000", request_id=RID.format(9), text="x")
    assert e.value.code == "conversation_unavailable"


def test_a_message_already_sent_cannot_be_queued_again_and_removal_is_exact(hist):
    a = hist.ensure_default()["conversation_id"]
    hist.begin(send(a, turn_id_for(RID.format(1))))
    with pytest.raises(HistoryRefused) as e:
        hist.queue_add(OWNER, conversation_id=a, request_id=RID.format(1), text="again")
    assert e.value.code == "already_sent"
    hist.queue_add(OWNER, conversation_id=a, request_id=RID.format(2), text="keep")
    hist.queue_add(OWNER, conversation_id=a, request_id=RID.format(3), text="drop")
    assert hist.queue_remove(OWNER, RID.format(3)) is True
    assert hist.queue_remove(OWNER, RID.format(3)) is False
    assert [i["text"] for i in hist.queue(OWNER, a)] == ["keep"]


# ---- the routes ----------------------------------------------------------------------------------------------------

from tests.cos.test_history_wiring import OWNER as OWNER_HEADERS, cos  # noqa: E402,F401  (fixture)

QUEUED = "bbbbbbbb-0000-4000-8000-000000000001"


def _as_owner(cos, monkeypatch, signed=True):
    monkeypatch.setattr(cos, "_request_binding",
                        lambda: {"origin": "owner", "principal": "1", "owner_request_id": None} if signed else None)


def test_only_a_signed_owner_request_can_queue_or_cancel(cos, monkeypatch):
    client = cos.app.test_client()
    cid = cos._history.ensure_default()["conversation_id"]
    body = {"conversation_id": cid, "request_id": QUEUED, "text": "while you work"}
    _as_owner(cos, monkeypatch, signed=False)
    assert client.post("/ui/queue", json=body, headers=OWNER_HEADERS).status_code == 403
    assert client.post("/ui/queue/cancel", json={"request_id": QUEUED}, headers=OWNER_HEADERS).status_code == 403
    _as_owner(cos, monkeypatch)
    r = client.post("/ui/queue", json=body, headers=OWNER_HEADERS)
    assert r.status_code == 200 and r.get_json()["outcome"] == "queued"
    listed = client.get("/ui/queue?conversation_id=" + cid, headers=OWNER_HEADERS).get_json()
    assert [i["text"] for i in listed["items"]] == ["while you work"] and listed["max"] == 5
    assert client.post("/ui/queue/cancel", json={"request_id": QUEUED}, headers=OWNER_HEADERS).get_json() == {"removed": True}
    assert client.get("/ui/queue", headers=OWNER_HEADERS).get_json()["items"] == []


def test_sending_a_queued_message_takes_it_out_of_the_line(cos, monkeypatch):
    client = cos.app.test_client()
    cid = cos._history.ensure_default()["conversation_id"]
    _as_owner(cos, monkeypatch)
    client.post("/ui/queue", json={"conversation_id": cid, "request_id": QUEUED, "text": "next"}, headers=OWNER_HEADERS)
    r = client.post("/ui/send", json={"text": "next", "channel": "html_text", "conversation_id": cid,
                                      "request_id": QUEUED}, headers=OWNER_HEADERS)
    assert r.status_code == 200 and r.get_json()["reply"]
    assert client.get("/ui/queue", headers=OWNER_HEADERS).get_json()["items"] == []
    # Sent once: queueing the same request again is refused rather than run twice.
    again = client.post("/ui/queue", json={"conversation_id": cid, "request_id": QUEUED, "text": "next"}, headers=OWNER_HEADERS)
    assert again.status_code == 409 and again.get_json()["error"] == "already_sent"

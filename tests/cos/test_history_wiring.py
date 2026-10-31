"""B1a: Confer turns go through the canonical CoS history (spec v0.3 section 4.19), and the page's history routes.

The CoS app is imported with its scheduler stubbed and the network refused; the backend is a fake that records each
turn it was sent. All content is synthetic.
"""
import json
import sys
from unittest.mock import MagicMock

import pytest

from domains.cos.history import BEFORE_1_2, DEFAULT_CONVERSATION_ID

OWNER = {"X-Minimoi-Auth-Id": "1"}
REQUEST_ID = "aaaaaaaa-0000-4000-8000-000000000001"
TURN_ID = "t-aaaaaaaa000040008000000000000001"


def _load(monkeypatch, tmp_path, *, history_on: bool, store_on: bool = False):
    for name in ("apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background",
                 "apscheduler.triggers", "apscheduler.triggers.cron", "apscheduler.triggers.interval"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv("DISABLE_TELEGRAM_POLL", "1")
    monkeypatch.setenv("COS_BACKEND_TYPE", "grok")
    monkeypatch.setenv("MINIMOI_LIMITS_DIR", str(tmp_path / "limits"))
    monkeypatch.setenv("COS_REMINDERS_DIR", str(tmp_path / "reminders"))
    (tmp_path / "conversations").mkdir()
    monkeypatch.setenv("COS_CONVERSATIONS_DIR", str(tmp_path / "conversations"))
    for name in ("COS_FILES_INBOX", "COS_FILES_OUTBOX", "MINIMOI_COS_MANAGED_REALTIME", "COS_STORE_DIR",
                 "COS_STORE_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    if history_on:
        monkeypatch.setenv("COS_STORE_ENVIRONMENT", "dev")
    else:
        monkeypatch.delenv("COS_STORE_ENVIRONMENT", raising=False)
    if store_on:
        (tmp_path / "token").write_text("synthetic-token")
        monkeypatch.setenv("COS_STORE_DIR", str(tmp_path / "store"))
        monkeypatch.setenv("COS_STORE_TOKEN_FILE", str(tmp_path / "token"))
    sys.modules.pop("domains.cos.chief_of_staff", None)
    import domains.cos.chief_of_staff as module

    def no_network(*args, **kwargs):
        raise AssertionError("unexpected network call")
    monkeypatch.setattr(module.requests, "post", no_network)
    monkeypatch.setattr(module.requests, "get", no_network)
    sent = []

    class Backend:
        backend_label, model_label = "fake", "fake-model"
        fail = False

        def call_backend(self, prompt, context, tool_policy):
            sent.append((prompt, context))
            if Backend.fail:
                raise TimeoutError("synthetic timeout")
            return f"Reply {len(sent)}."
    monkeypatch.setattr(module, "_backend", Backend())
    module._test_sent, module._TestBackend = sent, Backend
    return module


@pytest.fixture
def cos(monkeypatch, tmp_path):
    yield _load(monkeypatch, tmp_path, history_on=True)
    sys.modules.pop("domains.cos.chief_of_staff", None)


@pytest.fixture
def cos_off(monkeypatch, tmp_path):
    yield _load(monkeypatch, tmp_path, history_on=False)
    sys.modules.pop("domains.cos.chief_of_staff", None)


def _send(client, text="Wie geht's?", **extra):
    body = {"text": text, "channel": "html_text", "conversation_id": "owner", "request_id": REQUEST_ID, **extra}
    return client.post("/ui/send", json=body, headers=OWNER)


def test_a_turn_is_kept_answered_and_projected_to_the_recall_log(cos):
    r = _send(cos.app.test_client())
    assert r.status_code == 200, r.json
    assert r.json["history"] == {"conversation_id": DEFAULT_CONVERSATION_ID, "turn_id": TURN_ID,
                                 "status": "answered", "saved": True, "reply_saved": True}
    # The routing receipt and the history name the same turn; the first conversation keeps the "owner" session.
    assert r.json["turn_id"] == REQUEST_ID and r.json["conversation_id"] == "owner"
    _, context = cos._test_sent[-1]
    assert context["confer"]["receipt_id"] == REQUEST_ID
    turn = cos._history.turn(DEFAULT_CONVERSATION_ID, TURN_ID)
    assert turn["user_text"] == "Wie geht's?" and turn["reply_text"] == "Reply 1." and turn["status"] == "answered"
    (log,) = cos._conversations_dir.glob("[0-9]*.jsonl")
    line = json.loads(log.read_text().splitlines()[-1])
    assert line["projection"] is True and line["turn_id"] == TURN_ID and line["robert"] == "Wie geht's?"


def test_a_repeated_send_never_calls_the_model_twice(cos):
    client = cos.app.test_client()
    first = _send(client)
    again = _send(client)
    assert again.status_code == 200 and again.json["reply"] == first.json["reply"]
    assert again.json["history"]["replayed"] is True
    assert len(cos._test_sent) == 1
    changed = _send(client, text="Something else")
    assert changed.status_code == 409 and changed.json["history"] == {"refused": "turn_id_reused"}
    assert len(cos._test_sent) == 1


def test_a_failed_backend_call_leaves_the_turn_failed_not_lost(cos):
    cos._TestBackend.fail = True
    r = _send(cos.app.test_client())
    assert r.status_code == 500
    turn = cos._history.turn(DEFAULT_CONVERSATION_ID, TURN_ID)
    assert turn["status"] == "failed" and turn["failure_code"] == "backend_timeout"
    again = _send(cos.app.test_client())
    assert again.status_code == 409 and "Send it again as a new message" in again.json["reply"]


def test_operations_keep_their_own_records(cos):
    r = cos.app.test_client().post("/ui/send", json={"text": "/new", "channel": "html_text"}, headers=OWNER)
    assert r.status_code in (200, 500)
    assert not list((cos._history.root / "turns").glob("*.jsonl"))


def test_the_selected_conversation_carries_the_turn_and_its_own_session(cos):
    client = cos.app.test_client()
    made = client.post("/ui/history/conversations", json={"title": "German plan", "select": True, "device": "laptop"},
                       headers=OWNER)
    assert made.status_code == 201
    cid = made.json["conversation_id"]
    assert client.get("/ui/history/selection?device=laptop", headers=OWNER).json["conversation_id"] == cid
    assert client.get("/ui/history/selection?device=phone", headers=OWNER).json["conversation_id"] \
        == DEFAULT_CONVERSATION_ID
    r = _send(client, device="laptop")
    assert r.json["history"]["conversation_id"] == cid and r.json["conversation_id"] == cid
    page = client.get(f"/ui/history/conversations/{cid}/turns").json
    assert [t["user_text"] for t in page["items"]] == ["Wie geht's?"] and page["next_before_seq"] is None
    listing = client.get("/ui/history/conversations").json["items"]
    assert listing[0]["conversation_id"] == cid and listing[0]["turn_count"] == 1


def test_history_routes_need_identity_for_writes_and_name_missing_records(cos):
    client = cos.app.test_client()
    assert client.post("/ui/history/conversations", json={"title": "x"}).status_code == 401
    assert client.get("/ui/history/selection").status_code == 401
    assert client.post("/ui/history/selection", json={"conversation_id": DEFAULT_CONVERSATION_ID}).status_code == 401
    assert client.get("/ui/history/conversations/cosc-ffffffffffff/turns").status_code == 404


def test_the_before_1_2_conversation_reloads_but_takes_no_turns(cos):
    (cos._conversations_dir / "2026-10.jsonl").write_text(json.dumps({
        "at": "2026-10-20T10:00:00+00:00", "conversation_id": "owner", "turn_id": REQUEST_ID.replace("1", "2"),
        "channel": "html_text", "robert": "Old words", "attachments": [], "cos": "Old reply", "downloads": []}) + "\n")
    assert cos._history.import_monthly_logs()["imported"] == 1
    client = cos.app.test_client()
    page = client.get(f"/ui/history/conversations/{BEFORE_1_2}/turns").json
    assert [t["user_text"] for t in page["items"]] == ["Old words"]
    r = _send(client, conversation_id=BEFORE_1_2)
    assert r.status_code == 400 and r.json["history"] == {"refused": "read_only"}


def test_with_the_switch_off_nothing_changes(cos_off):
    assert cos_off._history is None
    r = _send(cos_off.app.test_client())
    assert r.status_code == 200 and "history" not in r.json
    (log,) = cos_off._conversations_dir.glob("*.jsonl")
    assert "projection" not in json.loads(log.read_text().splitlines()[-1])
    assert cos_off.app.test_client().get("/ui/history/conversations").status_code == 404


def test_after_the_memory_import_a_note_goes_to_the_store_and_the_file_is_its_projection(monkeypatch, tmp_path):
    from domains.cos.store import memory
    cos = _load(monkeypatch, tmp_path, history_on=True, store_on=True)
    try:
        memory_file = tmp_path / "cos_memory.md"
        memory_file.write_text("## Active agenda\n[chat] 2026-07-11: Synthetic line.\n")
        monkeypatch.setattr(cos, "COS_MEMORY_FILE", memory_file)
        writer = cos._cos_store_writer
        assert writer is not None and writer._verify == cos._history.verify_robert_source
        before = cos._save_explicit_note("Synthetic note before import", "op-before")
        assert before["saved"] is True and "store" not in str(writer.rows("cos.fact"))
        assert "Synthetic note before import" in memory_file.read_text()        # the file writer, as today
        memory.import_file(writer, memory_file)
        after = cos._save_explicit_note("Synthetic note after import", "op-after")
        assert after == {"saved": True, "deduplicated": False}
        assert writer.rows("cos.fact")[-1]["entry"] == {"text": "Synthetic note after import",
                                                         "provenance": "approved",
                                                         "ref": {"type": "cos.memory_note", "id": "op-after"}}
        assert memory_file.read_text().startswith(memory.PROJECTION_HEADER)
        assert cos._save_explicit_note("Synthetic note after import", "op-after")["deduplicated"] is True
    finally:
        sys.modules.pop("domains.cos.chief_of_staff", None)


def test_a_turn_never_stays_pending_when_its_reply_cannot_be_saved(cos, monkeypatch):
    # Review S1 (#462): if the reply can't be put on the turn, the turn is failed, not left pending.
    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(cos._history, "answer", broken)
    r = _send(cos.app.test_client())
    assert r.json["history"]["status"] == "failed" and r.json["history"]["reply_saved"] is False
    assert cos._history.turn(DEFAULT_CONVERSATION_ID, TURN_ID)["failure_code"] == "reply_not_saved"


def test_a_failure_after_the_backend_still_fails_the_turn(cos, monkeypatch):
    # Review S1 (#462): anything between begin and answer that raises marks the turn failed.
    from domains.cos import confer_service
    monkeypatch.setattr(confer_service.ConferTurnResult, "public_dict", lambda self: (_ for _ in ()).throw(RuntimeError("x")))
    r = _send(cos.app.test_client())
    assert r.status_code == 500
    assert cos._history.turn(DEFAULT_CONVERSATION_ID, TURN_ID)["status"] == "failed"


def test_without_the_store_a_note_is_refused_when_the_file_is_the_projection(cos_off, monkeypatch, tmp_path):
    # Review M2 (#462): cos-bot has no store writer; appending to the projection would be erased by the next rewrite.
    from domains.cos.store import memory
    memory_file = tmp_path / "cos_memory.md"
    memory_file.write_text(memory.PROJECTION_HEADER + "\n\n## Recent\n[note] 2026-10-31: kept in the store\n")
    monkeypatch.setattr(cos_off, "COS_MEMORY_FILE", memory_file)
    out = cos_off._save_explicit_note("A Telegram note", "op-tg-1")
    assert out["saved"] is False and out["code"] == "memory_needs_store"
    assert "A Telegram note" not in memory_file.read_text()

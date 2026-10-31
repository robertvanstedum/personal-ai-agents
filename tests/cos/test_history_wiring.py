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


@pytest.mark.parametrize("entry_type,operation", [("chat", None), ("note", "synthetic-operation")])
def test_writerless_memory_never_appends_to_store_projection(cos_off, tmp_path, monkeypatch, entry_type, operation):
    path = tmp_path / "memory.md"
    path.write_text(cos_off.cos_memory_store.PROJECTION_HEADER + "\n\nExisting fact\n")
    monkeypatch.setattr(cos_off, "COS_MEMORY_FILE", path)
    monkeypatch.setattr(cos_off, "_cos_store_writer", None)
    monkeypatch.setattr(cos_off, "_log_file", lambda *a: None)
    before = path.read_bytes()
    result = cos_off._append_memory_entry("Do not lose this", entry_type=entry_type, operation_id=operation)
    assert result == {"saved": False, "deduplicated": False, "code": "memory_store_unavailable"}
    assert path.read_bytes() == before


def test_writerless_legacy_memory_still_accepts_notes(cos_off, tmp_path, monkeypatch):
    path = tmp_path / "memory.md"
    path.write_text("# CoS Memory\n\nLegacy fact\n")
    monkeypatch.setattr(cos_off, "COS_MEMORY_FILE", path)
    monkeypatch.setattr(cos_off, "_cos_store_writer", None)
    result = cos_off._append_memory_entry("A new note", entry_type="note", operation_id="synthetic-operation")
    assert result["saved"] is True
    assert "A new note" in path.read_text()


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


@pytest.mark.parametrize("stage", ["snapshot", "delivered_since"])
def test_file_failure_does_not_leave_history_pending(cos, monkeypatch, stage):
    files = MagicMock()
    getattr(files, stage).side_effect = OSError("synthetic file failure")
    monkeypatch.setattr(cos, "_cos_files", files)
    assert _send(cos.app.test_client()).status_code == 500
    assert cos._history.turn(DEFAULT_CONVERSATION_ID, TURN_ID)["status"] == "failed"
    calls = len(cos._test_sent)
    assert _send(cos.app.test_client()).status_code == 409
    assert len(cos._test_sent) == calls


def test_reply_save_retries_without_calling_model_again(cos, monkeypatch):
    answer = cos._history.answer
    attempts = []
    def flaky(*args):
        attempts.append(args)
        saved = answer(*args)
        if len(attempts) == 1:
            raise OSError("lost acknowledgement")
        return saved
    monkeypatch.setattr(cos._history, "answer", flaky)
    result = _send(cos.app.test_client())
    assert result.status_code == 200 and result.json["history"]["reply_saved"] is True
    assert len(attempts) == 2 and len(cos._test_sent) == 1


def test_persistent_reply_save_failure_is_terminal(cos, monkeypatch):
    def fail(*args):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(cos._history, "answer", fail)
    result = _send(cos.app.test_client())
    assert result.status_code == 200 and result.json["reply"] == "Reply 1."
    assert result.json["history"]["status"] == "failed"
    assert result.json["history"]["reply_saved"] is False
    assert _send(cos.app.test_client()).status_code == 409
    assert len(cos._test_sent) == 1


def test_two_lost_answer_acknowledgements_still_project_reconciled_reply(cos, monkeypatch):
    answer = cos._history.answer
    def lost_ack(*args):
        answer(*args)
        raise OSError("lost acknowledgement")
    monkeypatch.setattr(cos._history, "answer", lost_ack)
    result = _send(cos.app.test_client())
    assert result.status_code == 200 and result.json["history"]["reply_saved"] is True
    assert result.json["history"]["status"] == "answered"
    (log,) = cos._conversations_dir.glob("[0-9]*.jsonl")
    record = json.loads(log.read_text().splitlines()[-1])
    assert record["cos"] == "Reply 1." and record["turn_id"] == TURN_ID
    assert len(cos._test_sent) == 1


def test_picture_preference_is_owner_device_scoped_and_validated(cos):
    client = cos.app.test_client()
    assert client.post('/ui/history/preferences', json={'device':'desk','picture':'faded'}, headers=OWNER).get_json() == {'picture':'faded'}
    assert client.get('/ui/history/preferences?device=desk', headers=OWNER).get_json() == {'picture':'faded'}
    assert client.get('/ui/history/preferences?device=phone', headers=OWNER).get_json() == {'picture':'shown'}
    assert client.post('/ui/history/preferences', json={'picture':'<script>'}, headers=OWNER).status_code == 400
    assert client.post('/ui/history/preferences', json={'picture':'hidden'}).status_code == 401


def test_founders_page_serves_shared_style_and_canonical_history_client(cos):
    client = cos.app.test_client()
    response = client.get('/ui/confer', headers=OWNER)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'cos-history-ui.js' in html and 'founders-ui/founders.css' in html
    assert 'data-tab="rooms"' in html and 'data-tab="records"' in html
    import re
    revision = re.search(r'founders.css\?v=([a-f0-9]{16})',html).group(1)
    assert f'cos-history-ui.js?v={revision}' in html
    assert 'data-mc-panel' in html  # This page owns its MC controls; no second floating shell.
    assert client.get('/founders-ui/founders.css').status_code == 200
    assert client.get('/static/cos-history-ui.js').status_code == 200

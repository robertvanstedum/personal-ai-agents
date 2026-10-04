"""CoS's turn-log file format, pinned byte for byte (v0.5.1 §8: the shared
core/agent_turns writer must not change what CoS writes)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from domains.cos import turn_log, voice_transcripts
from domains.cos.confer_service import ConferTurnRequest, ConferTurnResult

NOW = datetime(2026, 10, 4, 3, 30, 5, tzinfo=timezone.utc)


def test_a_cos_turn_line_and_status_file_are_exactly_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("COS_TURNS_DIR", str(tmp_path))
    monkeypatch.setenv("COS_TURNS_MIN_FREE_BYTES", "1")
    monkeypatch.setenv("COS_CONTAINER_NAME", "cos-fmt")
    monkeypatch.setenv("COS_BACKEND_TYPE", "Grok")
    monkeypatch.setenv("COS_AGENT_TIMEZONE", "America/Chicago")
    result = ConferTurnResult(turn_id="t1", conversation_id="owner", channel="html_text", user_text="héllo",
                              reply="a reply", backend_label="B", model_label="m", created_at="c",
                              operation={"type": "note", "status": "saved", "storage": "s", "extra": 1})
    assert turn_log.record_turn(ConferTurnRequest(text="héllo", channel="html_text"), result, clock=lambda: NOW) == (True, "saved")
    expected = ('{"schema_version":1,"record_type":"cos_turn","time":"2026-10-04T03:30:05Z","turn_id":"t1",'
                '"conversation_id":"owner","channel":"html_text","backend_type":"grok","backend_label":"B",'
                '"served_model":"m","receipt_id":"t1","user_text":"héllo","reply":"a reply","sanitized":false,'
                '"operation":{"type":"note","status":"saved","storage":"s"}}\n')
    assert (tmp_path / "2026" / "2026-10-03.jsonl").read_text(encoding="utf-8") == expected
    assert json.loads((tmp_path / "_status" / "cos-fmt.json").read_text()) == {
        "schema_version": 1, "container": "cos-fmt", "last_success_at": "2026-10-04T03:30:05Z"}


def test_the_old_import_paths_and_codes_still_work():
    from core.agent_turns import writer
    assert voice_transcripts.append_record is writer.append_record
    assert (turn_log.SAVED, turn_log.DISK_LOW, turn_log.WRITE_FAILED, turn_log.INTERNAL) == (
        "saved", "disk_low", "disk_write_failed", "internal")
    assert turn_log.FAILURES == writer.FAILURES and turn_log.MAX_USER_TEXT == 8000 and turn_log.MAX_REPLY == 16000
    assert (turn_log.NO_TURN_LOG, turn_log.DISABLED, turn_log.PRIVATE) == ("no_turn_log", "disabled", "private")

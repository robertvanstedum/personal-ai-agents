import json

from domains.curator import curator_intelligence as intelligence


def test_storage_paths_share_curator_data_directory():
    assert intelligence.PREFS_PATH.parent == intelligence.DATA_DIR
    assert intelligence.OUTPUT_DIR == intelligence.DATA_DIR
    assert intelligence.RESPONSES_PATH.parent == intelligence.DATA_DIR


def test_save_output_uses_configured_output_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(intelligence, "OUTPUT_DIR", tmp_path)

    output = intelligence.save_output(
        [{"type": "topic_velocity", "content": "test"}],
        "2026-07-21",
        telegram_sent=False,
    )

    assert output == tmp_path / "intelligence_20260721.json"
    assert json.loads(output.read_text())["date"] == "2026-07-21"


def test_save_response_uses_configured_response_file(tmp_path, monkeypatch):
    response_file = tmp_path / "intelligence_responses.json"
    monkeypatch.setattr(intelligence, "RESPONSES_PATH", response_file)

    saved = intelligence.save_response({
        "observation_type": "topic_velocity",
        "topic": "rates",
        "reaction": "agree",
    })

    assert saved["id"] == "resp_001"
    assert json.loads(response_file.read_text())["responses"][0]["topic"] == "rates"

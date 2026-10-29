import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from domains.curator.research.agent import research, threads
from domains.curator import research_routes


def _load_generate_dive_module():
    script = (
        ROOT
        / "domains"
        / "curator"
        / "research"
        / "scripts"
        / "generate_dive.py"
    )
    spec = importlib.util.spec_from_file_location("test_generate_dive_persistence", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_web_created_sessions_are_counted_from_durable_thread_data(
    tmp_path, monkeypatch
):
    research_root = tmp_path / "research-intelligence"
    durable = research_root / "data" / "threads" / "new-topic" / "sessions"
    durable.mkdir(parents=True)
    (durable / "new-topic-001.md").write_text("# Session")

    monkeypatch.setattr(research_routes, "DATA_ROOT", research_root / "data")
    monkeypatch.setattr(research_routes, "STATE_ROOT", research_root)

    assert research_routes._get_topic_sessions("new-topic") == ["new-topic-001"]
    assert research_routes._next_session_name("new-topic") == "new-topic-002"


def test_research_config_merges_durable_thread_queries(tmp_path, monkeypatch):
    root = tmp_path / "research-intelligence"
    (root / "agent").mkdir(parents=True)
    (root / "agent" / "config.json").write_text(json.dumps({
        "session_searches": {"static-topic": ["static query"]},
        "triage_targets": {},
    }))
    thread_dir = root / "data" / "threads" / "new-topic"
    thread_dir.mkdir(parents=True)
    (thread_dir / "thread.json").write_text(json.dumps({
        "session_searches": ["durable query"],
        "triage_targets": ["durable target"],
    }))

    from domains.curator.research.paths import configured
    monkeypatch.setattr(research, "PATHS", configured({"CURATOR_RESEARCH_DATA_DIR":str(root/"data"), "CURATOR_RESEARCH_STATE_DIR":str(root)},repo=root/"repo"))
    monkeypatch.setattr(research, "DATA_ROOT", root/"data")
    config = research.load_config("new-topic")

    assert config["session_searches"]["new-topic"] == ["durable query"]
    assert config["triage_targets"]["new-topic"] == ["durable target"]


def test_dive_loader_reads_durable_web_session(tmp_path, monkeypatch):
    module = _load_generate_dive_module()
    topics_dir = tmp_path / "topics"
    threads_dir = tmp_path / "data" / "threads"
    durable = threads_dir / "new-topic" / "sessions"
    durable.mkdir(parents=True)
    (threads_dir / "new-topic" / "thread.json").write_text(json.dumps({
        "motivation": "Investigate the new topic",
    }))
    (durable / "new-topic-001.md").write_text("""# Session Findings

<!-- MACHINE-READABLE HEADER — do not remove or reorder these lines -->
date: 2026-07-30
session: new-topic-001
topic: new-topic
<!-- END HEADER -->

## Key Findings

- Durable sessions survive container replacement.
""")

    monkeypatch.setattr(module, "TOPICS_DIR", topics_dir)
    monkeypatch.setattr(module, "THREADS_DIR", threads_dir)
    monkeypatch.setattr(module, "ANNOTATIONS_DIR", tmp_path / "annotations")

    data = module.load_thread_data("new-topic")

    assert data["motivation"] == "Investigate the new topic"
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["findings"] == [
        "Durable sessions survive container replacement."
    ]


def test_thread_record_preserves_dynamic_research_directions():
    record = threads.ThreadRecord(
        id="new-topic-2026",
        topic="new-topic",
        opened="2026-07-30T14:00:00+00:00",
        motivation="Investigate",
        prior_belief="",
        session_searches=["query one"],
        triage_targets=["target one"],
    )

    restored = threads.ThreadRecord(**record.model_dump())

    assert restored.session_searches == ["query one"]
    assert restored.triage_targets == ["target one"]


def test_dive_reads_research_and_general_comments_without_moving_them(
    tmp_path, monkeypatch
):
    module = _load_generate_dive_module()
    research_comments = tmp_path / "research-intelligence" / "annotations"
    general_comments = tmp_path / "app" / "annotations"
    (research_comments / "new-topic").mkdir(parents=True)
    (general_comments / "new-topic").mkdir(parents=True)
    research_file = research_comments / "new-topic" / "2026-03-27.json"
    general_file = general_comments / "new-topic" / "2026-10-15.json"
    research_file.write_text(json.dumps([
        {"note_id": "aaa", "timestamp": "2026-03-27T10:00:00", "note": "older research note", "page": "sessions"},
        {"note_id": "dup", "timestamp": "2026-03-28T10:00:00", "note": "same note in both", "page": "sessions"},
    ]))
    general_file.write_text(json.dumps([
        {"note_id": "bbb", "timestamp": "2026-10-15T09:00:00", "note": "general comment", "page": "observe", "domain": "research"},
        {"note_id": "dup", "timestamp": "2026-03-28T10:00:00", "note": "same note in both", "page": "sessions"},
        "not a record",
    ]))
    (tmp_path / "threads" / "new-topic" / "sessions").mkdir(parents=True)
    before = {p: p.read_bytes() for p in (research_file, general_file)}

    monkeypatch.setattr(module, "TOPICS_DIR", tmp_path / "topics")
    monkeypatch.setattr(module, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(module, "ANNOTATIONS_DIR", research_comments)
    monkeypatch.setattr(module, "GENERAL_ANNOTATIONS_DIR", general_comments)

    data = module.load_thread_data("new-topic")

    assert [a["note_id"] for a in data["annotations"]] == ["aaa", "dup", "bbb"]
    assert data["annotations"][2]["page"] == "observe"
    assert data["annotations"][2]["domain"] == "research"
    assert {p: p.read_bytes() for p in before} == before


def test_dive_general_comments_folder_is_the_mounted_app_annotations_folder():
    module = _load_generate_dive_module()
    assert module.GENERAL_ANNOTATIONS_DIR == ROOT / "data" / "annotations" / "research"


def test_dive_keeps_different_comments_that_share_a_note_id(tmp_path, monkeypatch):
    module = _load_generate_dive_module()
    research_comments = tmp_path / "research" / "annotations"
    general_comments = tmp_path / "general" / "annotations"
    (research_comments / "new-topic").mkdir(parents=True)
    (general_comments / "new-topic").mkdir(parents=True)
    (tmp_path / "threads" / "new-topic" / "sessions").mkdir(parents=True)
    (research_comments / "new-topic" / "2026-10-15.json").write_text(json.dumps([
        {"note_id": "same", "timestamp": "2026-10-15T09:00:00", "note": "first wording", "page": "sessions"},
    ]))
    (general_comments / "new-topic" / "2026-10-15.json").write_text(json.dumps([
        {"note_id": "same", "timestamp": "2026-10-15T09:00:00", "note": "second wording", "page": "observe"},
        {"note_id": "same", "timestamp": "2026-10-15T09:00:00", "note": "second wording", "page": "sessions"},
    ]))

    monkeypatch.setattr(module, "TOPICS_DIR", tmp_path / "topics")
    monkeypatch.setattr(module, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(module, "ANNOTATIONS_DIR", research_comments)
    monkeypatch.setattr(module, "GENERAL_ANNOTATIONS_DIR", general_comments)

    kept = module.load_thread_data("new-topic")["annotations"]

    assert sorted((a["note"], a["page"]) for a in kept) == [
        ("first wording", "sessions"),
        ("second wording", "observe"),
        ("second wording", "sessions"),
    ]


def test_dive_orders_mixed_timestamps_without_dropping_any_record(tmp_path, monkeypatch):
    module = _load_generate_dive_module()
    research_comments = tmp_path / "research" / "annotations"
    general_comments = tmp_path / "general" / "annotations"
    (research_comments / "new-topic").mkdir(parents=True)
    (general_comments / "new-topic").mkdir(parents=True)
    (tmp_path / "threads" / "new-topic" / "sessions").mkdir(parents=True)
    (research_comments / "new-topic" / "2026-10-15.json").write_text(json.dumps([
        {"note_id": "num", "timestamp": 1760000000, "note": "numeric date"},
        {"note_id": "late", "timestamp": "2026-10-15T09:00:00", "note": "later"},
        {"note_id": "null", "timestamp": None, "note": "null date"},
    ]))
    (general_comments / "new-topic" / "2026-10-15.json").write_text(json.dumps([
        {"note_id": "missing", "note": "no date field"},
        {"note_id": "early", "timestamp": "2026-03-27T10:00:00", "note": "earlier"},
        {"note_id": "empty", "timestamp": "", "note": "empty date", "ref_text": 42},
    ]))

    monkeypatch.setattr(module, "TOPICS_DIR", tmp_path / "topics")
    monkeypatch.setattr(module, "THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(module, "ANNOTATIONS_DIR", research_comments)
    monkeypatch.setattr(module, "GENERAL_ANNOTATIONS_DIR", general_comments)

    kept = module.load_thread_data("new-topic")["annotations"]

    # Dated records by time first, then the rest in load order; none dropped.
    assert [a["note_id"] for a in kept] == ["early", "late", "num", "null", "missing", "empty"]
    text = module._format_annotations(kept)
    assert "[2026-03-27] (reaction) earlier" in text
    assert "[1760000000] (reaction) numeric date" in text
    assert "[undated] (reaction) null date" in text
    assert "[undated] (reaction) no date field" in text
    assert '[undated] (reaction) re: "42" — empty date' in text

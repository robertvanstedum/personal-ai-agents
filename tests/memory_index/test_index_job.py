"""The memory-index job publishes the freshness light through the jobs status contract. Synthetic only."""
from __future__ import annotations

import io
import importlib.util
from pathlib import Path

from core.jobs import status
from tests.memory_index.helpers import FakeEmbedder, add_record, world

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("index_job", REPO / "scripts" / "memory" / "index_job.py")
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


def run(tmp_path, shelf, emb):
    code = job.run(jobs_root=tmp_path / "jobs", shelf_root=shelf.root, embedder=emb,
                   registry_path=tmp_path / "missing.json", out=io.StringIO())
    return code, status.read_status(tmp_path / "jobs", "memory-index.json", "memory-index")


def test_ok_run_writes_a_valid_counts_only_status(tmp_path):
    shelf, _ = world(tmp_path)
    code, doc = run(tmp_path, shelf, FakeEmbedder())
    assert code == 0 and doc["state"] == "ok" and doc["results"] == {"update": "ok", "light": "ok"}
    assert doc["last_success_at"] and "4 records current, 1 held" in doc["summary"]
    for word in ("telescope", "sourdough", "sessions-raw"):
        assert word not in str(doc)


def test_embedder_down_is_warn_and_pending_shows(tmp_path):
    shelf, _ = world(tmp_path)
    run(tmp_path, shelf, FakeEmbedder())
    add_record(shelf, "grok", "late", [("human", "seedlings")])
    code, doc = run(tmp_path, shelf, FakeEmbedder(down=True))
    assert code == 0 and doc["state"] == "warn"
    assert doc["results"]["update"] == "embedder_unavailable" and doc["results"]["pending"] == "some"


def test_pin_mismatch_is_failed(tmp_path):
    shelf, _ = world(tmp_path)
    run(tmp_path, shelf, FakeEmbedder())
    code, doc = run(tmp_path, shelf, FakeEmbedder(digest="0a109f422b47" + "1" * 52))
    assert code == 1 and doc["state"] == "failed" and doc["results"]["update"] == "pin_mismatch"
    assert doc["last_success_at"]                                  # carried forward from the earlier good run


def test_judge_maps_codes():
    assert job.judge({"code": "ok"}, {"light": "ok"})[0] == "ok"
    assert job.judge({"code": "disk_low"}, {"light": "ok"})[0] == "warn"
    assert job.judge({"code": "ok"}, {"light": "unknown"})[0] == "warn"
    assert job.judge({"code": "ok"}, {"light": "failed", "orphans": {"x": 1}})[0] == "failed"
    assert job.judge({"code": "exception_oserror"}, {"light": "ok"})[0] == "failed"

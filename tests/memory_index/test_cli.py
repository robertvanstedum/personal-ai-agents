"""The ``moi rebuild`` / ``moi index`` commands. Synthetic only."""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from core.memory_index import cli
from tests.memory_index.helpers import FakeEmbedder, world

REPO = Path(__file__).resolve().parents[2]


def run(argv, emb):
    out = io.StringIO()
    code = cli.main(argv, embedder=emb, out=out)
    return code, out.getvalue()


def test_rebuild_status_search_round_trip(tmp_path, monkeypatch):
    shelf, ids = world(tmp_path)
    emb = FakeEmbedder()
    from core.memory_index import health
    monkeypatch.setattr(health, "fidelity_sweep", lambda root: {"checked": 5, "ok": 5, "failed": 0, "errors": 0,
                                                               "skipped": {}, "failures": []})
    code, text = run(["rebuild", "--shelf-root", str(shelf.root)], emb)
    doc = json.loads(text)
    assert code == 0 and doc["code"] == "ok" and doc["swapped"] and doc["fidelity"]["ok"] == 5
    code, text = run(["index", "status", "--shelf-root", str(shelf.root)], emb)
    assert code == 0 and json.loads(text)["light"] == "ok"
    code, text = run(["index", "check", "--shelf-root", str(shelf.root)], emb)
    assert code == 0
    code, text = run(["index", "search", "Dobsonian", "--tier", "raw", "--shelf-root", str(shelf.root)], emb)
    assert code == 0 and "== keyword (1) ==" in text and "Dobsonian" in text and "written by Grok" in text
    code, text = run(["index", "search", "Dobsonian", "--json", "--shelf-root", str(shelf.root)], emb)
    assert code == 0 and json.loads(text)["keyword"] == []                      # default tier is curated


def test_rebuild_fails_loudly_when_the_fidelity_sweep_finds_damage(tmp_path, monkeypatch):
    shelf, _ = world(tmp_path)
    from core.memory_index import health
    monkeypatch.setattr(health, "fidelity_sweep", lambda root: {"checked": 5, "ok": 4, "failed": 1, "errors": 0,
                                                               "skipped": {}, "failures": [{"record": "x", "findings": ["dropped"]}]})
    code, _ = run(["rebuild", "--shelf-root", str(shelf.root)], FakeEmbedder())
    assert code == 1


def test_check_exits_nonzero_without_an_index_and_search_says_rebuild(tmp_path):
    shelf, _ = world(tmp_path)
    code, text = run(["index", "check", "--shelf-root", str(shelf.root)], FakeEmbedder())
    assert code == 1 and json.loads(text)["light"] == "unknown"
    code, text = run(["index", "search", "x", "--shelf-root", str(shelf.root)], FakeEmbedder())
    assert code == 1 and "moi rebuild" in text


def test_update_and_status_print_counts_only(tmp_path):
    shelf, _ = world(tmp_path)
    emb = FakeEmbedder()
    _, a = run(["index", "update", "--shelf-root", str(shelf.root)], emb)
    _, b = run(["index", "status", "--shelf-root", str(shelf.root)], emb)
    for word in ("sourdough", "telescope", "invoice", "zebra", "sessions-raw"):
        assert word not in a + b


def test_moi_dispatches_index_commands(tmp_path):
    script = (REPO / "scripts" / "moi").read_text()
    assert "rebuild|index) mod=core.memory_index.cli" in script
    proc = subprocess.run(["sh", str(REPO / "scripts" / "moi"), "index", "--help"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "MOI_PYTHON": str(Path(__import__("sys").executable))})
    assert proc.returncode == 0 and "update" in proc.stdout and "search" in proc.stdout

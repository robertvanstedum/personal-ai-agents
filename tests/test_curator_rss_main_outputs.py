"""The real production main() writes every daily output after the history (#305).

A later local `import os` made `os` local to the whole of main(), so the
CURATOR_OUTPUT_PATH read raised UnboundLocalError. Everything after
save_to_history() was skipped: curator_output.txt, the dated archive, the
latest/briefing pages and curator_latest.json.
This runs the actual main() with only scoring, HTML, role and Signal Store
stubbed: no feeds, provider, Telegram, network or repository writes.
"""

import json
import symtable
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RSS = ROOT / "domains" / "curator" / "curator_rss_v2.py"


def test_main_never_binds_os_locally():
    namespace = symtable.symtable(RSS.read_text(), str(RSS), "exec").lookup("main").get_namespace()
    assert not namespace.lookup("os").is_local()


def _article():
    return {
        "hash_id": "abc12", "title": "Synthetic article", "source": "Example",
        "link": "https://example.com/a", "summary": "s", "category": "other",
        "published": datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc),
        "score": 9.0, "final_score": 9.0, "method": "synthetic",
    }


def test_production_main_writes_every_output_after_history(tmp_path, monkeypatch):
    from domains.curator import curator_rss_v2 as rss
    import signal_store
    import utils.role

    work = tmp_path / "app"
    (work / "data" / "curator").mkdir(parents=True)
    pages = tmp_path / "pages" / "domains" / "curator"
    pages.mkdir(parents=True)
    output_path = work / "data" / "curator" / "curator_output.txt"
    monkeypatch.chdir(work)
    monkeypatch.setenv("CURATOR_OUTPUT_PATH", str(output_path))
    monkeypatch.setattr(sys, "argv", ["curator_rss_v2.py", "--model=xai"])

    monkeypatch.setattr(utils.role, "is_production", lambda: True)
    monkeypatch.setattr(utils.role, "role_label", lambda: "production")
    monkeypatch.setattr(signal_store, "get_session_id", lambda: "0123456789")
    monkeypatch.setattr(signal_store, "log_article_scored", lambda **_kw: None)
    monkeypatch.setattr(signal_store, "log_priority_match", lambda **_kw: None)
    monkeypatch.setattr(rss, "curate", lambda **_kw: ([_article()], [_article()]))
    monkeypatch.setattr(rss, "get_active_topic_tags", lambda: {})
    monkeypatch.setattr(rss, "find_radar_articles", lambda *_a, **_kw: [])
    monkeypatch.setattr(rss, "format_html", lambda *_a, **_kw: '<a href="../x">briefing</a>')
    monkeypatch.setattr(rss, "generate_index_page", lambda _d: None)
    monkeypatch.setattr(rss, "_DATA_DIR", work / "data" / "curator")
    # latest/briefing pages are anchored to the module's own folder; keep them in tmp.
    monkeypatch.setattr(rss, "__file__", str(pages / "curator_rss_v2.py"))

    rss.main()

    assert (work / "curator_cache" / "abc12.json").exists()           # history step
    assert "Synthetic article" in output_path.read_text()            # the crash point
    assert not (work / "curator_output.txt").exists()                # never the old path
    assert list((work / "curator_archive").glob("curator_*.html"))   # dated archive
    assert list((work / "curator_archive").glob("curator_*.json"))   # dated JSON copy
    assert (pages / "curator_latest.html").read_text() == '<a href="x">briefing</a>'
    assert (pages / "curator_briefing.html").exists()
    latest = json.loads((work / "data" / "curator" / "curator_latest.json").read_text())
    assert latest[0]["hash_id"] == "abc12"
    assert latest[0]["briefing_date"] == datetime.now(timezone.utc).date().isoformat()

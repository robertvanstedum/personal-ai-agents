import fcntl
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import german_domain
import html_server
from domains.german import lesen_refresh_cli


CHICAGO = ZoneInfo("America/Chicago")


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _configure_store(monkeypatch, tmp_path, articles=None, sources=None):
    config = tmp_path / "config"
    articles_file = config / "lesen_articles.json"
    sources_file = config / "lesen_sources.json"
    filters_file = config / "lesen_filters.json"
    monkeypatch.setattr(german_domain, "_LESEN_ARTICLES_FILE", articles_file)
    monkeypatch.setattr(german_domain, "_LESEN_SOURCES_FILE", sources_file)
    monkeypatch.setattr(german_domain, "_LESEN_FILTERS_FILE", filters_file)
    monkeypatch.setattr(german_domain, "_LESEN_FEEDBACK_FILE", config / "lesen_feedback.json")
    monkeypatch.setattr(german_domain, "_LESEN_ARCHIV_FILE", config / "lesen_archiv.json")
    monkeypatch.setattr(german_domain, "_LESEN_MUTATION_LOCK_FILE", config / ".lesen_articles.lock")
    monkeypatch.setattr(german_domain, "_LESEN_REFRESH_LOCK_FILE", config / ".lesen_refresh.lock")
    _write(
        articles_file,
        articles or {"articles": [], "last_fetched": None},
    )
    _write(
        sources_file,
        {
            "sources": sources
            or [
                {"name": "ORF Wien", "url": "https://feed.test/wien", "category": "wien"},
                {"name": "ORF Sport", "url": "https://feed.test/sport", "category": "alltag"},
            ]
        },
    )
    _write(filters_file, {"blocked_keywords": []})
    return articles_file


def _entry(url, title="Neue Geschichte"):
    return {"link": url, "title": title, "summary": "Eine kurze Zusammenfassung."}


def _feed(entries=(), *, bozo=False, error=None, status=None):
    return SimpleNamespace(
        entries=list(entries),
        bozo=bozo,
        bozo_exception=error,
        status=status,
    )


def _mock_fetch(monkeypatch, feeds):
    monkeypatch.setattr(german_domain.feedparser, "parse", lambda url: feeds[url])
    monkeypatch.setattr(
        german_domain,
        "categorize_article",
        lambda title, summary, source_category="wien": source_category,
    )


def test_healthy_refresh_adds_unique_articles_and_marks_success(monkeypatch, tmp_path):
    articles_file = _configure_store(monkeypatch, tmp_path)
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed([_entry("https://news.test/a?utm_source=rss")]),
            "https://feed.test/sport": _feed([_entry("https://news.test/b")]),
        },
    )

    result = german_domain.refresh_lesen_feed(
        now=datetime(2026, 7, 20, 9, 30, tzinfo=CHICAGO)
    )

    saved = json.loads(articles_file.read_text())
    assert result["ok"] is True
    assert result["status"] == "success"
    assert result["added"] == 2
    assert result["source_counts"] == {
        "configured": 2,
        "attempted": 2,
        "successful": 2,
        "healthy_empty": 0,
        "failed": 0,
    }
    assert saved["last_fetched"].startswith("2026-07-20T09:30:00")
    assert len(saved["articles"]) == 2
    assert len({article["id"] for article in saved["articles"]}) == 2
    assert saved["articles"][0]["url"] == "https://news.test/a?utm_source=rss"
    assert "_normalized_url" not in saved["articles"][0]


def test_fetch_report_is_read_only(monkeypatch, tmp_path):
    articles_file = _configure_store(monkeypatch, tmp_path)
    before = articles_file.read_bytes()
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed([_entry("https://news.test/a")]),
            "https://feed.test/sport": _feed([]),
        },
    )

    report = german_domain.fetch_lesen_articles_report(
        now=datetime(2026, 7, 20, 9, 30, tzinfo=CHICAGO)
    )

    assert report["candidate_count"] == 1
    assert articles_file.read_bytes() == before


def test_source_processing_error_is_counted_once(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed([_entry("https://news.test/a", "bad")]),
            "https://feed.test/sport": _feed([]),
        },
    )
    monkeypatch.setattr(
        german_domain,
        "categorize_article",
        lambda title, summary, source_category="wien": (_ for _ in ()).throw(RuntimeError("model")),
    )

    report = german_domain.fetch_lesen_articles_report(
        now=datetime(2026, 7, 20, 9, 30, tzinfo=CHICAGO)
    )

    assert report["source_counts"]["configured"] == 2
    assert report["source_counts"]["attempted"] == 2
    assert report["source_counts"]["failed"] == 1
    assert report["source_counts"]["healthy_empty"] == 1


def test_feedparser_warning_with_usable_entries_is_not_retry_failure(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed(
                [_entry("https://news.test/a")],
                bozo=True,
                error=ValueError("minor XML warning"),
            ),
            "https://feed.test/sport": _feed([], status=200),
        },
    )

    report = german_domain.fetch_lesen_articles_report(
        now=datetime(2026, 7, 20, 9, 30, tzinfo=CHICAGO)
    )

    assert report["source_counts"]["failed"] == 0
    assert report["source_counts"]["successful"] == 1
    assert report["sources"][0]["error_category"] == "ValueError"


def test_http_error_is_source_failure_even_with_entries(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed(
                [_entry("https://news.test/error-page")], status=503
            ),
            "https://feed.test/sport": _feed([], status=200),
        },
    )

    report = german_domain.fetch_lesen_articles_report(
        now=datetime(2026, 7, 20, 9, 30, tzinfo=CHICAGO)
    )

    assert report["candidate_count"] == 0
    assert report["source_counts"]["failed"] == 1
    assert report["sources"][0]["error_category"] == "http_503"


def test_healthy_zero_new_urls_advances_success_marker(monkeypatch, tmp_path):
    existing = {
        "articles": [{"id": "old", "url": "https://news.test/a", "status": "active"}],
        "last_fetched": "2026-07-19T08:00:00-05:00",
    }
    articles_file = _configure_store(monkeypatch, tmp_path, articles=existing)
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed([_entry("https://news.test/a/")]),
            "https://feed.test/sport": _feed([]),
        },
    )

    result = german_domain.refresh_lesen_feed(
        now=datetime(2026, 7, 20, 10, 0, tzinfo=CHICAGO)
    )

    saved = json.loads(articles_file.read_text())
    assert result["ok"] is True
    assert result["added"] == 0
    assert saved["last_fetched"].startswith("2026-07-20T10:00:00")
    assert saved["articles"] == existing["articles"]


def test_total_source_failure_leaves_pool_and_marker_byte_identical(monkeypatch, tmp_path):
    articles_file = _configure_store(monkeypatch, tmp_path)
    before = articles_file.read_bytes()
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed(bozo=True, error=RuntimeError("down")),
            "https://feed.test/sport": _feed(bozo=True, error=RuntimeError("down")),
        },
    )

    result = german_domain.refresh_lesen_feed(
        now=datetime(2026, 7, 20, 11, 0, tzinfo=CHICAGO)
    )

    assert result["ok"] is False
    assert result["status"] == "total_failure"
    assert result["retry"] is True
    assert articles_file.read_bytes() == before


def test_partial_failure_merges_safe_candidates_without_advancing_marker(monkeypatch, tmp_path):
    articles_file = _configure_store(monkeypatch, tmp_path)
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed([_entry("https://news.test/a")]),
            "https://feed.test/sport": _feed(bozo=True, error=RuntimeError("bad feed")),
        },
    )

    result = german_domain.refresh_lesen_feed(
        now=datetime(2026, 7, 20, 12, 0, tzinfo=CHICAGO)
    )

    saved = json.loads(articles_file.read_text())
    assert result["ok"] is False
    assert result["status"] == "partial_failure"
    assert result["added"] == 1
    assert result["retry"] is True
    assert saved["last_fetched"] is None
    assert len(saved["articles"]) == 1


def test_retry_after_partial_failure_does_not_recategorize_saved_url(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    feeds = {
        "https://feed.test/wien": _feed([_entry("https://news.test/a")]),
        "https://feed.test/sport": _feed(bozo=True, error=RuntimeError("bad feed")),
    }
    monkeypatch.setattr(german_domain.feedparser, "parse", lambda url: feeds[url])
    categorized = []
    monkeypatch.setattr(
        german_domain,
        "categorize_article",
        lambda title, summary, source_category="wien": categorized.append(title) or source_category,
    )

    first = german_domain.refresh_lesen_feed(
        now=datetime(2026, 7, 20, 12, 0, tzinfo=CHICAGO)
    )
    second = german_domain.refresh_lesen_feed(
        now=datetime(2026, 7, 20, 13, 0, tzinfo=CHICAGO)
    )

    assert first["added"] == 1
    assert second["added"] == 0
    assert categorized == ["Neue Geschichte"]


def test_new_ids_are_stable_by_normalized_url_and_historical_ids_remain(monkeypatch, tmp_path):
    historical = {
        "id": "art_legacy_orf_00",
        "url": "https://news.test/history",
        "status": "active",
    }
    articles_file = _configure_store(
        monkeypatch,
        tmp_path,
        articles={"articles": [historical], "last_fetched": None},
    )
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed([_entry("HTTPS://NEWS.TEST/new/?b=2&utm_medium=x&a=1")]),
            "https://feed.test/sport": _feed([_entry("https://news.test/new?a=1&b=2")]),
        },
    )

    result = german_domain.refresh_lesen_feed(
        now=datetime(2026, 7, 20, 13, 0, tzinfo=CHICAGO)
    )

    saved = json.loads(articles_file.read_text())
    assert result["added"] == 1
    assert saved["articles"][0] == historical
    assert saved["articles"][1]["id"] == german_domain._lesen_article_id(
        "https://news.test/new?a=1&b=2", "2026-07-20"
    )
    assert saved["articles"][1]["id"] == german_domain._lesen_article_id(
        "https://news.test/new?a=1&b=2", "2030-01-01"
    )


def test_different_urls_cannot_silently_share_a_new_id(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    _mock_fetch(
        monkeypatch,
        {
            "https://feed.test/wien": _feed([_entry("https://news.test/a")]),
            "https://feed.test/sport": _feed([_entry("https://news.test/b")]),
        },
    )
    monkeypatch.setattr(german_domain, "_lesen_article_id", lambda url, date: "collision")

    with pytest.raises(german_domain.LesenRefreshError, match="collision"):
        german_domain.refresh_lesen_feed(
            now=datetime(2026, 7, 20, 14, 0, tzinfo=CHICAGO)
        )


def test_atomic_replace_failure_leaves_original_file_intact(monkeypatch, tmp_path):
    articles_file = _configure_store(monkeypatch, tmp_path)
    before = articles_file.read_bytes()
    monkeypatch.setattr(german_domain.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("stop")))

    with pytest.raises(OSError, match="stop"):
        german_domain._atomic_write_lesen_articles(
            {"articles": [{"id": "new", "url": "https://news.test/new"}], "last_fetched": None}
        )

    assert articles_file.read_bytes() == before


def test_action_reloads_after_lock_and_preserves_concurrent_refresh(monkeypatch, tmp_path):
    articles_file = _configure_store(
        monkeypatch,
        tmp_path,
        articles={
            "articles": [{"id": "a", "url": "https://news.test/a", "status": "active"}],
            "last_fetched": None,
        },
    )
    real_lock = german_domain._lesen_lock

    @contextmanager
    def concurrent_then_lock(path, timeout=10.0):
        current = json.loads(articles_file.read_text())
        current["articles"].append(
            {"id": "b", "url": "https://news.test/b", "status": "active"}
        )
        _write(articles_file, current)
        with real_lock(path, timeout=timeout):
            yield

    monkeypatch.setattr(german_domain, "_lesen_lock", concurrent_then_lock)
    german_domain.lesen_action("a", "pos")

    saved = json.loads(articles_file.read_text())
    assert [article["id"] for article in saved["articles"]] == ["a", "b"]
    assert saved["articles"][0]["status"] == "dismissed_pos"


def test_lock_timeout_refuses_without_writing(monkeypatch, tmp_path):
    articles_file = _configure_store(monkeypatch, tmp_path)
    before = articles_file.read_bytes()
    lock_path = german_domain._LESEN_REFRESH_LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(german_domain.LesenLockTimeout):
            with german_domain._lesen_lock(lock_path, timeout=0.01):
                pass
    assert articles_file.read_bytes() == before


@pytest.mark.parametrize(
    ("hour", "expected_status"),
    [(5, "outside_window"), (6, "ran"), (21, "ran"), (22, "outside_window")],
)
def test_scheduled_time_gate_uses_chicago(monkeypatch, tmp_path, hour, expected_status):
    _configure_store(monkeypatch, tmp_path)
    calls = []

    def refresh(*, now):
        calls.append(now)
        return {"ok": True, "status": "success", "added": 0}

    exit_code, result = lesen_refresh_cli.run_scheduled_refresh(
        now=datetime(2026, 7, 20, hour, 0, tzinfo=CHICAGO),
        refresh_func=refresh,
    )

    assert result["status"] == expected_status
    assert exit_code == 0
    assert bool(calls) is (expected_status == "ran")


def test_same_day_success_skips_but_failed_attempt_retries(monkeypatch, tmp_path):
    articles_file = _configure_store(
        monkeypatch,
        tmp_path,
        articles={
            "articles": [],
            "last_fetched": "2026-07-20T06:15:00-05:00",
            "last_attempted": "2026-07-20T07:00:00-05:00",
        },
    )
    calls = []
    exit_code, result = lesen_refresh_cli.run_scheduled_refresh(
        now=datetime(2026, 7, 20, 8, 0, tzinfo=CHICAGO),
        refresh_func=lambda **kwargs: calls.append(kwargs),
    )
    assert exit_code == 0
    assert result["status"] == "already_succeeded"
    assert calls == []

    data = json.loads(articles_file.read_text())
    data["last_fetched"] = "2026-07-19T06:15:00-05:00"
    _write(articles_file, data)
    exit_code, result = lesen_refresh_cli.run_scheduled_refresh(
        now=datetime(2026, 7, 20, 8, 0, tzinfo=CHICAGO),
        refresh_func=lambda **kwargs: {"ok": False, "status": "partial_failure"},
    )
    assert exit_code == 1
    assert result["status"] == "partial_failure"


def test_schedule_converts_utc_to_chicago_across_daylight_saving(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    calls = []
    # 11:00 UTC is 06:00 CDT in July and therefore inside the window.
    exit_code, result = lesen_refresh_cli.run_scheduled_refresh(
        now=datetime.fromisoformat("2026-07-20T11:00:00+00:00"),
        refresh_func=lambda **kwargs: calls.append(kwargs) or {
            "ok": True,
            "status": "success",
        },
    )
    assert exit_code == 0
    assert result["status"] == "ran"
    assert calls[0]["now"].hour == 6
    assert calls[0]["now"].utcoffset().total_seconds() == -5 * 3600


def test_refresh_api_blocks_guest_and_reports_retryable_failure(monkeypatch, german_client):
    calls = []
    monkeypatch.setattr(
        html_server,
        "refresh_lesen_feed",
        lambda: calls.append(True) or {
            "ok": False,
            "status": "partial_failure",
            "retry": True,
        },
    )

    guest = german_client.post(
        "/api/lesen-refresh",
        headers={"X-Minimoi-User-Tier": "guest"},
    )
    assert guest.status_code == 403
    assert calls == []

    admin = german_client.post(
        "/api/lesen-refresh",
        headers={"X-Minimoi-User-Tier": "admin"},
    )
    assert admin.status_code == 403
    assert calls == []

    owner = german_client.post(
        "/api/lesen-refresh",
        headers={"X-Minimoi-User-Tier": "owner"},
    )
    assert owner.status_code == 503
    assert owner.get_json()["retry"] is True

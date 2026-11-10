"""A green PR is called ready for Robert's merge only when it merges cleanly and changed lately (#475)."""
from datetime import datetime, timedelta, timezone

from core.supervision import watch

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)


class Store:
    """As tests/supervision/test_supervision.py's FakeStore: no stops, nothing pending."""

    def pending(self):
        return []

    def latest_stops(self):
        return []


class GitHub:
    def __init__(self, detail, updated):
        self.detail, self.updated, self.pulls = detail, updated, 0

    def open_prs(self):
        return [{"number": 470, "title": "Timing test fix", "head": {"sha": "a" * 40, "ref": "b"}, "draft": False,
                 "updated_at": self.updated.isoformat()}]

    def checks(self, sha):
        return [{"status": "completed", "conclusion": "success"}]

    def runs(self):
        return []

    def pull(self, number, sha=None, *, now=None):
        self.pulls += 1
        return self.detail


def _items(detail, updated):
    _summary, items = watch.check_in(Store(), GitHub(detail, updated), now=NOW)
    return [i for i in items if i["key"].startswith("pr-")]


def test_a_conflicting_pr_says_it_needs_a_rebase_not_your_merge():
    [item] = _items({"mergeable": False, "mergeable_state": "dirty"}, NOW - timedelta(days=9))
    assert item["key"].startswith("pr-conflict:470:") and "needs a rebase" in item["text"] and "waits for your merge" not in item["text"]


def test_a_clean_but_old_pr_says_to_check_it_still_applies():
    [item] = _items({"mergeable": True, "mergeable_state": "clean"}, NOW - timedelta(days=9))
    assert item["key"].startswith("pr-stale:470:") and "no change for 9 days" in item["text"]


def test_a_clean_recent_pr_still_waits_for_your_merge():
    [item] = _items({"mergeable": True, "mergeable_state": "clean"}, NOW - timedelta(hours=2))
    assert item["key"].startswith("pr-green:470:") and "waits for your merge" in item["text"]


def test_an_unknown_mergeability_never_invents_a_conflict():
    [item] = _items({"mergeable": None, "mergeable_state": "unknown"}, NOW - timedelta(hours=2))
    assert item["key"].startswith("pr-green:470:")


def test_the_real_reader_keeps_a_prs_detail_an_hour_per_head_commit():
    calls = []
    gh = watch.GitHub()
    gh.get = lambda path, **p: calls.append(path) or {"mergeable": True}
    gh.pull(470, "a" * 40, now=NOW)
    gh.pull(470, "a" * 40, now=NOW + timedelta(minutes=30))
    gh.pull(470, "a" * 40, now=NOW + timedelta(minutes=61))
    gh.pull(470, "b" * 40, now=NOW + timedelta(minutes=61))
    assert calls == ["/pulls/470"] * 3

"""B2: the domain activity records (DAR v2, spec v0.3 section 4.3). Every record written here is validated against
contracts/cos/v1/activity-record.schema.json, so a change to the contract runs these tests. All content is synthetic."""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.activity import config
from core.activity.writer import ActivityWriter, record_digest
from domains.cos.store import contracts
from domains.curator import activity as curator
from domains.german import activity as german
from domains.portuguese import activity as portuguese

CHI = ZoneInfo("America/Chicago")
DAY = "2026-10-30"
OWNER = 7


def clock_at(hour=12):
    return lambda: datetime(2026, 10, 30, hour, 0, 0, tzinfo=CHI)


def valid(record):
    errors = contracts.errors("activity-record", None, record)
    assert not errors, errors
    assert record["digest"] == record_digest(record)
    return record


def item(event_id, hour=10, kind="session"):
    return {"event_id": event_id, "kind": kind, "time_precision": "second",
            "event_time": f"2026-10-30T{hour:02d}:00:00-05:00", "one_line": "x",
            "ref": {"type": "german.session", "id": event_id}}


@pytest.fixture
def writer(tmp_path):
    return ActivityWriter(tmp_path / "german", domain="german", metric="sessions_completed", environment="dev",
                          service="minimoi-german", clock=clock_at())


# ---- the writer's rules --------------------------------------------------------------------------------------------

def test_seq_grows_per_key_and_generation_is_per_process(writer, tmp_path):
    first = valid(writer.publish(DAY, state="partial", items=[item("a")], count=1))
    second = valid(writer.publish(DAY, state="partial", items=[item("b")], count=2))
    assert (first["seq"], second["seq"]) == (1, 2) and first["generation"] == second["generation"]
    restarted = ActivityWriter(tmp_path / "german", domain="german", metric="sessions_completed",
                               environment="dev", service="minimoi-german", clock=clock_at())
    third = valid(restarted.publish(DAY, state="partial", items=[], count=2))
    assert third["seq"] == 3 and third["generation"] != first["generation"]
    assert valid(writer.publish("2026-10-29", state="partial", items=[], count=0))["seq"] == 1


def test_partial_never_retracts_and_complete_withdraws_explicitly(writer):
    writer.publish(DAY, state="partial", items=[item("a"), item("b", 11)], count=2)
    partial = valid(writer.publish(DAY, state="partial", items=[item("c", 12)], count=3))
    assert {i["event_id"] for i in partial["items"]} == {"a", "b", "c"} and partial["withdrawn"] == []
    complete = valid(writer.publish(DAY, state="complete", items=[item("a"), item("c", 12)], count=2, final=True))
    assert {i["event_id"] for i in complete["items"]} == {"a", "c"} and complete["withdrawn"] == ["b"]
    assert complete["observation"]["final"] is True
    assert complete["observation"]["covers"] == {"from": "2026-10-30T00:00:00-05:00",
                                                 "to": "2026-10-30T23:59:59-05:00"}
    again = valid(writer.publish(DAY, state="partial", items=[item("b", 11)], count=2))
    assert "b" not in {i["event_id"] for i in again["items"]} and again["withdrawn"] == ["b"]


def test_unreadable_never_replaces_last_known_items(writer):
    good = writer.publish(DAY, state="partial", items=[item("a")], count=1)
    after = valid(writer.publish(DAY, state="unreadable", error="sessions_unlistable"))
    assert after["items"] == good["items"] and after["count"] == 1 and after["seq"] == 2
    assert after["health"] == {"writer_last_ok": good["health"]["writer_last_ok"], "last_error": "sessions_unlistable"}
    first = valid(writer.publish("2026-10-28", state="unreadable"))
    assert first["count"] is None and first["items"] == [] and first["items_total"] is None


def test_a_long_day_keeps_the_newest_fifty_and_says_so(writer):
    many = [item(f"e{n:02d}", hour=n % 24) for n in range(60)]
    record = valid(writer.publish(DAY, state="partial", items=many, count=60))
    assert len(record["items"]) == 50 and record["items_total"] == 60 and record["items_truncated"] is True


def test_publish_quietly_never_raises(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a folder")
    w = ActivityWriter(blocked, domain="german", metric="sessions_completed", environment="dev",
                       service="minimoi-german", clock=clock_at())
    assert w.publish_quietly(DAY, state="partial", items=[], count=0) is None


def test_files_are_group_readable_and_keep_the_setgid_group(tmp_path):
    root = tmp_path / "activity"
    root.mkdir()
    root.chmod(0o2750)
    w = ActivityWriter(root / "curator", domain="curator", metric="feedback_actions", environment="dev",
                       service="minimoi-curator", clock=clock_at())
    w.publish(DAY, state="partial", items=[], count=0)
    assert stat.S_IMODE((root / "curator").stat().st_mode) == 0o2750
    assert stat.S_IMODE((root / "curator" / f"{DAY}.main.json").stat().st_mode) == 0o640


def test_writers_are_off_until_configured(tmp_path):
    env = {"MINIMOI_ACTIVITY_DIR": str(tmp_path), "MINIMOI_ACTIVITY_ENVIRONMENT": "dev"}
    assert config.writer_from_environ(domain="german", metric="sessions_completed", service="s", environ={}) is None
    assert config.writer_from_environ(domain="german", metric="sessions_completed", service="s",
                                      environ=dict(env, MINIMOI_ACTIVITY_DIR="relative")) is None
    assert config.writer_from_environ(domain="german", metric="sessions_completed", service="s", environ=env)
    assert german.publish(DAY, final=False, state_dir=tmp_path, environ=env) is None      # no owner named
    assert config.safe_id("curator", "liked", "https://x.org/a b?c") == "curator-liked-https:-x.org-a-b-c"


# ---- German -------------------------------------------------------------------------------------------------------

def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


@pytest.fixture
def german_state(tmp_path):
    state = tmp_path / "german-state"
    write(state / "sessions" / "2026-10-30_001.json", {"user_id": OWNER, "date": DAY, "scenario": "Beim Arzt"})
    write(state / "sessions" / "2026-10-31_001.json", {"user_id": OWNER, "date": "2026-10-31",
                                                       "created_at": "2026-10-31T02:30:00+00:00",
                                                       "scenario": "Im Café"})       # 21:30 in Chicago on the 30th
    write(state / "sessions" / "2026-10-30_002.json", {"user_id": 99, "date": DAY, "scenario": "someone else"})
    write(state / "sessions" / "2026-10-30_003.json", {"date": DAY, "mode": "skipped", "skipped": True})
    write(state / "writing_sessions" / f"user_{OWNER}.json", {"entries": [
        {"id": "ws_20261030_150000", "user_id": OWNER, "mode": "free", "timestamp": "2026-10-30T15:00:00-05:00"},
        {"id": "ws_20261029_150000", "user_id": OWNER, "mode": "free", "timestamp": "2026-10-29T15:00:00-05:00"}]})
    write(state / "config" / "phrasebook.json", {"phrases": [
        {"id": "p1", "user": str(OWNER), "last_practiced": "2026-10-30T09:00:00-05:00"},
        {"id": "p2", "user": "99", "last_practiced": "2026-10-30T09:00:00-05:00"}]})
    return state


def env_for(tmp_path, domain):
    return {"MINIMOI_ACTIVITY_DIR": str(tmp_path / "activity" / domain), "MINIMOI_ACTIVITY_ENVIRONMENT": "dev",
            "MINIMOI_ACTIVITY_OWNER_ID": str(OWNER)}


def test_german_counts_the_owners_sessions_only_and_reports_drills_and_writing_as_items(german_state, tmp_path):
    record = valid(german.publish(DAY, final=True, state_dir=german_state, environ=env_for(tmp_path, "german")))
    assert record["count"] == 2 and record["observation"]["state"] == "complete"
    kinds = sorted((i["kind"], i["time_precision"]) for i in record["items"])
    assert kinds == [("drill", "second"), ("session", "day"), ("session", "second"), ("writing", "second")]
    late = next(i for i in record["items"] if i["event_id"] == "german-sess-2026-10-31_001")
    assert late["event_time"] == "2026-10-31T02:30:00+00:00"
    assert not any("someone else" in i["one_line"] for i in record["items"])


def test_german_new_sessions_carry_their_own_time(tmp_path, monkeypatch):
    from domains.german import german_domain
    monkeypatch.setattr(german_domain, "_SESSIONS_DIR", tmp_path / "sessions")
    published = []
    monkeypatch.setattr(german_domain, "_publish_activity", lambda: published.append(True))
    german_domain.save_review_session("A: Hallo", "Anna", "Café", {}, "m", "html", user_id=OWNER)
    (saved,) = (tmp_path / "sessions").glob("*.json")
    created = datetime.fromisoformat(json.loads(saved.read_text())["created_at"])
    assert created.tzinfo is not None and published == [True]


# ---- Portuguese ---------------------------------------------------------------------------------------------------

def test_portuguese_matches_german_on_sessions_and_writing(tmp_path):
    data = tmp_path / "pt"
    write(data / "conversas_sessions" / f"user_{OWNER}.json", {"sessions": [
        {"id": "pt_cs_20261030_100000", "user_id": OWNER, "created_at": "2026-10-30T10:00:00-05:00",
         "scenario": "Na padaria"},
        {"id": "pt_cs_x", "user_id": 99, "created_at": "2026-10-30T10:00:00-05:00"}]})
    write(data / "writing_sessions" / f"user_{OWNER}.json", {"entries": [
        {"id": "pt_ws_1", "user_id": OWNER, "mode": "free", "created_at": "2026-10-30T11:00:00-05:00"}]})
    write(data / "writing_sessions.json", {"entries": [{"id": "legacy", "timestamp": "2026-10-30T11:00:00-05:00"}]})
    record = valid(portuguese.publish(DAY, final=True, data_dir=data, environ=env_for(tmp_path, "portuguese")))
    assert record["count"] == 1
    assert sorted(i["kind"] for i in record["items"]) == ["session", "writing"]


def test_portuguese_unreadable_file_makes_the_day_partial(tmp_path):
    data = tmp_path / "pt"
    (data / "conversas_sessions").mkdir(parents=True)
    (data / "conversas_sessions" / f"user_{OWNER}.json").write_text("{torn")
    record = valid(portuguese.publish(DAY, final=True, data_dir=data, environ=env_for(tmp_path, "portuguese")))
    assert record["observation"]["state"] == "partial" and record["observation"]["final"] is False


# ---- Curator ------------------------------------------------------------------------------------------------------

def test_curator_reports_feedback_actions_on_robert_s_day(tmp_path):
    prefs = tmp_path / "curator_preferences.json"
    prefs.write_text(json.dumps({"feedback_history": {
        "2026-10-30": {"liked": [{"article_id": "h1", "source": "Synthetic Wire", "title": "A title",
                                  "timestamp": "2026-10-30T08:00:00-05:00"}],
                       "disliked": [], "saved": [{"article_id": "h2", "title": "Kept",
                                                  "timestamp": "2026-10-30T09:00:00-05:00"}]},
        "2026-10-31": {"liked": [{"article_id": "h3", "title": "Late", "timestamp": "2026-10-31T03:00:00+00:00"}]},
    }}))
    env = {"MINIMOI_ACTIVITY_DIR": str(tmp_path / "activity" / "curator"), "MINIMOI_ACTIVITY_ENVIRONMENT": "dev"}
    record = valid(curator.publish(DAY, final=True, preferences_file=prefs, environ=env))
    assert record["count"] == 3 and record["metric"] == "feedback_actions"
    assert sorted(i["kind"] for i in record["items"]) == ["feedback_like", "feedback_like", "feedback_save"]
    assert any(i["one_line"] == "Liked · Synthetic Wire: A title" for i in record["items"])


def test_curator_with_no_feedback_publishes_an_honest_zero_and_unreadable_as_null(tmp_path):
    env = {"MINIMOI_ACTIVITY_DIR": str(tmp_path / "activity" / "curator"), "MINIMOI_ACTIVITY_ENVIRONMENT": "dev"}
    zero = valid(curator.publish(DAY, final=True, preferences_file=tmp_path / "missing.json", environ=env))
    assert zero["count"] == 0 and zero["observation"]["state"] == "complete"
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    record = valid(curator.publish("2026-10-29", final=True, preferences_file=bad, environ=env))
    assert record["count"] is None and record["health"]["last_error"] == "preferences_unreadable"
    assert os.path.exists(tmp_path / "activity" / "curator" / "2026-10-29.main.json")

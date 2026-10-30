"""Scheduled briefing delivery is strict and never claims an unproven send (#305).

Unit tests drive core.telegram.telegram_bot.deliver_briefing with scripted fake
Telegram replies. The composed tests run the real EC2 cron wrapper, its real
state check, the real curator_rss_v2.main() (scoring stubbed) and the real
strict sender against a fake Telegram. Nothing reaches a provider, Telegram or
the network.
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "run_curator_cron_ec2.sh"
TODAY = "2026-10-16"


class Reply:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


OK = Reply(200, {"ok": True, "result": {}})


def _briefing_text():
    from domains.curator.curator_rss_v2 import format_output
    articles = [{"hash_id": f"h{n}", "title": f"Synthetic {n}", "source": "Example", "link": f"https://example.com/{n}",
                 "summary": "s", "category": "other", "score": 9.0, "final_score": 9.0, "method": "synthetic",
                 "published": datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)} for n in (1, 2)]
    return format_output(articles)


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    from core.telegram import telegram_bot as tb
    from functools import partial
    monkeypatch.setattr(tb, "deliver_briefing", partial(tb.deliver_briefing, today=TODAY))
    out = tmp_path / "curator_output.txt"
    out.write_text(_briefing_text())
    monkeypatch.setenv("CURATOR_OUTPUT_PATH", str(out))
    monkeypatch.setattr(tb, "DELIVERY_DIR", tmp_path / "telegram_delivery")
    posts, calls = [], []

    def script(*replies):
        queue = list(replies)

        def post(url, json=None, timeout=None, **kwargs):
            posts.append(json)
            calls.append(dict(timeout=timeout, **kwargs))
            reply = queue.pop(0) if queue else OK
            if isinstance(reply, Exception):
                raise reply
            return reply
        monkeypatch.setattr(tb.requests, "post", post)
    script()
    tb.posts, tb.calls, tb.script, tb.out = posts, calls, script, out
    return tb


def _ledger(tb):
    return json.loads((tb.DELIVERY_DIR / f"{TODAY}.json").read_text())


def test_full_delivery_is_acknowledged_without_secrets_or_text(bot):
    assert bot.deliver_briefing("SECRET-TOKEN", "CHAT-777") == 0
    record = _ledger(bot)
    assert record["state"] == "acknowledged" and record["accepted"] == record["planned"] == 4
    raw = (bot.DELIVERY_DIR / f"{TODAY}.json").read_text()
    assert "SECRET-TOKEN" not in raw and "CHAT-777" not in raw and "Synthetic" not in raw
    assert bot.deliver_briefing("SECRET-TOKEN", "CHAT-777") == 0 and len(bot.posts) == 4  # no resend


@pytest.mark.parametrize("token,chat,reason", [("", "c", "missing_config"), ("t", None, "missing_config")])
def test_missing_config_is_definitely_not_sent(bot, token, chat, reason):
    assert bot.deliver_briefing(token, chat) == 1
    assert _ledger(bot)["state"] == "not_sent" and _ledger(bot)["reason"] == reason and bot.posts == []


def test_missing_or_empty_briefing_is_definitely_not_sent(bot):
    bot.out.unlink()
    assert bot.deliver_briefing("t", "c") == 1 and _ledger(bot)["reason"] == "missing_briefing"
    bot.out.write_text("nothing parseable\n")
    assert bot.deliver_briefing("t", "c") == 1 and _ledger(bot)["reason"] == "empty_briefing"
    assert bot.posts == []


def test_rejected_first_message_is_retryable_and_then_acknowledged(bot):
    bot.script(Reply(401, {"ok": False, "description": "Unauthorized"}))
    assert bot.deliver_briefing("t", "c") == 1
    assert _ledger(bot)["state"] == "not_sent" and _ledger(bot)["http_status"] == 401
    assert bot.deliver_briefing("t", "c") == 0
    assert _ledger(bot)["state"] == "acknowledged" and _ledger(bot)["previous_attempts"] == 1


def test_rejection_after_an_accepted_message_is_partial_and_held(bot):
    bot.script(OK, Reply(403, {"ok": False}))
    assert bot.deliver_briefing("t", "c") == 3
    assert _ledger(bot)["state"] == "partial" and _ledger(bot)["accepted"] == 1
    sent = len(bot.posts)
    assert bot.deliver_briefing("t", "c") == 3 and len(bot.posts) == sent  # held, nothing resent


@pytest.mark.parametrize("failure", [requests.exceptions.Timeout(), requests.exceptions.ConnectionError(),
                                     Reply(200, ValueError("not json")), Reply(302, {"ok": True})])
def test_unknown_outcome_is_uncertain_held_and_never_retried(bot, failure):
    bot.script(failure)
    assert bot.deliver_briefing("t", "c") == 3
    assert _ledger(bot)["state"] == "uncertain" and len(bot.posts) == 1  # one attempt, no retry
    assert bot.deliver_briefing("t", "c") == 3 and len(bot.posts) == 1


def test_lost_acknowledgement_write_leaves_the_day_held(bot, monkeypatch):
    real, writes = bot._write_ledger, []

    def flaky(path, record):
        writes.append(record["state"])
        if record["state"] == "acknowledged":
            raise OSError("disk full")
        real(path, record)
    monkeypatch.setattr(bot, "_write_ledger", flaky)
    assert bot.deliver_briefing("t", "c") == 3
    assert _ledger(bot)["state"] == "started" and writes == ["started", "acknowledged"]


def test_failed_start_record_sends_nothing(bot, monkeypatch):
    def broken(_path, _record):
        raise OSError("read-only")
    monkeypatch.setattr(bot, "_write_ledger", broken)
    assert bot.deliver_briefing("t", "c") == 1 and bot.posts == []


def test_unreadable_ledger_is_held(bot):
    bot.DELIVERY_DIR.mkdir(parents=True)
    (bot.DELIVERY_DIR / f"{TODAY}.json").write_text("{broken")
    assert bot.deliver_briefing("t", "c") == 3 and bot.posts == []


# ── composed: real wrapper + real state check + real RSS main + real strict sender ──

FAKE_DOCKER = r"""#!/bin/bash
shift                                   # exec
envs=()
while [ "$1" = "-e" ]; do envs+=("$2"); shift 2; done
shift                                   # container name
cd "$APP"
case "$*" in
  *"from utils.role import role_label"*) echo production; exit 0 ;;
  *"scripts.x.x_pull_incremental"*) echo x_pull >> "$FAKE/calls"; exit 0 ;;
  *"curator_rss_v2.py"*) echo rss >> "$FAKE/calls"; exec "$PY" "$HELPERS/rss_helper.py" ;;
  *"telegram_bot.py --send"*) echo telegram >> "$FAKE/calls"; exec env ${envs[@]+"${envs[@]}"} "$PY" "$HELPERS/tg_helper.py" ;;
  python3\ -c*) echo state >> "$FAKE/calls"; shift; exec "$PY" "$@" ;;
  *) echo "unexpected: $*" >> "$FAKE/calls"; exit 99 ;;
esac
"""

# Never calls `date`: on this fake PATH that would be the fake itself and recurse.
# The day is the test's own UTC TODAY, the same date the ledger lookups use.
FAKE_DATE = r"""#!/bin/bash
case "$*" in *"+%H"*) echo 13 ;; *"+%Y-%m-%d"*) echo __TODAY__ ;; *) echo now ;; esac
""".replace("__TODAY__", TODAY)

RSS_HELPER = """
import os, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, os.environ["REPO"])
from domains.curator import curator_rss_v2 as rss
import signal_store, utils.role
if os.environ.get("FAKE_RSS_CRASH") == "1":
    raise SystemExit("synthetic curation crash")
art = {"hash_id": "abc12", "title": "Synthetic article", "source": "Example", "link": "https://example.com/a",
       "summary": "s", "category": "other", "score": 9.0, "final_score": 9.0, "method": "synthetic",
       "published": datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)}
utils.role.is_production = lambda: True
utils.role.role_label = lambda: "production"
signal_store.get_session_id = lambda: "0123456789"
signal_store.log_article_scored = lambda **_k: None
signal_store.log_priority_match = lambda **_k: None
rss.curate = lambda **_k: ([dict(art)], [dict(art)])
rss.get_active_topic_tags = lambda: {}
rss.find_radar_articles = lambda *_a, **_k: []
rss.format_html = lambda *_a, **_k: "<p>briefing</p>"
rss.generate_index_page = lambda _d: None
rss._DATA_DIR = Path("data/curator").resolve()
rss.__file__ = os.path.join(os.environ["PAGES"], "curator_rss_v2.py")
sys.argv = ["curator_rss_v2.py", "--model=xai"]
rss.main()
stamp = datetime(2026, 10, 16, 13, tzinfo=timezone.utc).timestamp()
os.utime(os.environ["CURATOR_OUTPUT_PATH"], (stamp, stamp))
"""

TG_HELPER = """
import os, sys
sys.path.insert(0, os.environ["REPO"])
import requests, utils.telegram
from core.telegram import telegram_bot as tb
class Reply:
    def __init__(self, status, body): self.status_code, self._body = status, body
    def json(self): return self._body
mode = open(os.environ["TG_MODE_FILE"]).read().strip()
def post(url, json=None, timeout=None, **kwargs):
    assert kwargs == {"allow_redirects": False}
    with open(os.environ["FAKE"] + "/posts", "a") as f: f.write("post\\n")
    if mode == "reject_first":
        return Reply(401, {"ok": False})
    if mode == "timeout_first":
        raise requests.exceptions.Timeout()
    return Reply(200, {"ok": True})
tb.requests.post = post
utils.telegram.get_system_token = lambda: "tkn"
tb.get_chat_id = lambda: "42"
tb.run_send_mode()
"""


@pytest.fixture()
def cron(tmp_path):
    app, helpers, pages, bin_dir = (tmp_path / d for d in ("app", "helpers", "pages", "bin"))
    for d in (app / "data" / "curator", helpers, pages, bin_dir):
        d.mkdir(parents=True)
    (helpers / "rss_helper.py").write_text(RSS_HELPER)
    (helpers / "sitecustomize.py").write_text(CLOCK_BOOTSTRAP)
    (helpers / "tg_helper.py").write_text(TG_HELPER)
    for name, body in (("docker", FAKE_DOCKER), ("date", FAKE_DATE)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    mode = tmp_path / "tg_mode"
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE": str(tmp_path), "APP": str(app),
           "HELPERS": str(helpers), "PAGES": str(pages), "PY": sys.executable, "REPO": str(ROOT),
           "PYTHONPATH": f"{helpers}:{ROOT}", "TG_MODE_FILE": str(mode),
           "CURATOR_OUTPUT_PATH": str(app / "data" / "curator" / "curator_output.txt"),
           "CURATOR_DELIVERY_DIR": str(app / "data" / "curator" / "telegram_delivery")}

    def run(tg_mode, **extra):
        mode.write_text(tg_mode)
        (tmp_path / "calls").write_text("")
        result = subprocess.run(["bash", str(WRAPPER)], env={**env, **extra}, capture_output=True, text=True,
                                timeout=120)
        return result, (tmp_path / "calls").read_text().split()

    def ledger():
        return json.loads((app / "data" / "curator" / "telegram_delivery" / f"{TODAY}.json").read_text())
    run.ledger = ledger
    run.posts = lambda: len((tmp_path / "posts").read_text().split()) if (tmp_path / "posts").exists() else 0
    return run


def test_composed_rejected_delivery_is_retried_without_rescoring(cron):
    result, calls = cron("reject_first")
    assert result.returncode == 1, result.stdout + result.stderr
    assert calls == ["state", "x_pull", "rss", "telegram"]
    assert cron.ledger()["state"] == "not_sent"
    result, calls = cron("ok")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["state", "telegram"]                        # delivery only: no paid rescoring
    assert cron.ledger()["state"] == "acknowledged"
    result, calls = cron("ok")
    assert result.returncode == 0 and calls == ["state"]         # delivered: nothing runs


def test_composed_uncertain_delivery_is_held_for_inspection(cron):
    result, calls = cron("timeout_first")
    assert result.returncode == 1 and "HOLD" in result.stdout
    assert cron.ledger()["state"] == "uncertain" and cron.posts() == 1
    result, calls = cron("ok")
    assert result.returncode == 1 and "HOLD" in result.stdout
    assert calls == ["state"] and cron.posts() == 1               # never resent automatically


def test_composed_curation_crash_reaches_neither_telegram_nor_a_ledger(cron):
    result, calls = cron("ok", FAKE_RSS_CRASH="1")
    assert result.returncode == 1 and calls == ["state", "x_pull", "rss"]
    assert cron.posts() == 0
    result, calls = cron("ok")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["state", "x_pull", "rss", "telegram"] and cron.ledger()["state"] == "acknowledged"


# Only disposable composed child processes load this through their private PATH.
CLOCK_BOOTSTRAP = """
import datetime
class FixedDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 10, 16, 13, tzinfo=datetime.timezone.utc)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)
datetime.datetime = FixedDatetime
"""


def test_default_sender_day_advances_across_utc_midnight(bot, monkeypatch):
    import datetime as clock
    real_datetime = clock.datetime
    class AdvancingClock(real_datetime):
        instant = real_datetime(2026, 10, 16, 23, 59, tzinfo=clock.timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)
    monkeypatch.setattr(clock, "datetime", AdvancingClock)
    # Bypass the fixture's default-day partial to exercise production's clock.
    send = bot.deliver_briefing.func
    assert send("t", "c") == 0
    AdvancingClock.instant = real_datetime(2026, 10, 17, 0, 1, tzinfo=clock.timezone.utc)
    assert send("t", "c") == 0
    for day in ("2026-10-16", "2026-10-17"):
        record = json.loads((bot.DELIVERY_DIR / f"{day}.json").read_text())
        assert record["date"] == day and record["state"] == "acknowledged"
    assert len(bot.posts) == 8

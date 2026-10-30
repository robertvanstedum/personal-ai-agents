"""Production's check-in is byte-for-byte today's when no runtime is configured (host runtime spec v0.5 §3.3, R4.6-b,
R5.6-b; S1-G). ``checkin_golden.json`` was written by the check-in code as it stood at ``5d6c7295`` (before S1-G),
from the fixtures below; this test runs the same fixtures through the code as it is now, with every new argument left
at its default, and compares the summary, the items and the Telegram text exactly. The supervisor's own check-in
(the once-per-key memory and the message it sends) is compared the same way."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.supervision import watch  # noqa: E402

GOLDEN = Path(__file__).with_name("checkin_golden.json")
NOW = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)


class Store:
    """The check-in's store, as the supervision tables would answer: stops, decisions, delegations, exchanges."""

    def __init__(self, stops=(), pending=(), jobs=(), runners=(), exchanges=(), delegations_fail=False):
        self.stops, self._pending, self.jobs, self.runners = list(stops), list(pending), list(jobs), list(runners)
        self.exchanges, self.fail = list(exchanges), delegations_fail
        self.escalated, self.seen, self.checkins = [], set(), []

    def pending(self):
        return self._pending

    def latest_stops(self):
        return self.stops

    def mark_escalated(self, stop_id):
        self.escalated.append(stop_id)

    def delegations(self):
        if self.fail:
            raise OSError("down")
        return {"jobs": self.jobs, "runners": self.runners}

    def recent_exchanges(self, **kw):
        return [e for e in self.exchanges if e["kind"] in (kw.get("kinds") or [e["kind"]])]

    def new_alert_keys(self, keys):
        fresh = [k for k in keys if k not in self.seen]
        self.seen.update(keys)
        return fresh

    def record_checkin(self, summary, told, telegram):
        self.checkins.append({"summary": summary, "told": list(told), "telegram": telegram})
        return {"id": len(self.checkins)}


class GitHub:
    def __init__(self, prs=(), checks=None, runs=(), down=False):
        self.prs, self.check_map, self.run_list, self.down = list(prs), checks or {}, list(runs), down

    def open_prs(self):
        if self.down:
            raise OSError("down")
        return self.prs

    def checks(self, sha):
        return self.check_map.get(sha, [])

    def runs(self):
        return self.run_list


def stop(i, state, minutes, **kw):
    base = {"id": i, "agent": "claude-code", "session": f"session{i:04d}", "state": state, "needs": "", "next": "",
            "decision_key": None, "pr": None, "branch": "b", "nudged_at": None, "escalated_at": None,
            "stopped_at": NOW - timedelta(minutes=minutes)}
    base.update(kw)
    return base


def pr(n, sha, minutes=30, draft=False):
    return {"number": n, "title": f"PR {n}", "draft": draft, "head": {"sha": sha, "ref": f"br{n}"},
            "updated_at": (NOW - timedelta(minutes=minutes)).isoformat()}


def job(i, state, *, created=30, started=None, heartbeat=None, code=None, actor="claude-code", scope="research"):
    rid = f"00000000-0000-4000-8000-{i:012d}"
    return {"request_id": rid, "job_id": f"job-{i}", "actor": actor, "scope": scope, "state": state,
            "created_at": NOW - timedelta(minutes=created),
            "started_at": None if started is None else NOW - timedelta(minutes=started),
            "finished_at": None, "heartbeat_at": None if heartbeat is None else NOW - timedelta(minutes=heartbeat),
            "deadline": NOW + timedelta(hours=1), "brief": f"Brief number {i}: read the code and say what it does " * 2,
            "code": code, "now": NOW}


DONE = {"status": "completed", "conclusion": "success"}
FAIL = {"status": "completed", "conclusion": "failure"}


def cases():
    """Every family of check-in item today's code makes, alone and together."""
    gh = GitHub(prs=[pr(1, "a" * 40), pr(2, "b" * 40), pr(3, "c" * 40, minutes=2), pr(4, "d" * 40, draft=True),
                     pr(7, "e" * 40)],
                checks={"a" * 40: [DONE], "b" * 40: [FAIL], "c" * 40: [DONE], "e" * 40: [DONE]},
                runs=[{"id": 9, "path": ".github/workflows/deploy.yml", "status": "completed", "name": "Deploy",
                       "conclusion": "failure", "updated_at": (NOW - timedelta(hours=1)).isoformat(),
                       "head_branch": "main", "html_url": "https://example.invalid/run/9"},
                      {"id": 10, "path": ".github/workflows/nightly-tests.yml", "status": "completed",
                       "conclusion": "failure", "updated_at": NOW.isoformat()}])
    stops = [stop(1, "done", 90), stop(2, "blocked", 5, needs="the gateway key is missing"),
             stop(3, "awaiting_owner", 5, decision_key="ask-filed000"), stop(4, "awaiting_owner", 5, needs="which port?"),
             stop(5, "waiting_ci", 5, pr=7), stop(6, "unreported", 25), stop(7, "unreported", 10),
             stop(8, "unreported", 60, nudged_at=NOW - timedelta(minutes=30)), stop(9, "awaiting_review", 61),
             stop(10, "error", 3, agent="grok", needs="boom"), stop(11, "waiting_ci", 4, pr=2)]
    jobs = [job(1, "queued", created=6), job(2, "queued", created=2), job(3, "running", started=20, heartbeat=7),
            job(4, "running", started=20, heartbeat=1), job(5, "claimed", created=9, started=None),
            job(6, "failed", code="exit_1"), job(7, "interrupted", code="runner_lost"), job(8, "completed"),
            job(9, "timed_out"), job(10, "refused", code="cost_limit_unenforceable", actor="codex", scope="build"),
            job(11, "output_limit"), job(12, "cancelled")]
    runners = [{"runner_id": "mac-runner-1", "state": "idle", "seen_at": NOW - timedelta(minutes=3), "now": NOW}]
    exchanges = [{"event_key": "r-x:0123abcd", "actor": "grok", "kind": "needs_you", "text": "Which phone path?"}]
    return {
        "everything": (Store(stops, [{"request_key": "ask-filed000", "code": "D-1"}], jobs, runners, exchanges), gh),
        "quiet": (Store(), GitHub()),
        "github_down": (Store(stops[:3]), GitHub(down=True)),
        "delegations_down": (Store(delegations_fail=True), GitHub()),
        "only_delegations": (Store(jobs=jobs, runners=runners), GitHub()),
        "only_stops": (Store(stops), GitHub(prs=[pr(7, "e" * 40)], checks={"e" * 40: [DONE]})),
    }


def render(name, store, github):
    summary, items = watch.check_in(store, github, now=NOW)
    return {"summary": json.loads(json.dumps(summary, sort_keys=True, default=str)), "items": items,
            "message": watch.message(items, summary) if items else None, "escalated": list(store.escalated)}


def supervisor_round(store, github):
    """The supervisor's own check-in, twice (the second tells nothing new), with a bot that keeps what it sends."""
    from services.supervisor.main import Supervisor

    class Bot:
        id = 7000000001

        def __init__(self):
            self.sent = []

        def send(self, chat_id, text, *, reply_to=None):
            self.sent.append([chat_id, text, reply_to])
            return len(self.sent)
    bot = Bot()
    sup = Supervisor(store, bot, github, chat_id=8379221702, user_id=8379221702)
    out = []
    for minutes in (0, 15):
        summary, new = sup.checkin(now=NOW + timedelta(minutes=minutes))
        out.append({"new": new, "summary": json.loads(json.dumps(summary, sort_keys=True, default=str))})
    return {"rounds": out, "sent": bot.sent,
            "checkins": json.loads(json.dumps(store.checkins, sort_keys=True, default=str))}


def golden():
    out = {}
    for name, (store, github) in cases().items():
        out[name] = render(name, store, github)
    for name, (store, github) in cases().items():
        out["supervisor:" + name] = supervisor_round(store, github)
    return out


def test_check_in_output_is_byte_identical_to_todays_for_todays_fixtures():
    expected = GOLDEN.read_bytes()
    got = json.dumps(golden(), sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8") + b"\n"
    assert got == expected


if __name__ == "__main__":          # writes the golden file: run only against the code as it stood before S1-G
    GOLDEN.write_bytes(json.dumps(golden(), sort_keys=True, indent=1, ensure_ascii=False).encode("utf-8") + b"\n")

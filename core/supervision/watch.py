"""One build check-in: open PRs and their CI, failed releases, agents' stop lines and decisions waiting on Robert.

``check_in`` is deterministic (no model): it nudges an agent session with a fixed text only when CI it waits on has
finished, and returns the items Robert must hear about. Each item has a stable key so he is told once, not every 15 minutes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import urllib.parse
import urllib.request

from core.supervision.store import name

REPO = "robertvanstedum/personal-ai-agents"
RELEASE_WORKFLOWS = ("deploy.yml", "deploy-mc-agent.yml", "deploy-iotconnect.yml")
STALL_AFTER = timedelta(minutes=20)        # a turn that ended with no stop line, or open work, and nothing since: Robert hears
REVIEW_WAIT = timedelta(minutes=60)
GREEN_SETTLE = timedelta(minutes=10)
UNCLAIMED_AFTER = timedelta(minutes=5)     # a queued delegation nobody picked up: the Mac runner is not there
SILENT_AFTER = timedelta(minutes=5)        # a running delegation whose runner stopped renewing it


class GitHub:
    """Public, read-only GitHub API (no credential): a check-in costs about 2 + one call per open PR."""

    def __init__(self, repo=REPO, *, opener=None):
        self.repo = repo
        self._open = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def get(self, path, **params):
        url = f"https://api.github.com/repos/{self.repo}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "MiniMoi-supervisor/1",
                                                   "X-GitHub-Api-Version": "2022-11-28"})
        with self._open(req, timeout=15) as r:
            return json.loads(r.read(5_000_000))

    def open_prs(self):
        return self.get("/pulls", state="open", per_page=30)

    def checks(self, sha):
        return self.get(f"/commits/{sha}/check-runs", per_page=100).get("check_runs", [])

    def runs(self):
        return self.get("/actions/runs", per_page=30).get("workflow_runs", [])


def _ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def ci_state(check_runs):
    """'none' | 'running' | 'failed' | 'passed' for one commit's check runs."""
    if not check_runs:
        return "none"
    if any(c.get("status") != "completed" for c in check_runs):
        return "running"
    bad = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
    return "failed" if any(c.get("conclusion") in bad for c in check_runs) else "passed"


def _who(stop):
    return name(stop["agent"]) + f" {stop['session'][:8]}" + \
        (f" ({stop['branch']})" if stop.get("branch") else "")


def check_in(store, github, *, now=None, since=None):
    """Run one check-in. Returns (summary, items); items are {key, kind, text} for Robert, newest facts only."""
    now = now or datetime.now(timezone.utc)
    since = since or now - timedelta(hours=6)
    items, prs, failed_runs, sessions, nudged = [], [], [], [], []
    errors = []

    pr_ci = {}
    try:
        for pr in github.open_prs():
            sha = pr["head"]["sha"]
            state = "draft" if pr.get("draft") else ci_state(github.checks(sha))
            pr_ci[pr["number"]] = state
            prs.append({"number": pr["number"], "title": pr["title"][:120], "ci": state, "sha": sha[:8],
                        "branch": pr["head"].get("ref", "")})
            if state == "failed":
                items.append({"key": f"pr-failed:{pr['number']}:{sha[:12]}", "kind": "error",
                              "text": f"PR #{pr['number']} CI failed at {sha[:8]}: {pr['title'][:100]}"})
            elif state == "passed" and now - _ts(pr["updated_at"]) >= GREEN_SETTLE:
                items.append({"key": f"pr-green:{pr['number']}:{sha[:12]}", "kind": "waiting_on_robert",
                              "text": f"PR #{pr['number']} is green at {sha[:8]} and waits for your merge: "
                                      f"{pr['title'][:100]}"})
    except Exception as exc:
        errors.append(f"pull requests unreadable ({type(exc).__name__})")

    try:
        for run in github.runs():
            path = (run.get("path") or "").rsplit("/", 1)[-1]
            if path in RELEASE_WORKFLOWS and run.get("status") == "completed" and \
                    run.get("conclusion") in ("failure", "timed_out", "startup_failure") and \
                    _ts(run["updated_at"]) >= since:
                failed_runs.append({"id": run["id"], "workflow": path, "branch": run.get("head_branch")})
                items.append({"key": f"release-failed:{run['id']}", "kind": "error",
                              "text": f"Release {run.get('name') or path} failed on {run.get('head_branch')} "
                                      f"({run.get('html_url', '')})"})
    except Exception as exc:
        errors.append(f"workflow runs unreadable ({type(exc).__name__})")

    pending = store.pending()
    asked = {d["request_key"] for d in pending}
    for stop in store.latest_stops():
        age = now - stop["stopped_at"]
        sessions.append({"agent": stop["agent"], "session": stop["session"][:8], "state": stop["state"],
                         "minutes": int(age.total_seconds() // 60), "pr": stop["pr"], "needs": stop["needs"][:200]})
        who = _who(stop)
        if stop["state"] == "done":
            continue
        if stop["state"] in ("blocked", "error"):
            items.append({"key": f"stop:{stop['id']}", "kind": "error" if stop["state"] == "error" else "blocked",
                          "text": f"{who} {stop['state']}: {stop['needs'] or 'no reason given'}"})
        elif stop["state"] == "awaiting_owner":
            if stop["decision_key"] not in asked:   # a filed decision is sent on its own, word for word
                items.append({"key": f"stop:{stop['id']}", "kind": "waiting_on_robert",
                              "text": f"{who} waits on you: {stop['needs'] or 'no question given'}"})
        elif stop["state"] == "waiting_ci" and stop["pr"] and pr_ci.get(stop["pr"]) in ("passed", "failed"):
            if store.nudge(stop["id"], "ci_finished", {"conclusion": pr_ci[stop["pr"]]}):
                nudged.append(stop["session"][:8])
        elif stop["state"] == "awaiting_review" and age >= REVIEW_WAIT:
            items.append({"key": f"review-wait:{stop['id']}", "kind": "stalled",
                          "text": f"{who} has waited {int(age.total_seconds() // 60)} min for a review: "
                                  f"{stop['needs'] or ''}".strip()})
        elif stop["state"] == "unreported" and age >= STALL_AFTER and stop["escalated_at"] is None:
            # No "continue" nudge any more (Robert, 28 Oct: not a permanent feature): a silent stop is told to Robert
            # and MC as stalled, once; nothing wakes the session.
            store.mark_escalated(stop["id"])
            items.append({"key": f"stalled:{stop['id']}", "kind": "stalled",
                          "text": f"{who} stopped {int(age.total_seconds() // 60)} min ago without saying why."})
    running = []
    try:
        work = store.delegations() if hasattr(store, "delegations") else {"jobs": [], "runners": []}
    except Exception as exc:
        work = {"jobs": [], "runners": []}
        errors.append(f"delegations unreadable ({type(exc).__name__})")
    for job in work["jobs"]:
        who = name(job["actor"])
        what = f"{who} {job['scope']} “{job['brief'][:60]}”"
        if job["state"] in ("queued", "claimed", "running"):
            minutes = int((job["now"] - (job["started_at"] or job["created_at"])).total_seconds() // 60)
            running.append({"job": job["job_id"], "agent": job["actor"], "state": job["state"], "minutes": minutes,
                            "brief": job["brief"][:80]})
            if job["state"] == "queued" and job["now"] - job["created_at"] >= UNCLAIMED_AFTER:
                items.append({"key": f"delegation-unclaimed:{job['request_id']}", "kind": "stalled",
                              "text": f"{what} has waited {minutes} min and no runner on the Mac picked it up."})
            elif job["state"] == "running" and job["heartbeat_at"] and job["now"] - job["heartbeat_at"] >= SILENT_AFTER:
                items.append({"key": f"delegation-silent:{job['request_id']}", "kind": "stalled",
                              "text": f"{what} has not reported for "
                                      f"{int((job['now'] - job['heartbeat_at']).total_seconds() // 60)} min."})
        elif job["state"] in ("failed", "refused", "timed_out", "output_limit", "interrupted"):
            items.append({"key": f"delegation-ended:{job['request_id']}", "kind": "error",
                          "text": f"{what} ended {job['state'].replace('_', ' ')}"
                                  + (f" ({job['code']})" if job.get("code") else "") + "."})
    try:     # an agent asking Robert through the ledger (needs-you) reaches him once, word for word
        for e in (store.recent_exchanges(kinds=["needs_you"], limit=10) if hasattr(store, "recent_exchanges") else []):
            items.append({"key": f"exchange-needs:{e['event_key']}", "kind": "waiting_on_robert",
                          "text": f"{name(e['actor'])} asks you: {e['text'][:300]}"})
    except Exception as exc:
        errors.append(f"agent exchanges unreadable ({type(exc).__name__})")
    for err in errors:
        items.append({"key": f"watch-error:{err}:{now:%Y%m%d%H}", "kind": "error", "text": f"Check-in: {err}."})
    summary = {"at": now.isoformat(), "prs": prs, "failed_releases": failed_runs, "sessions": sessions,
               "pending_decisions": [d["code"] for d in pending], "nudged": nudged, "errors": errors,
               "running": running,
               "runners": [{"id": r["runner_id"], "state": r["state"],
                            "seen_minutes_ago": int((r["now"] - r["seen_at"]).total_seconds() // 60)}
                           for r in work["runners"]]}
    return summary, items


LABEL = {"waiting_on_robert": "Waiting on you", "error": "Error", "blocked": "Blocked", "stalled": "Stalled"}


def message(items, summary):
    """The Telegram check-in: what is new, grouped, then one line of state."""
    lines = ["MC check-in"]
    for kind in ("waiting_on_robert", "error", "blocked", "stalled"):
        group = [i["text"] for i in items if i["kind"] == kind]
        if group:
            lines.append(f"\n{LABEL[kind]}:")
            lines.extend(f"• {t}" for t in group)
    if summary.get("running"):
        lines.append("\nRunning:")
        lines.extend(f"• {name(r['agent'])}: {r['brief'][:70]} "
                     f"({r['state']}, {r['minutes']} min)" for r in summary["running"])
    tail = [f"{len(summary['prs'])} open PRs", f"{len(summary['pending_decisions'])} decisions waiting"]
    if summary["nudged"]:
        tail.append(f"nudged {', '.join(summary['nudged'])}")
    lines.append("\n" + " · ".join(tail))
    return "\n".join(lines)

"""One build check-in: open PRs and their CI, failed releases, agents' stop lines and decisions waiting on Robert.

``check_in`` is deterministic (no model) and sends nothing to an agent session: it returns the items Robert must hear
about, a session's finished CI included. Each item has a stable key so he is told once, not every 15 minutes.

Host runtime S1-G (spec v0.5 §3.3, R4.6-b, R5.6-b), each part only when a release configures it; with none, the
check-in is exactly today's (tests/supervision/test_checkin_unchanged.py compares it byte for byte):
* ``runtime`` (core/supervision/runtime.RuntimeLink): the delegation items come from the runtime's snapshot, one rule
  set with today's keys (core/mc_delegation_dispatcher/snapshot.py; without a link the same rules read
  store.delegations(), as today).
* ``sync_view_bound_s``: the Mac sync's mirror (stop lines, the agents' exchanges) is a view; its age is the worker
  credential's last contact. Unknown or past the bound, no rule fires from it, and one ``view-stale`` item says so.
* ``watch`` ({"timeout_s": s}): production's notify-only watch of staging (R5.6-b): ``staging-silent:<host>:<since>``
  once when a watched host's heartbeat is older than the timeout, ``staging-back:<host>:<since>`` once on its return.
  Armed by the first heartbeat (a row), disarmed only by removing the credential and this configuration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import urllib.parse
import urllib.request

from core.mc_delegation_dispatcher.snapshot import legacy_items
from core.supervision.store import name

REPO = "robertvanstedum/personal-ai-agents"
RELEASE_WORKFLOWS = ("deploy.yml", "deploy-mc-agent.yml", "deploy-iotconnect.yml")
STALL_AFTER = timedelta(minutes=20)        # a turn that ended with no stop line, or open work, and nothing since: Robert hears
REVIEW_WAIT = timedelta(minutes=60)
GREEN_SETTLE = timedelta(minutes=10)


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


def check_in(store, github, *, now=None, since=None, runtime=None, sync_view_bound_s=None, watch=None):
    """Run one check-in. Returns (summary, items); items are {key, kind, text} for Robert, newest facts only."""
    now = now or datetime.now(timezone.utc)
    since = since or now - timedelta(hours=6)
    items, prs, failed_runs, sessions = [], [], [], []
    errors = []
    view_stale = None if sync_view_bound_s is None else _sync_view_stale(store, sync_view_bound_s)

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
    if view_stale:          # R4.6-b: the mirror is too old to read absence from; nothing fires from it
        items.append(view_stale)
    for stop in store.latest_stops():
        age = now - stop["stopped_at"]
        sessions.append({"agent": stop["agent"], "session": stop["session"][:8], "state": stop["state"],
                         "minutes": int(age.total_seconds() // 60), "pr": stop["pr"], "needs": stop["needs"][:200]})
        who = _who(stop)
        if stop["state"] == "done" or view_stale:
            continue
        if stop["state"] in ("blocked", "error"):
            items.append({"key": f"stop:{stop['id']}", "kind": "error" if stop["state"] == "error" else "blocked",
                          "text": f"{who} {stop['state']}: {stop['needs'] or 'no reason given'}"})
        elif stop["state"] == "awaiting_owner":
            if stop["decision_key"] not in asked:   # a filed decision is sent on its own, word for word
                items.append({"key": f"stop:{stop['id']}", "kind": "waiting_on_robert",
                              "text": f"{who} waits on you: {stop['needs'] or 'no question given'}"})
        elif stop["state"] == "waiting_ci" and stop["pr"] and pr_ci.get(stop["pr"]) in ("passed", "failed"):
            # No "ci_finished" nudge any more (Robert, 28 Oct, N2): Robert hears that the CI this session waits on has
            # finished, once per conclusion; nothing wakes the session.
            conclusion = pr_ci[stop["pr"]]
            items.append({"key": f"ci-finished:{stop['id']}:{conclusion}", "kind": "stalled",
                          "text": f"{who} waits on PR #{stop['pr']}, whose CI has finished ({conclusion}); "
                                  f"nothing wakes the session."})
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
    if runtime is not None:
        # The runtime's snapshot is the one rule set (§3.3): its notices, today's delegation keys among them.
        try:
            found, running, runners, failed = runtime.check_in()
        except Exception as exc:                # the link never costs the check-in (SF3)
            found, running, runners, failed = [], [], [], [f"runtime link unreadable ({type(exc).__name__})"]
        items.extend(found)
        errors.extend(failed)
    else:
        try:
            work = store.delegations() if hasattr(store, "delegations") else {"jobs": [], "runners": []}
        except Exception as exc:
            work = {"jobs": [], "runners": []}
            errors.append(f"delegations unreadable ({type(exc).__name__})")
        found, running = legacy_items(work["jobs"])
        items.extend(found)
        runners = [{"id": r["runner_id"], "state": r["state"],
                    "seen_minutes_ago": int((r["now"] - r["seen_at"]).total_seconds() // 60)}
                   for r in work["runners"]]
    try:     # an agent asking Robert through the ledger (needs-you) reaches him once, word for word
        for e in (store.recent_exchanges(kinds=["needs_you"], limit=10)
                  if hasattr(store, "recent_exchanges") and not view_stale else []):
            items.append({"key": f"exchange-needs:{e['event_key']}", "kind": "waiting_on_robert",
                          "text": f"{name(e['actor'])} asks you: {e['text'][:300]}"})
    except Exception as exc:
        errors.append(f"agent exchanges unreadable ({type(exc).__name__})")
    if watch is not None:
        try:      # queued in the supervisor's outbox and told by its delivery: listed here, never told twice (P2-3)
            items.extend(dict(i, drained=True) for i in staging_watch(store, timeout_s=watch["timeout_s"]))
        except Exception as exc:
            errors.append(f"staging watch unreadable ({type(exc).__name__})")
    for err in errors:
        items.append({"key": f"watch-error:{err}:{now:%Y%m%d%H}", "kind": "error", "text": f"Check-in: {err}."})
    summary = {"at": now.isoformat(), "prs": prs, "failed_releases": failed_runs, "sessions": sessions,
               "pending_decisions": [d["code"] for d in pending], "errors": errors,
               "running": running, "runners": runners}
    return summary, items


def _sync_view_stale(store, bound_s):
    """R4.6-b: the Mac sync's mirror read as a view, its age the worker credential's last contact (pull included).
    None when fresh; else the one item that says no rule fires from it, keyed by that contact."""
    c = (store.contacts() or {}).get("worker")
    if c is not None and (c["now"] - c["last_at"]).total_seconds() <= bound_s:
        return None
    since = None if c is None else c["last_at"].isoformat()
    return {"key": f"view-stale:mac-sync:{since}", "kind": "error",
            "text": "The Mac sync " + ("has never reported" if c is None else f"last reported at {since}")
                    + "; no rule fires from its stop lines or exchanges until it reports again."}


def staging_watch(store, *, timeout_s, hosts=None):
    """R5.6-b over the watch rows (one per watched host, written only by its heartbeat credential): silent once when
    its last heartbeat is older than ``timeout_s`` (90 s in production, N3), back once when it beats again. A host
    with no row has never beaten: the watch is not armed for it. ``hosts`` names the hosts whose credential is still
    configured: a row of any other host is ignored, so removing the credential alone disarms the watch. Staging's own
    account of itself (its view) is quoted with its age, never acted on: production has no say over dev work.

    Each transition and its notice are written together (``store.watch_transition``: the row's silent_since and the
    notice queued in the supervisor's outbox, in one transaction), so neither is ever lost alone; the notice is told by
    the outbox's delivery, marked sent only once a message carrying it went out (Codex part 2 P2-3). -> the notices
    queued now."""
    out = []
    for row in store.watch_rows():
        if hosts is not None and row["host_id"] not in hosts:
            continue
        host, age = row["host_id"], (row["now"] - row["last_at"]).total_seconds()
        view = row["view"] or {}
        if age > timeout_s and row["silent_since"] is None:
            taken = view.get("as_of")
            told = "of unknown age" if type(taken) not in (int, float) else \
                f"{int(row['now'].timestamp() - taken)} s old"                 # a view carries its age (R4.6)
            item = {"key": f"staging-silent:{host}:{row['last_at'].isoformat()}", "kind": "error",
                    "text": f"Staging {host} has been silent for {int(age)} s (last heartbeat "
                            f"{row['last_at']:%H:%M:%S %Z}). Production only watches: nothing in dev is touched. "
                            f"Its last account ({told}): authority answering {view.get('authority_answering')}, "
                            f"sweeper {view.get('sweeper_age_s')} s old, {view.get('lapsed_always_on')} lapsed "
                            f"always-on items."}
            if store.watch_transition(host, "silent", row["last_at"], item):
                out.append(item)
        elif age <= timeout_s and row["silent_since"] is not None:
            since = row["silent_since"]
            item = {"key": f"staging-back:{host}:{since.isoformat()}", "kind": "error",
                    "text": f"Staging {host} is back: heartbeat {int(age)} s ago, after silence since "
                            f"{since:%H:%M:%S %Z}."}
            if store.watch_transition(host, "back", since, item):
                out.append(item)
    return out


WATCH_TITLE = "MC: staging, watched by production"


def outbox_message(channel, rows) -> str:
    """One message for notices the supervisor's outbox holds on one channel (Codex part 2 P2-3), in the words each
    channel used before it had an outbox: the staging watch's grouped notices, or the runtime link's one line."""
    if channel == "runtime_link":
        return (f"MC: the runtime link failed ({', '.join(r['text'] for r in rows)}). Today's approvals and check-ins "
                "go on; the runtime's decisions and notices wait until it works again.")
    return notices_message(rows, WATCH_TITLE)


LABEL = {"waiting_on_robert": "Waiting on you", "error": "Error", "blocked": "Blocked", "stalled": "Stalled"}


def notices_message(items, title="MC: the runtime"):
    """The runtime's rule notices (and production's staging watch), told as they are raised, between check-ins (S1-G):
    grouped as the check-in groups them, with no state line."""
    lines = [title]
    for kind in ("waiting_on_robert", "error", "blocked", "stalled"):
        group = [i["text"] for i in items if i["kind"] == kind]
        if group:
            lines.append(f"\n{LABEL[kind]}:")
            lines.extend(f"• {t}" for t in group)
    return "\n".join(lines)


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
                     + (f"({r['state']}, {r['minutes']} min)" if r["minutes"] is not None else f"({r['state']})")
                     for r in summary["running"])
    tail = [f"{len(summary['prs'])} open PRs", f"{len(summary['pending_decisions'])} decisions waiting"]
    lines.append("\n" + " · ".join(tail))
    return "\n".join(lines)

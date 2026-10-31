"""The supervision tables (sql/001_supervision.sql): what the Mac sync may write and read, decisions and receipts.

The Mac sync (worker token) can report stop lines and ASK decisions; it can never answer one. Answers are written only
by ``decide``, which the Telegram bridge and the portal's owner-command path call after verifying Robert himself.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
SESSION = re.compile(r"^[A-Za-z0-9._:-]{4,96}$")
AGENTS = ("claude-code", "codex", "grok")
NAMES = {"claude-code": "Claude Code", "codex": "Codex", "grok": "Grok", "mc": "MC", "robert": "Robert"}
EXCHANGE_KINDS = ("ask", "receipt", "progress", "result", "needs_you", "cancel")
EXCHANGE_ACTORS = ("mc", "claude-code", "codex", "grok", "robert")
REQUEST_ID = re.compile(r"^r-[0-9a-f]{20,40}$")
EVENT_ID = re.compile(r"^(ask|[0-9a-f]{8,40})$")
MAX_EXCHANGES = 50


def name(agent):
    """How a person or agent is named to Robert and MC."""
    return NAMES.get(agent, agent)
STATES = ("done", "blocked", "awaiting_owner", "awaiting_review", "waiting_ci", "error", "unreported")
CODE = re.compile(r"^D-?(\d{1,9})$", re.I)
MAX_STOPS, MAX_ASKS = 20, 5

# Fixed texts: a nudge never carries words a model wrote. No kind is left: "continue" and "ci_finished" were both
# retired on 28 Oct 2026 (Robert, N2: nothing wakes an agent session in the 1.2 design), so ``nudge`` refuses every
# kind. The queue, the pull's "nudges" and the ack stay, unused, until the Mac's supervision sync is reinstalled
# without them; the wire keeps its shape until then.
NUDGE_TEXT = {}


class Refused(ValueError):
    pass


def need(ok, code="supervision_request_invalid"):
    if not ok:
        raise Refused(code)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def code_of(decision_id: int) -> str:
    return f"D-{decision_id}"


def id_of(code: str) -> int | None:
    m = CODE.match(code.strip()) if isinstance(code, str) else None
    return int(m.group(1)) if m else None


def _text(value, n, *, required=False):
    need(isinstance(value, str) and len(value) <= n and (value.strip() or not required))
    need(not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", value))
    return value.strip()


def _int(value):
    need(value is None or (type(value) is int and 1 <= value <= 999999))
    return value


def _cursor(conn):
    from psycopg2.extras import RealDictCursor
    return conn.cursor(cursor_factory=RealDictCursor)


def clean_stop(s):
    need(type(s) is dict and set(s) <= {"report_key", "agent", "session", "state", "needs", "next", "decision_key",
                                        "pr", "issue", "branch", "waiting", "stopped_at"})
    need(type(s.get("report_key")) is str and KEY.match(s["report_key"]))
    need(s.get("agent") in AGENTS and type(s.get("session")) is str and SESSION.match(s["session"]))
    need(s.get("state") in STATES)
    need(s.get("decision_key") is None or (type(s["decision_key"]) is str and KEY.match(s["decision_key"])))
    need(type(s.get("stopped_at")) in (int, float) and 1.6e9 < s["stopped_at"] < 4e9)
    need(type(s.get("waiting", False)) is bool)
    return {"report_key": s["report_key"], "agent": s["agent"], "session": s["session"], "state": s["state"],
            "needs": _text(s.get("needs", ""), 500), "next": _text(s.get("next", ""), 500),
            "decision_key": s.get("decision_key"), "pr": _int(s.get("pr")), "issue": _int(s.get("issue")),
            "branch": _text(s.get("branch", ""), 200), "waiting": s.get("waiting", False),
            "stopped_at": datetime.fromtimestamp(s["stopped_at"], timezone.utc)}


def clean_ask(a):
    need(type(a) is dict and set(a) <= {"request_key", "agent", "session", "branch", "text"})
    need(type(a.get("request_key")) is str and KEY.match(a["request_key"]))
    need(a.get("agent") in AGENTS and type(a.get("session")) is str and SESSION.match(a["session"]))
    text = _text(a.get("text"), 1500, required=True)
    need(len(text) >= 10, "decision_text_too_short")
    return {"request_key": a["request_key"], "text": text,
            "requester": {"agent": a["agent"], "session": a["session"], "branch": _text(a.get("branch", ""), 200)}}


def clean_exchange(e):
    """One entry from the agents' request ledger on the Mac (core/agent_requests): an ask or a reply. The words are
    an agent's, kept as data for MC and Robert to read; they never become an instruction or an answer."""
    need(type(e) is dict and set(e) <= {"request_id", "event_id", "kind", "actor", "to", "at", "text"})
    need(type(e.get("request_id")) is str and REQUEST_ID.match(e["request_id"]))
    need(type(e.get("event_id")) is str and EVENT_ID.match(e["event_id"]))
    need(e.get("kind") in EXCHANGE_KINDS and e.get("actor") in EXCHANGE_ACTORS and e.get("to") in EXCHANGE_ACTORS)
    need(type(e.get("at")) in (int, float) and 1.6e9 < e["at"] < 4e9)
    return {"event_key": f"{e['request_id']}:{e['event_id']}", "request_id": e["request_id"], "kind": e["kind"],
            "actor": e["actor"], "recipient": e["to"], "text": _text(e.get("text", ""), 600),
            "at": datetime.fromtimestamp(e["at"], timezone.utc)}


class Store:
    def __init__(self, connect):
        self.connect = connect

    def _tx(self):
        conn = self.connect()
        conn.autocommit = False
        return conn

    def _run(self, fn):
        conn = self._tx()
        try:
            with conn:
                with _cursor(conn) as cur:
                    return fn(cur)
        finally:
            conn.close()

    # ---- the Mac sync (worker token) ----------------------------------------------------------------------------
    def worker(self, body, relay=None):
        """One bounded call from the Mac sync: report | pull | ack. Raises Refused for anything malformed. ``relay``
        (core/supervision/relay.Relay over production's request ledger, or None while MC's front door is off) also
        carries Robert's requests to the agents down and their replies up."""
        from . import relay as R
        need(type(body) is dict and body.get("phase") in ("report", "pull", "ack"))
        phase = body["phase"]
        if phase == "report":
            need(set(body) <= {"phase", "stops", "asks", "exchanges", "relay_events", "listeners"})
            stops = body.get("stops", [])
            asks = body.get("asks", [])
            exchanges = body.get("exchanges", [])
            events = body.get("relay_events", [])
            need(type(stops) is list and len(stops) <= MAX_STOPS and type(asks) is list and len(asks) <= MAX_ASKS)
            need(type(exchanges) is list and len(exchanges) <= MAX_EXCHANGES)
            need(type(events) is list and len(events) <= R.MAX_EVENTS)
            stops, asks = [clean_stop(s) for s in stops], [clean_ask(a) for a in asks]
            exchanges = [clean_exchange(e) for e in exchanges]
            try:
                events = [R.clean_event(e) for e in events]
                listeners = R.clean_listeners(body.get("listeners", {}))
            except R.Refused:
                raise Refused("supervision_request_invalid") from None
            out = self._run(lambda cur: self._report(cur, stops, asks, exchanges))
            if events:
                out["relay_events"] = (relay.apply(events) if relay is not None else
                                       [{"key": f"{e['request_id']}:{e['event_id']}", "state": "refused",
                                         "code": "relay_off"} for e in events])
            if relay is not None and listeners:
                out["listeners"] = relay.listeners(listeners)
            return out
        if phase == "pull":
            need(set(body) == {"phase"})
            out = self._run(self._pull)
            return R.fill(out, relay.pull()) if relay is not None else out
        need(set(body) <= {"phase", "decisions", "nudges", "requests"})
        decisions, nudges = body.get("decisions", []), body.get("nudges", [])
        requests = body.get("requests", [])
        need(type(decisions) is list and len(decisions) <= 50 and all(type(k) is str and KEY.match(k) for k in decisions))
        need(type(nudges) is list and len(nudges) <= 50 and all(type(i) is int and i > 0 for i in nudges))
        need(type(requests) is list and len(requests) <= R.MAX_PULL
             and all(type(r) is str and REQUEST_ID.match(r) for r in requests))
        out = self._run(lambda cur: self._ack(cur, decisions, nudges))
        if requests:
            out["requests"] = relay.ack(requests) if relay is not None else []
        return out

    @staticmethod
    def _report(cur, stops, asks, exchanges=()):
        out = {"stops": [], "asks": [], "exchanges": []}
        for e in exchanges:
            cur.execute("""INSERT INTO guild.supervision_exchanges (event_key, request_id, kind, actor, recipient, text, at)
                VALUES (%(event_key)s, %(request_id)s, %(kind)s, %(actor)s, %(recipient)s, %(text)s, %(at)s)
                ON CONFLICT (event_key) DO NOTHING""", e)
            out["exchanges"].append(e["event_key"])
        for s in stops:
            cur.execute("""INSERT INTO guild.supervision_stops (report_key, agent, session, state, needs, next,
                    decision_key, pr, issue, branch, waiting, stopped_at)
                VALUES (%(report_key)s, %(agent)s, %(session)s, %(state)s, %(needs)s, %(next)s, %(decision_key)s,
                    %(pr)s, %(issue)s, %(branch)s, %(waiting)s, %(stopped_at)s)
                ON CONFLICT (report_key) DO NOTHING""", s)
            out["stops"].append(s["report_key"])
        for a in asks:
            cur.execute("""INSERT INTO guild.supervision_decisions (request_key, requester, text, text_sha256)
                VALUES (%s, %s::jsonb, %s, %s) ON CONFLICT (request_key) DO NOTHING""",
                        (a["request_key"], json.dumps(a["requester"]), a["text"], sha256(a["text"])))
            cur.execute("SELECT id, text_sha256 FROM guild.supervision_decisions WHERE request_key=%s",
                        (a["request_key"],))
            row = cur.fetchone()
            # A reused key with different words is refused for that ask: the first words are the decision.
            out["asks"].append({"request_key": a["request_key"], "code": code_of(row["id"]),
                                "accepted": row["text_sha256"] == sha256(a["text"])})
        return out

    @staticmethod
    def _pull(cur):
        cur.execute("""SELECT id, request_key, requester, state, text_sha256, receipt, decided_at
            FROM guild.supervision_decisions WHERE state IN ('approved', 'declined', 'expired') AND delivered_at IS NULL
            ORDER BY id LIMIT 20""")
        decisions = [{"request_key": r["request_key"], "code": code_of(r["id"]), "state": r["state"],
                      "session": r["requester"].get("session"), "agent": r["requester"].get("agent"),
                      "text_sha256": r["text_sha256"], "receipt": public_receipt(r["receipt"])}
                     for r in cur.fetchall()]
        # Only a kind still in NUDGE_TEXT is delivered: one queued before its kind was retired never reaches a session.
        cur.execute("""SELECT id, agent, session, kind, detail FROM guild.supervision_nudges WHERE delivered_at IS NULL
            AND kind = ANY(%s::text[]) ORDER BY id LIMIT 20""", (sorted(NUDGE_TEXT),))
        nudges = [{"id": r["id"], "agent": r["agent"], "session": r["session"], "kind": r["kind"],
                   "text": NUDGE_TEXT[r["kind"]].format(**r["detail"])} for r in cur.fetchall()]
        return {"decisions": decisions, "nudges": nudges}

    @staticmethod
    def _ack(cur, decisions, nudges):
        if decisions:
            cur.execute("""UPDATE guild.supervision_decisions SET delivered_at=clock_timestamp()
                WHERE request_key = ANY(%s) AND state IN ('approved', 'declined', 'expired') AND delivered_at IS NULL""",
                        (decisions,))
        if nudges:
            cur.execute("""UPDATE guild.supervision_nudges SET delivered_at=clock_timestamp()
                WHERE id = ANY(%s) AND delivered_at IS NULL""", (nudges,))
        return {"acked": True}

    # ---- decisions -------------------------------------------------------------------------------------------------
    def pending(self):
        """Decisions still waiting on Robert (new or sent, not expired), oldest first."""
        def q(cur):
            cur.execute("""UPDATE guild.supervision_decisions SET state='expired'
                WHERE state IN ('new', 'sent') AND expires_at <= clock_timestamp()""")
            cur.execute("""SELECT id, request_key, requester, text, text_sha256, state, telegram_message_id, created_at
                FROM guild.supervision_decisions WHERE state IN ('new', 'sent') ORDER BY id LIMIT 20""")
            return [dict(r, code=code_of(r["id"])) for r in cur.fetchall()]
        return self._run(q)

    def mark_sent(self, decision_id, message_id):
        self._run(lambda cur: cur.execute("""UPDATE guild.supervision_decisions SET state='sent',
            telegram_message_id=%s, sent_at=clock_timestamp() WHERE id=%s AND state='new'""", (message_id, decision_id)))

    def by_message(self, message_id):
        def q(cur):
            cur.execute("SELECT * FROM guild.supervision_decisions WHERE telegram_message_id=%s", (message_id,))
            return cur.fetchone()
        return self._run(q)

    def by_code(self, code):
        did = id_of(code)
        if did is None:
            return None

        def q(cur):
            cur.execute("SELECT * FROM guild.supervision_decisions WHERE id=%s", (did,))
            return cur.fetchone()
        return self._run(q)

    def decide(self, decision_id, answer, receipt, *, text_sha256):
        """Record Robert's verified answer once. Returns the row as decided, or None when it was no longer waiting or
        its words changed. The receipt names the channel and quotes him verbatim."""
        need(answer in ("approved", "declined") and type(receipt) is dict and receipt.get("channel") in ("telegram", "guild"))

        def q(cur):
            cur.execute("""UPDATE guild.supervision_decisions SET state=%s, decided_at=clock_timestamp(),
                    receipt=%s::jsonb
                WHERE id=%s AND state IN ('new', 'sent') AND expires_at > clock_timestamp() AND text_sha256=%s
                RETURNING *""", (answer, json.dumps(receipt, sort_keys=True), decision_id, text_sha256))
            return cur.fetchone()
        return self._run(q)

    def unannounced(self):
        def q(cur):
            cur.execute("""SELECT * FROM guild.supervision_decisions WHERE state IN ('approved', 'declined', 'expired')
                AND announced_at IS NULL ORDER BY id LIMIT 20""")
            return cur.fetchall()
        return self._run(q)

    def mark_announced(self, decision_id):
        self._run(lambda cur: cur.execute(
            "UPDATE guild.supervision_decisions SET announced_at=clock_timestamp() WHERE id=%s", (decision_id,)))

    # ---- telegram offset -------------------------------------------------------------------------------------------
    def offset(self):
        def q(cur):
            cur.execute("SELECT offset_ FROM guild.supervision_telegram WHERE id=1")
            row = cur.fetchone()
            return row["offset_"] if row else 0
        return self._run(q)

    def set_offset(self, value):
        self._run(lambda cur: cur.execute(
            "UPDATE guild.supervision_telegram SET offset_=GREATEST(offset_, %s) WHERE id=1", (value,)))

    # ---- check-ins -------------------------------------------------------------------------------------------------
    def latest_stops(self, *, hours=48):
        """The newest stop line of each agent session reported in the last ``hours``."""
        def q(cur):
            cur.execute("""SELECT DISTINCT ON (agent, session) * FROM guild.supervision_stops
                WHERE stopped_at > clock_timestamp() - make_interval(hours => %s)
                ORDER BY agent, session, stopped_at DESC, id DESC""", (hours,))
            return cur.fetchall()
        return self._run(q)

    def nudge(self, stop_id, kind, detail=None):
        """One fixed nudge for this stop line (once per kind). Returns True when newly queued. Nothing calls it since
        28 Oct (no kind is left in NUDGE_TEXT); it goes with the rest of the nudge plumbing."""
        need(kind in NUDGE_TEXT)

        def q(cur):
            cur.execute("""INSERT INTO guild.supervision_nudges (agent, session, kind, detail, stop_id)
                SELECT agent, session, %s, %s::jsonb, id FROM guild.supervision_stops WHERE id=%s
                ON CONFLICT (stop_id, kind) DO NOTHING RETURNING id""", (kind, json.dumps(detail or {}), stop_id))
            made = cur.fetchone() is not None
            if made:
                cur.execute("UPDATE guild.supervision_stops SET nudged_at=clock_timestamp() WHERE id=%s", (stop_id,))
            return made
        return self._run(q)

    def mark_escalated(self, stop_id):
        self._run(lambda cur: cur.execute(
            "UPDATE guild.supervision_stops SET escalated_at=clock_timestamp() WHERE id=%s", (stop_id,)))

    def new_alert_keys(self, keys):
        """The keys not told before; records them all as seen."""
        def q(cur):
            fresh = []
            for key in keys:
                cur.execute("""INSERT INTO guild.supervision_alerts (key) VALUES (%s)
                    ON CONFLICT (key) DO UPDATE SET last_at=clock_timestamp() RETURNING (xmax = 0) AS inserted""", (key,))
                if cur.fetchone()["inserted"]:
                    fresh.append(key)
            return fresh
        return self._run(q)

    def record_checkin(self, summary, told, telegram):
        def q(cur):
            cur.execute("""INSERT INTO guild.supervision_checkins (summary, told, telegram) VALUES (%s::jsonb, %s, %s)
                RETURNING id, at""", (json.dumps(summary, sort_keys=True, default=str), list(told), telegram))
            return cur.fetchone()
        return self._run(q)

    def delegations(self, *, hours=24):
        """Delegated jobs (core/mc_delegation_dispatcher) active or ended in the last ``hours``, and when each runner
        was last seen. Empty when delegation was never installed here."""
        def q(cur):
            cur.execute("SELECT to_regclass('guild.mc_delegations') IS NOT NULL AS present")
            if not cur.fetchone()["present"]:
                return {"jobs": [], "runners": []}
            cur.execute("""SELECT request_id, job_id, actor, scope, state, created_at, started_at, finished_at,
                    heartbeat_at, deadline, left(brief, 120) AS brief, result_detail->>'code' AS code,
                    clock_timestamp() AS now
                FROM guild.mc_delegations
                WHERE state IN ('queued', 'claimed', 'running')
                   OR finished_at > clock_timestamp() - make_interval(hours => %s)
                ORDER BY created_at DESC LIMIT 30""", (hours,))
            jobs = cur.fetchall()
            cur.execute("""SELECT runner_id, state, seen_at, clock_timestamp() AS now
                FROM guild.mc_delegation_runners ORDER BY seen_at DESC LIMIT 5""")
            return {"jobs": jobs, "runners": cur.fetchall()}
        return self._run(q)

    def recent_exchanges(self, *, hours=24, limit=12, kinds=None):
        """The agents' newest ledger entries (asks and replies), newest first. Empty before the table exists."""
        def q(cur):
            cur.execute("SELECT to_regclass('guild.supervision_exchanges') IS NOT NULL AS present")
            if not cur.fetchone()["present"]:
                return []
            cur.execute("""SELECT event_key, request_id, kind, actor, recipient, text, at FROM guild.supervision_exchanges
                WHERE at > clock_timestamp() - make_interval(hours => %s) AND (%s::text[] IS NULL OR kind = ANY(%s))
                ORDER BY at DESC, id DESC LIMIT %s""", (hours, kinds, kinds, limit))
            return cur.fetchall()
        return self._run(q)

    def checkins_between(self, start, end):
        """For guild-build's activity facts (CoS 1.2 spec section 4.4): how many check-ins in [start, end], and the
        newest one at or before end."""
        def q(cur):
            cur.execute("""SELECT count(*) FILTER (WHERE at >= %s AND at <= %s) AS count,
                                  max(at) FILTER (WHERE at <= %s) AS last_at
                           FROM guild.supervision_checkins""", (start, end, end))
            return cur.fetchone()
        return self._run(q)

    def latest_checkin(self):
        def q(cur):
            cur.execute("SELECT * FROM guild.supervision_checkins ORDER BY id DESC LIMIT 1")
            return cur.fetchone()
        return self._run(q)


def public_receipt(receipt):
    """What the waiting agent is told about the answer: channel, Robert's words, when, and the message it answered."""
    if not receipt:
        return None
    keep = ("channel", "text", "date", "message_id", "reply_to_message_id", "conversation_id", "note_id")
    return {k: receipt[k] for k in keep if k in receipt}

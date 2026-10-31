"""The supervision tables (sql/001_supervision.sql): what the Mac sync may write and read, decisions and receipts.

The Mac sync (worker token) can report stop lines and ASK decisions; it can never answer one. Answers are written only
by ``decide``, which the Telegram bridge and the portal's owner-command path call after verifying Robert himself.

Host runtime S1-G (sql/003, spec v0.5 R3.2-b, R4.6-b, R5.6-b, R7.4-c), each used only when a release turns it on:
* ``worker(..., credential=...)``: the endpoint names the credential that called; its last contact is recorded on
  every call, pull included (``contacts``). The staging watch's credential (``watch:<host_id>``) may only send its
  heartbeat, which writes its own row (``watch_rows``) and nothing else; the worker credential may not send one.
  Without ``credential`` the worker is exactly today's.
* Runtime decisions: an authority decision (core/mc_delegation_dispatcher) gets a D-code as a row here, request key
  ``rt-<decision id>``, requester ``{"agent": "runtime", ...}``, in the authority's own words (``runtime_decision``).
  The Mac sync never pulls one; a batched notice maps one message to its decisions (``add_batch``, ``by_batch``).
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import re

_log = logging.getLogger("supervision")
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
SESSION = re.compile(r"^[A-Za-z0-9._:-]{4,96}$")
AGENTS = ("claude-code", "codex", "grok")
NAMES = {"claude-code": "Claude Code", "codex": "Codex", "grok": "Grok", "mc": "MC", "robert": "Robert"}
EXCHANGE_KINDS = ("ask", "receipt", "progress", "result", "needs_you", "cancel")
EXCHANGE_ACTORS = ("mc", "claude-code", "codex", "grok", "robert")
REQUEST_ID = re.compile(r"^r-[0-9a-f]{20,40}$")
EVENT_ID = re.compile(r"^(ask|[0-9a-f]{8,40})$")
MAX_EXCHANGES = 50
RUNTIME = "runtime"                            # the requester of a decision the runtime authority queued (S1-G)
RUNTIME_KEY = re.compile(r"^rt-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\.r[0-9]{1,3})?$")
RUNTIME_PREFIX = "rt-"                         # reserved for runtime decisions: no agent's ask may use it (N14)
WATCH = re.compile(r"^watch:([A-Za-z0-9._:-]{1,64})$")
PHASES = ("report", "pull", "ack", "heartbeat")
RUNTIME_DECISION_DAYS = 30                     # the authority holds the decision; its D-code lives as long, bounded


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
    need(not a["request_key"].startswith(RUNTIME_PREFIX), "request_key_reserved")     # the runtime's D-codes (N14)
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
    def worker(self, body, relay=None, *, credential=None):
        """One bounded call from the Mac sync: report | pull | ack. Raises Refused for anything malformed. ``relay``
        (core/supervision/relay.Relay over production's request ledger, or None while MC's front door is off) also
        carries Robert's requests to the agents down and their replies up. ``credential`` (S1-G, configured only)
        names the caller: ``worker`` (the Mac sync) or ``watch:<host_id>`` (staging's heartbeat, R5.6-b); its contact
        is recorded first, on every call (R4.6-b), and the watch credential may send only its heartbeat."""
        from . import relay as R
        phases = ("report", "pull", "ack")
        if credential is not None:
            need(credential == "worker" or bool(WATCH.match(credential)), "supervision_credential_invalid")
            phase = body.get("phase") if type(body) is dict and body.get("phase") in PHASES else "invalid"
            self._record_contact(credential, phase)
            if credential != "worker":
                phases = ("heartbeat",)
        need(type(body) is dict and body.get("phase") in phases)
        phase = body["phase"]
        if phase == "heartbeat":
            return self._run(lambda cur: self._heartbeat(cur, WATCH.match(credential).group(1), body))
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

    _contact_failed = False          # one error recorded per process when contacts cannot be written (SF7)

    def _record_contact(self, credential, phase):
        """R4.6-b's contact, in its own transaction. It never costs the call: a database without migration 003 (or any
        failure to write it) skips it, and the first failure is recorded once, by type (SF7)."""
        try:
            self._run(lambda cur: self._contact(cur, credential, phase))
        except Exception as exc:
            if not Store._contact_failed:
                Store._contact_failed = True
                _log.error("supervision contact not recorded (%s): is migration 003 applied?", type(exc).__name__)

    def contacts_ready(self) -> bool:
        """Whether migration 003's contacts table exists (the portal checks it before naming callers, SF7)."""
        def q(cur):
            cur.execute("SELECT to_regclass('guild.supervision_contacts') IS NOT NULL AS present")
            return cur.fetchone()["present"]
        return self._run(q)

    @staticmethod
    def _contact(cur, credential, phase):
        cur.execute("""INSERT INTO guild.supervision_contacts (credential, last_phase) VALUES (%s, %s)
            ON CONFLICT (credential) DO UPDATE SET last_at=clock_timestamp(), last_phase=EXCLUDED.last_phase,
              calls=guild.supervision_contacts.calls + 1""", (credential, phase))

    @staticmethod
    def _heartbeat(cur, host_id, body):
        """Staging's heartbeat (R5.6-b): its own row, keyed by the host its credential names, and nothing else."""
        need(set(body) == {"phase", "host_id", "view"} and body["host_id"] == host_id)
        v = body["view"]
        need(type(v) is dict and set(v) == {"as_of", "authority_answering", "sweeper_age_s", "lapsed_always_on"})
        need(type(v["as_of"]) in (int, float) and 1.6e9 < v["as_of"] < 4e9)
        need(v["authority_answering"] in (True, False, None))
        need(v["sweeper_age_s"] is None or (type(v["sweeper_age_s"]) in (int, float) and 0 <= v["sweeper_age_s"] < 1e8))
        need(v["lapsed_always_on"] is None or (type(v["lapsed_always_on"]) is int and 0 <= v["lapsed_always_on"] < 1e6))
        cur.execute("""INSERT INTO guild.supervision_watch (host_id, credential, view) VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (host_id) DO UPDATE SET last_at=clock_timestamp(), view=EXCLUDED.view""",
                    (host_id, f"watch:{host_id}", json.dumps(v, sort_keys=True)))
        return {"recorded": True}

    @staticmethod
    def _pull(cur):
        # A decision the runtime authority queued is applied by the runtime link, never handed to a Mac session.
        cur.execute("""SELECT id, request_key, requester, state, text_sha256, receipt, decided_at
            FROM guild.supervision_decisions WHERE state IN ('approved', 'declined', 'expired') AND delivered_at IS NULL
              AND requester->>'agent' IS DISTINCT FROM 'runtime'
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
                WHERE request_key = ANY(%s) AND state IN ('approved', 'declined', 'expired') AND delivered_at IS NULL
                  AND requester->>'agent' IS DISTINCT FROM 'runtime'""",
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
            # A runtime decision's end is told by the runtime link, with what the authority did (S1-G, SF2).
            cur.execute("""SELECT * FROM guild.supervision_decisions WHERE state IN ('approved', 'declined', 'expired')
                AND announced_at IS NULL AND requester->>'agent' IS DISTINCT FROM 'runtime' ORDER BY id LIMIT 20""")
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

    def told_keys(self, keys):
        """The keys among ``keys`` told before, read only (the runtime link records a key once its message went out)."""
        if not keys:
            return set()

        def q(cur):
            cur.execute("SELECT key FROM guild.supervision_alerts WHERE key = ANY(%s)", (list(keys),))
            return {r["key"] for r in cur.fetchall()}
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

    # ---- host runtime S1-G (sql/003): runtime decisions, batched notices, contacts, the staging watch -------------
    def runtime_decision(self, decision_id, words, *, notice, position, solo, environment, reissue=None):
        """The D-code of one authority decision, in the authority's own words: a row made once (request key
        ``rt-<decision id>``, or ``rt-<decision id>.r<n>`` when an answer was refused and the decision still waits:
        ``reissue``), never pulled by the Mac sync. Returns the row; refused when the key holds another requester's
        row or other words (the first words are the decision, as for an ask)."""
        need(reissue is None or (type(reissue) is int and 2 <= reissue <= 999), "runtime_decision_invalid")
        key = "rt-" + str(decision_id) + ("" if reissue is None else f".r{reissue}")
        need(bool(RUNTIME_KEY.match(key)) and type(words) is str and 10 <= len(words) <= 1500, "runtime_decision_invalid")
        need(type(notice) is str and len(notice) <= 128 and type(position) is int and 1 <= position <= 5
             and type(solo) is bool and environment in ("dev", "prod"), "runtime_decision_invalid")
        requester = {"agent": RUNTIME, "decision_id": str(decision_id), "notice": notice, "position": position,
                     "solo": solo, "environment": environment}

        def q(cur):
            cur.execute("""INSERT INTO guild.supervision_decisions (request_key, requester, text, text_sha256, expires_at)
                VALUES (%s, %s::jsonb, %s, %s, clock_timestamp() + make_interval(days => %s))
                ON CONFLICT (request_key) DO NOTHING""",
                        (key, json.dumps(requester), words, sha256(words), RUNTIME_DECISION_DAYS))
            cur.execute("SELECT * FROM guild.supervision_decisions WHERE request_key=%s", (key,))
            row = cur.fetchone()
            need(row["requester"].get("agent") == RUNTIME and row["text_sha256"] == sha256(words),
                 "runtime_decision_key_taken")
            return dict(row, code=code_of(row["id"]))
        return self._run(q)

    def add_batch(self, message_id, decision_ids):
        """One message carried these decisions (a batched notice, R7.4-c): each mapped to it once, in order, and sent."""
        need(type(message_id) is int and type(decision_ids) is list and 1 <= len(decision_ids) <= 5
             and len(set(decision_ids)) == len(decision_ids), "batch_invalid")

        def q(cur):
            # A solo decision (an unknown, a reassignment, a cross-vendor admission) never shares a message (N2).
            cur.execute("""SELECT count(*) AS n FROM guild.supervision_decisions WHERE id = ANY(%s)
                AND (requester->>'agent' IS DISTINCT FROM 'runtime' OR requester->>'solo' = 'true')""", (decision_ids,))
            need(cur.fetchone()["n"] == 0, "batch_solo_or_not_runtime")
            for pos, did in enumerate(decision_ids, 1):
                cur.execute("""INSERT INTO guild.supervision_decision_messages (message_id, decision_id, position)
                    VALUES (%s, %s, %s)""", (message_id, did, pos))
            cur.execute("""UPDATE guild.supervision_decisions SET state='sent', sent_at=clock_timestamp()
                WHERE id = ANY(%s) AND state='new'""", (decision_ids,))
        self._run(q)

    def by_batch(self, message_id):
        """The decisions one batched message carried, in its order; [] when it carried none."""
        def q(cur):
            cur.execute("""SELECT d.*, m.position FROM guild.supervision_decision_messages m
                JOIN guild.supervision_decisions d ON d.id = m.decision_id WHERE m.message_id=%s ORDER BY m.position""",
                        (message_id,))
            return [dict(r, code=code_of(r["id"])) for r in cur.fetchall()]
        return self._run(q)

    def batch_of(self, decision_id):
        """The message a batched decision went out in, or None."""
        def q(cur):
            cur.execute("SELECT message_id FROM guild.supervision_decision_messages WHERE decision_id=%s", (decision_id,))
            row = cur.fetchone()
            return None if row is None else row["message_id"]
        return self._run(q)

    def runtime_answered(self):
        """Runtime decisions Robert answered that the runtime link has not applied at the authority yet. Each carries
        ``superseded_by``: the newest later D-code of the same decision, or None. A row that has one was refused at
        the authority and replaced (the replacement's own insert is what consumes it, Codex's fix2 re-check): it must
        never be taken to the authority again, only completed here, so an older answer can't override a newer one."""
        def q(cur):
            cur.execute("""SELECT d.*, (SELECT max(l.id) FROM guild.supervision_decisions l
                    WHERE l.requester->>'agent' = 'runtime' AND l.requester->>'decision_id' = d.requester->>'decision_id'
                      AND l.id > d.id) AS superseded_by_id
                FROM guild.supervision_decisions d WHERE d.requester->>'agent' = 'runtime'
                AND d.state IN ('approved', 'declined') AND d.delivered_at IS NULL ORDER BY d.id LIMIT 50""")
            return [dict(r, code=code_of(r["id"]), superseded_by=None if r["superseded_by_id"] is None
                         else code_of(r["superseded_by_id"])) for r in cur.fetchall()]
        return self._run(q)

    def runtime_untold(self):
        """Runtime decisions whose answer the runtime link applied and whose outcome Robert has not been told yet."""
        def q(cur):
            cur.execute("""SELECT * FROM guild.supervision_decisions WHERE requester->>'agent' = 'runtime'
                AND state IN ('approved', 'declined') AND delivered_at IS NOT NULL AND announced_at IS NULL
                ORDER BY id LIMIT 50""")
            return [dict(r, code=code_of(r["id"])) for r in cur.fetchall()]
        return self._run(q)

    def runtime_open(self):
        """Runtime decisions still answerable here (new or sent, not expired), oldest first."""
        def q(cur):
            cur.execute("""SELECT * FROM guild.supervision_decisions WHERE requester->>'agent' = 'runtime'
                AND state IN ('new', 'sent') AND expires_at > clock_timestamp() ORDER BY id LIMIT 200""")
            return [dict(r, code=code_of(r["id"])) for r in cur.fetchall()]
        return self._run(q)

    def runtime_rows(self, decision_id):
        """Every D-code one authority decision was given, oldest first (a refused answer's re-issues after it)."""
        def q(cur):
            cur.execute("""SELECT * FROM guild.supervision_decisions WHERE requester->>'agent' = 'runtime'
                AND requester->>'decision_id' = %s ORDER BY id""", (str(decision_id),))
            return [dict(r, code=code_of(r["id"])) for r in cur.fetchall()]
        return self._run(q)

    def close_runtime(self, decision_id):
        """The authority closed this decision itself (withdrawn, superseded, resolved another way): its D-code can no
        longer be answered (expired now, and counted as told: the runtime link tells Robert why). True when closed."""
        def q(cur):
            cur.execute("""UPDATE guild.supervision_decisions SET state='expired', expires_at=clock_timestamp(),
                announced_at=clock_timestamp() WHERE id=%s AND state IN ('new', 'sent')
                AND requester->>'agent' = 'runtime' RETURNING id""", (decision_id,))
            return cur.fetchone() is not None
        return self._run(q)

    def mark_applied(self, decision_id):
        """The runtime link took the answer to the authority (its delivery, as the Mac sync's ack is for an ask)."""
        self._run(lambda cur: cur.execute("""UPDATE guild.supervision_decisions SET delivered_at=clock_timestamp()
            WHERE id=%s AND requester->>'agent' = 'runtime'""", (decision_id,)))

    def contacts(self):
        """{credential: {last_at, last_phase, calls, now}} (R4.6-b); {} before contacts are recorded."""
        def q(cur):
            cur.execute("SELECT to_regclass('guild.supervision_contacts') IS NOT NULL AS present")
            if not cur.fetchone()["present"]:
                return {}
            cur.execute("SELECT *, clock_timestamp() AS now FROM guild.supervision_contacts")
            return {r["credential"]: dict(r) for r in cur.fetchall()}
        return self._run(q)

    def watch_rows(self):
        """Staging's heartbeat rows (R5.6-b), each with the database clock."""
        def q(cur):
            cur.execute("SELECT *, clock_timestamp() AS now FROM guild.supervision_watch ORDER BY host_id")
            return cur.fetchall()
        return self._run(q)

    def watch_transition(self, host_id, to, since, notice) -> bool:
        """One staging transition and its notice, in one transaction (Codex part 2 P2-3): ``silent`` sets the row's
        silent_since to ``since`` (from none), ``back`` clears it (from ``since``), and the notice is queued in the
        outbox. False, with nothing queued, when the row had moved already. Robert is told by the outbox's delivery."""
        need(to in ("silent", "back"), "watch_transition_invalid")
        item = _outbox_item(notice)

        def q(cur):
            if to == "silent":
                cur.execute("""UPDATE guild.supervision_watch SET silent_since=%s
                    WHERE host_id=%s AND silent_since IS NULL RETURNING host_id""", (since, host_id))
            else:
                cur.execute("""UPDATE guild.supervision_watch SET silent_since=NULL
                    WHERE host_id=%s AND silent_since=%s RETURNING host_id""", (host_id, since))
            if cur.fetchone() is None:
                return False
            self._queue(cur, "staging_watch", item)
            return True
        return self._run(q)

    # ---- the supervisor's outbox (sql/003 supervision_outbox; Codex part 2 P2-3) --------------------------------
    @staticmethod
    def _queue(cur, channel, item) -> bool:
        cur.execute("""INSERT INTO guild.supervision_outbox (key, channel, kind, text) VALUES (%s, %s, %s, %s)
            ON CONFLICT (key) DO NOTHING RETURNING key""", (item["key"], channel, item["kind"], item["text"]))
        return cur.fetchone() is not None

    def queue_notices(self, channel, items) -> list:
        """Each notice queued once per key: a key queued before, sent or not, is never queued again. -> keys queued."""
        need(channel in OUTBOX_CHANNELS, "outbox_channel_invalid")
        clean = [_outbox_item(i) for i in items]

        def q(cur):
            return [i["key"] for i in clean if self._queue(cur, channel, i)]
        return self._run(q)

    def outbox_unsent(self, limit=50) -> list:
        """Notices queued and not yet sent, oldest first."""
        def q(cur):
            cur.execute("""SELECT key, channel, kind, text, attempts, last_error FROM guild.supervision_outbox
                WHERE sent_at IS NULL ORDER BY queued_at, key LIMIT %s""", (limit,))
            return cur.fetchall()
        return self._run(q)

    def outbox_sent(self, keys):
        """These notices went out in a message: marked sent, once."""
        self._run(lambda cur: cur.execute("""UPDATE guild.supervision_outbox SET sent_at=clock_timestamp()
            WHERE key = ANY(%s) AND sent_at IS NULL""", (list(keys),)))

    def outbox_failed(self, keys, code):
        """A send of these notices failed: counted, with its fixed code; they stay unsent, tried again next tick."""
        self._run(lambda cur: cur.execute("""UPDATE guild.supervision_outbox SET attempts=attempts + 1,
            last_error=%s WHERE key = ANY(%s) AND sent_at IS NULL""", (str(code)[:64], list(keys))))


OUTBOX_CHANNELS = ("staging_watch", "runtime_link")
OUTBOX_KINDS = ("waiting_on_robert", "error", "blocked", "stalled")


def _outbox_item(item) -> dict:
    need(type(item) is dict and type(item.get("key")) is str and 1 <= len(item["key"]) <= 200
         and item.get("kind") in OUTBOX_KINDS and type(item.get("text")) is str and 1 <= len(item["text"]) <= 2000,
         "outbox_item_invalid")
    return {"key": item["key"], "kind": item["kind"], "text": item["text"]}


def public_receipt(receipt):
    """What the waiting agent is told about the answer: channel, Robert's words, when, and the message it answered."""
    if not receipt:
        return None
    keep = ("channel", "text", "date", "message_id", "reply_to_message_id", "conversation_id", "note_id")
    return {k: receipt[k] for k in keep if k in receipt}

"""The phase-1 sources and the rules that turn what they show into items (spec v0.2 §3.3–3.4).

``Sources.collect`` reads every source OUTSIDE the store lock; each source fails alone, so one unreadable store is
"coverage partial" and never stops the others. ``reconcile`` then applies one snapshot to the store in one commit.
Only what keys and words need is read: a guest's email is used to key the request and is never stored.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import store as st
from .settings import AGENT_STALE_S, DECISION_GRACE_S, RESET_TTL_S

_log = logging.getLogger("action_waiting")

# Robert's own MC invite is not waiting on him. Kept equal to core.mc_tools.proposals.INVITE_REASON by a test, so this
# module needs no import of the MC tools package.
INVITE_REASON = "Invited by Robert through Master Craftsman"
APPS = {"portuguese": "Meu Português", "german": "Mein Deutsch"}
AGENT_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "grok": "Grok", "mc": "Master Craftsman"}
FILE_STORES = {"pending": "pending.json", "resets": "reset_requests.json"}
STORES = ("guild", "pending", "resets", "decisions")

_AGENT = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")

_DECISIONS_SQL = ("SELECT id, requester, text, state, created_at, expires_at FROM guild.supervision_decisions"
                  " WHERE state IN ('new', 'sent') AND expires_at > clock_timestamp() ORDER BY id LIMIT %s")
DECISIONS_MAX = 1000          # one read holds at most this many; more is a truncated (partial) read (Codex S2)


def scrub_words(text, limit=1500) -> str:
    """Credentials, then payment details and emails, removed before anything is stored (spec §3.6)."""
    from utils.credential_scrub import scrub as scrub_credentials
    from utils.payment_scrub import scrub as scrub_payment
    cleaned, _changed = scrub_credentials(str(text or ""))
    return scrub_payment(cleaned)[:limit]


def agent_name(agent) -> str:
    return AGENT_NAMES.get(agent, "An agent")


def screened(value, pattern):
    """An identifier (session, branch) only when it matches its pattern AND no scrubber would change it: a value that
    looks like a credential, a card or an email is dropped, never shown or stored (Codex B4)."""
    if not isinstance(value, str) or not pattern.match(value):
        return None
    from utils.credential_scrub import scrub as scrub_credentials
    from utils.payment_scrub import scrub as scrub_payment
    cleaned, changed = scrub_credentials(value)
    return value if not changed and scrub_payment(value) == value else None


def requester_facts(requester) -> dict:
    """Agent, session and branch, each only when it matches its pattern and carries no secret (never free text)."""
    r = requester if isinstance(requester, dict) else {}
    out = {}
    if isinstance(r.get("agent"), str) and _AGENT.match(r["agent"]):
        out["agent"] = r["agent"]
    for name, pattern in (("session", SESSION), ("branch", BRANCH)):
        value = screened(r.get(name), pattern)
        if value:
            out[name] = value
    return out


def epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso(value) -> str | None:
    t = epoch(value)
    return datetime.fromtimestamp(t, timezone.utc).isoformat() if t is not None else None


def identity(path):
    """(device, inode) of a source file, or None when it is missing."""
    try:
        s = os.stat(path)
    except FileNotFoundError:
        return None
    return [s.st_dev, s.st_ino]


@dataclass
class Obs:
    kind: str
    store: str
    key: str
    version: str
    fields: dict = field(default_factory=dict)
    notify_after: float | None = None
    state: str | None = None


@dataclass
class Snapshot:
    """What the sources showed. ``seq`` is the store's change mark read just BEFORE the sources were read (and
    ``started_at`` the time): an item changed after that is newer than this snapshot, and its absence here proves
    nothing. A snapshot with neither falls back to the ``now`` it is reconciled at."""
    seq: int | None = None
    started_at: float | None = None
    obs: list = field(default_factory=list)
    coverage: dict = field(default_factory=dict)     # store → ok | unavailable | missing | skipped
    present: dict = field(default_factory=dict)      # store → keys with an open entry (only when read)
    identities: dict = field(default_factory=dict)   # file store → [dev, ino] | None


class Sources:
    def __init__(self, settings, digest, *, guild_db=None, decision_rows=None, decision_state=None, clock=time.time):
        self.settings = settings
        self.digest = digest
        self.guild_db = guild_db
        self._decision_rows = decision_rows or self._decision_rows_db
        self._decision_state = decision_state or self._decision_state_db
        self.clock = clock

    # ---- keys and ref versions ----------------------------------------------------------------------------------
    def guest_key(self, email) -> str:
        return "guest:" + self.digest("guest", str(email).strip().lower())

    def reset_key(self, username) -> str:
        return "reset:" + self.digest("reset", str(username))

    @staticmethod
    def pending_version(token) -> str:
        return "pending:#" + hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def guild_version(request_id, requested_at) -> str:
        return f"guild:{int(request_id)}@{iso(requested_at) or '-'}"

    # ---- collection ---------------------------------------------------------------------------------------------
    def collect(self) -> Snapshot:
        snap = Snapshot()
        for stores, read in ((("guild", "pending"), self._guests), (("resets",), self._resets),
                             (("decisions",), self._decisions)):
            try:
                read(snap)
            except Exception as exc:                  # each source fails alone (review S5)
                _log.warning("action notices: source %s unreadable (%s)", "/".join(stores), type(exc).__name__)
                for name in stores:
                    snap.coverage.setdefault(name, "unavailable")
        return snap

    def _guests(self, snap):
        from minimoi_portal import auth, guest_requests as gr
        snap.identities["pending"] = identity(auth.AUTH_DIR / FILE_STORES["pending"])
        found = gr.open_with_coverage(self.guild_db)
        rows = found.get(gr.GUILD)
        if rows is None:
            snap.coverage["guild"] = "unavailable"
        else:
            present = set()
            for r in rows:
                if (r.get("reason") or "") == INVITE_REASON or r.get("id") is None:
                    continue
                email = (r.get("email") or "").strip().lower()
                key = self.guest_key(email) if email else f"guest:ref:guild:{int(r['id'])}"
                snap.obs.append(Obs("guest_access", "guild", key, self.guild_version(r["id"], r.get("requested_at")),
                                    {"app": APPS.get(str(r.get("domain") or "").lower()),
                                     "facts": {"requested_at": epoch(r.get("requested_at"))}}))
                present.add(key)
            snap.coverage["guild"], snap.present["guild"] = "ok", present
        entries = found.get(gr.PENDING_FILE)
        if entries is None:
            snap.coverage["pending"] = "unavailable"
        else:
            present = set()
            for e in entries:
                version = self.pending_version(e["token"])
                email = (e.get("email") or "").strip().lower()
                key = self.guest_key(email) if email else "guest:ref:" + version
                snap.obs.append(Obs("guest_access", "pending", key, version,
                                    {"facts": {"requested_at": epoch(e.get("requested_at"))}}))
                present.add(key)
            snap.coverage["pending"] = "ok" if snap.identities["pending"] is not None else "missing"
            snap.present["pending"] = present

    def _resets(self, snap):
        from minimoi_portal import auth
        now = self.clock()
        try:
            with auth._auth_lock():                   # read-only: never the pruning load_reset_requests()
                ident = identity(auth.AUTH_DIR / FILE_STORES["resets"])
                data = auth._load_json(FILE_STORES["resets"], strict=True)
                guests = {g.get("username"): g for g in auth.list_guests()}
        except (auth.AuthStoreError, OSError):
            snap.coverage["resets"] = "unavailable"
            return
        requests = data.get("requests", [])
        if not isinstance(requests, list):
            snap.coverage["resets"] = "unavailable"
            return
        present = set()
        for r in requests:
            if not isinstance(r, dict) or not r.get("id") or not r.get("username"):
                continue
            at = epoch(r.get("requested_at"))
            if at is None or now - at >= RESET_TTL_S:
                continue
            key = self.reset_key(r["username"])
            guest = guests.get(r["username"])
            snap.obs.append(Obs("password_reset", "resets", key, f"reset:{r['id']}",
                                {"facts": {"requested_at": at, "expires_at": at + RESET_TTL_S,
                                           "account_expired": bool(guest and guest.get("expired"))}}))
            present.add(key)
        snap.identities["resets"] = ident
        snap.coverage["resets"] = "ok" if ident is not None else "missing"
        snap.present["resets"] = present

    def _decisions(self, snap):
        if not self.settings.supervision:
            snap.coverage["decisions"] = "skipped"
            return
        rows = self._decision_rows()
        if rows is None:                              # the table is absent: supervision is not installed here
            snap.coverage["decisions"] = "skipped"
            return
        truncated = len(rows) > DECISIONS_MAX
        rows = rows[:DECISIONS_MAX]
        present = set()
        for r in rows:
            did = int(r["id"])
            created = epoch(r.get("created_at")) or self.clock()
            words = scrub_words(r.get("text"))
            facts = {"decision": f"D-{did}", "created_at": created, "expires_at": epoch(r.get("expires_at")),
                     **requester_facts(r.get("requester"))}
            key = f"decision:D-{did}"
            snap.obs.append(Obs("decision", "decisions", key, f"decision:{did}",
                                {"facts": facts, "words": words,
                                 "words_sha256": hashlib.sha256(words.encode("utf-8")).hexdigest()},
                                notify_after=created + DECISION_GRACE_S, state=r.get("state")))
            present.add(key)
        snap.coverage["decisions"] = "truncated" if truncated else "ok"
        snap.present["decisions"] = present

    # ---- the decisions table, read-only ------------------------------------------------------------------------
    @staticmethod
    def _connect():
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5, options="-c statement_timeout=5000",
                                cursor_factory=psycopg2.extras.RealDictCursor)
        conn.set_session(readonly=True, autocommit=True)
        return conn

    def _decision_rows_db(self):
        import psycopg2.errors
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(_DECISIONS_SQL, (DECISIONS_MAX + 1,))
                except psycopg2.errors.UndefinedTable:
                    return None
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def _decision_state_db(self, decision_id):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM guild.supervision_decisions WHERE id=%s", (int(decision_id),))
                row = cur.fetchone()
                return row["state"] if row else None
        finally:
            conn.close()

    def decision_state(self, decision_id):
        """The row's state just before a send (narrows the race with the supervisor; Codex B5). None: unknown."""
        try:
            return self._decision_state(decision_id)
        except Exception as exc:
            _log.warning("action notices: decision re-read failed (%s)", type(exc).__name__)
            return None


# ---- reconcile: one snapshot, one commit -------------------------------------------------------------------------

def reconcile(tx, snap: Snapshot, now) -> dict:
    """Apply a snapshot: create, join, mark by_supervisor, flag source_lost, resolve, mark stale.
    Returns counts (for logs and tests)."""
    data = tx.data
    counts = {"created": 0, "joined": 0, "resolved": 0, "source_lost": 0, "stale": 0}
    open_now = st.open_items(data)

    def newer_than_snapshot(item):
        if snap.seq is not None:
            return int(item.get("changed_seq") or 0) > snap.seq
        start = snap.started_at if snap.started_at is not None else now
        return (item.get("changed_at") or item.get("opened_at") or 0) >= start

    # Missing or replaced source files (Codex B1 on v0.1; independent S3 on the build): never resolve from them; flag
    # what they lost, so Robert sees it and can dismiss it.
    replaced = set()
    for name in FILE_STORES:
        if name not in snap.identities and snap.coverage.get(name) != "ok":
            continue
        current = snap.identities.get(name)
        prev = data["sources"].get(name)
        holds_items = any(st.store_of(r) == name for i in open_now for r in i["refs"])
        if current is None:
            if snap.coverage.get(name) == "missing":
                if holds_items:
                    replaced.add(name)              # its items' records are gone: the same as a replaced file
                else:
                    snap.coverage[name] = "ok"      # a fresh setup with no such file: genuinely nothing there
            continue
        if prev and prev != current and snap.coverage.get(name) == "ok":
            replaced.add(name)
        if prev != current:
            data["sources"][name] = current

    for name in replaced:
        for item in open_now:
            stores = {st.store_of(r) for r in item["refs"]}
            if name not in stores or item["key"] in snap.present.get(name, set()) or newer_than_snapshot(item):
                continue
            alive_elsewhere = any(snap.coverage.get(s) == "ok" and item["key"] in snap.present.get(s, set())
                                  for s in stores - {name})
            if not alive_elsewhere and not item.get("source_lost"):
                item["source_lost"] = True
                tx.event("source_lost", item["code"], reason=name)
                counts["source_lost"] += 1

    # Observations, grouped by key so one email in two stores is one item.
    grouped = {}
    for o in snap.obs:
        grouped.setdefault(o.key, []).append(o)
    for key, obs in grouped.items():
        fields = {"facts": {}}
        for o in obs:
            for k, v in o.fields.items():
                if k == "facts":
                    for fk, fv in v.items():
                        if fv is not None and fields["facts"].get(fk) is None:
                            fields["facts"][fk] = fv
                elif v not in (None, "") and fields.get(k) in (None, ""):
                    fields[k] = v
        notify_after = max((o.notify_after for o in obs if o.notify_after is not None), default=None)
        outcome, item = st.observe(tx, key=key, kind=obs[0].kind, versions=[o.version for o in obs], now=now,
                                   fields=fields, notify_after=notify_after)
        if outcome in counts:
            counts[outcome] += 1
        if item is None:
            continue
        if item["kind"] == "password_reset":           # the latest request sets the expiry
            exp = max((o.fields.get("facts", {}).get("expires_at") or 0 for o in obs), default=0)
            if exp > (item["facts"].get("expires_at") or 0):
                item["facts"]["expires_at"] = exp
            item["facts"]["account_expired"] = any(o.fields.get("facts", {}).get("account_expired") for o in obs)
        if item["kind"] == "decision" and any(o.state == "sent" for o in obs) \
                and item["delivery"]["state"] in ("pending", "failing"):
            item["delivery"]["state"] = "by_supervisor"      # the supervisor's own message counts (D7)
            tx.event("by_supervisor", item["code"])

    # Resolution: an item closes when any store it is in was read completely and no longer holds its key.
    for item in st.open_items(data):
        if item["kind"] == "agent_ask":
            if not item.get("stale") and now - (item.get("opened_at") or now) >= AGENT_STALE_S:
                item["stale"] = True                   # never closes by itself (Codex B4, D9)
                tx.event("stale", item["code"])
                counts["stale"] += 1
            continue
        if item.get("source_lost") or newer_than_snapshot(item):
            continue                                   # recorded or joined after the sources were read: not this sweep
        stores = {st.store_of(r) for r in item["refs"]} - {None}
        for name in sorted(stores):
            # Only a store read completely, and the same file as last time, may say an item is answered.
            if name in replaced or snap.coverage.get(name) != "ok":
                continue
            if item["key"] in snap.present.get(name, set()):
                continue
            expires = item["facts"].get("expires_at")
            expired = item["kind"] in ("password_reset", "decision") and expires is not None and now >= expires
            st.resolve(tx, item, "expired" if expired else "source_closed", now)
            counts["resolved"] += 1
            break
    return counts

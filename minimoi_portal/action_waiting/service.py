"""The notices service: the sweep, ingress from the create paths, agent asks, MC's reads, health (spec v0.2 §3).

It never approves, grants, rejects or decides anything. Its only effects are its own records and ``sendMessage``.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time

from . import sources as src
from . import store as st
from . import words
from .sender import LEGACY_CHAT_ID, Sender, failure_code, load_bot
from .settings import AGENT_OPEN_CAP, DEBOUNCE_S, RESET_TTL_S, RESOLVED_KEEP_S

_log = logging.getLogger("action_waiting")
SOURCE_TIMEOUT_S = 20.0
KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


class Refused(ValueError):
    """A door request that is malformed (400)."""


class KeyConflict(Refused):
    """The same open key with different words (409)."""


class TooManyOpen(Refused):
    """The agent already has AGENT_OPEN_CAP open items (429)."""


def default_legacy_bot():
    """The bot today's direct sends use (TELEGRAM_BOT_TOKEN, else Keychain telegram/polling_bot_token)."""
    from core.get_secret import get_secret
    from core.supervision.telegram import Bot
    return Bot(get_secret("TELEGRAM_BOT_TOKEN", "telegram", "polling_bot_token"))


class Service:
    def __init__(self, settings, *, store=None, sources=None, opener=None, clock=time.time, legacy_bot=None,
                 source_timeout=SOURCE_TIMEOUT_S):
        self.settings = settings
        self.clock = clock
        self.store = store or st.Store(settings.root, clock=clock)
        self.sources = sources or src.Sources(settings, self.store.digest, clock=clock)
        self.sender = Sender(settings, self.store, opener=opener, clock=clock)
        self.legacy_bot = legacy_bot or default_legacy_bot
        self.source_timeout = source_timeout
        self.coverage = {}
        self._inflight = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    # ---- the sweep ----------------------------------------------------------------------------------------------
    def _collect(self) -> src.Snapshot:
        """Each source in its own thread with a time budget: a hung source is 'unavailable' this sweep and never
        stops the rest, agent sends included (T21). A source still running from the last sweep is not started again."""
        snap = src.Snapshot()
        readers = {("guild", "pending"): self.sources._guests, ("resets",): self.sources._resets,
                   ("decisions",): self.sources._decisions}
        started = []
        for names, read in readers.items():
            running = self._inflight.get(names)
            if running is not None and running[0].is_alive():
                for n in names:
                    snap.coverage[n] = "unavailable"
                continue
            part = src.Snapshot()

            def run(read=read, part=part, names=names):
                try:
                    read(part)
                except Exception as exc:
                    _log.warning("action notices: source %s unreadable (%s)", "/".join(names), type(exc).__name__)
                    for n in names:
                        part.coverage[n] = "unavailable"
            thread = threading.Thread(target=run, daemon=True, name="action-notices-source")
            self._inflight[names] = (thread, part)
            thread.start()
            started.append((names, thread, part))
        deadline = time.monotonic() + self.source_timeout
        for names, thread, part in started:
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                for n in names:
                    snap.coverage[n] = "unavailable"
                continue
            self._inflight.pop(names, None)
            snap.obs.extend(part.obs)
            snap.coverage.update(part.coverage)
            snap.present.update(part.present)
            snap.identities.update(part.identities)
            for n in names:
                snap.coverage.setdefault(n, "unavailable")
        return snap

    def sweep(self) -> dict:
        now = self.clock()
        snap = self._collect()
        with self.store.edit() as tx:
            counts = src.reconcile(tx, snap, now)
            self._archive_old(tx, now)
        self.coverage = dict(snap.coverage)
        if self.sender.holds_lock():
            if self.settings.mode == "on":
                self.sender.deliver(self.sources, now)
            else:
                self.sender._set("record_only", now, reason="record_only")
            self.store.write_state({"sweep": {"at": now}, "sender": dict(self.sender.state),
                                    "coverage": dict(snap.coverage)})
        return counts

    def _archive_old(self, tx, now):
        old = [i for i in tx.data["items"].values()
               if i.get("state") == "resolved" and now - (i.get("resolved_at") or now) > RESOLVED_KEEP_S]
        if old:
            self.store.archive(old)
            for item in old:
                tx.data["items"].pop(item["code"], None)

    # ---- the thread ---------------------------------------------------------------------------------------------
    def wake(self):
        self._wake.set()

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="action-notices")
        self._thread.start()

    def stop(self, timeout=5.0):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self.sender.release()

    def _run(self):
        delay, first = 0.0, True
        while not self._stop.is_set():
            if not first:
                woke = self._wake.wait(timeout=delay or self.settings.sweep_seconds)
                if self._stop.is_set():
                    break
                if woke:
                    self._stop.wait(DEBOUNCE_S)          # a burst of wakes groups into one sweep
                    self._wake.clear()
            first = False
            try:
                self.sweep()
                delay = 0.0
            except Exception as exc:                     # never dies; backs off, logs the type only
                delay = min(max(delay * 2, 5.0), 300.0)
                _log.warning("action notices: sweep failed (%s); next in %.0f s", type(exc).__name__, delay)

    # ---- health -------------------------------------------------------------------------------------------------
    def health(self) -> dict:
        mode = self.settings.mode
        if mode == "off":
            return {"healthy": False, "reasons": ["off"]}
        reasons = []
        if mode == "record":
            reasons.append("record_only")
        if self.store.last_commit_ok is False or not self.store.readable():
            reasons.append("store_unavailable")
        if mode == "on":
            bot, _why = load_bot(self.settings)
            if bot is None:
                reasons.append("not_configured")
            state = self.store.read_state()
            beat = (state.get("sweep") or {}).get("at")
            if not isinstance(beat, (int, float)) or self.clock() - beat > 2 * self.settings.sweep_seconds:
                reasons.append("stalled")
            if (state.get("sender") or {}).get("state") == "failing":
                reasons.append("failing")
        return {"healthy": not reasons, "reasons": reasons}

    def suppresses(self) -> bool:
        """D5: today's direct send is skipped only while the service is on and healthy."""
        return self.settings.mode == "on" and self.health()["healthy"]

    def sender_view(self) -> dict:
        """The sender as MC and the API show it."""
        mode = self.settings.mode
        if mode == "off":
            return {"state": "off", "since": None, "last_ok": None, "reason": None}
        if mode == "record":
            return {"state": "record_only", "since": None, "last_ok": None, "reason": None}
        bot, why = load_bot(self.settings)
        state = self.store.read_state()
        sender = state.get("sender") or {}
        if bot is None:
            return {"state": "not_configured", "since": None, "last_ok": sender.get("last_ok"), "reason": why}
        beat = (state.get("sweep") or {}).get("at")
        if not isinstance(beat, (int, float)) or self.clock() - beat > 2 * self.settings.sweep_seconds:
            return {"state": "stalled", "since": beat, "last_ok": sender.get("last_ok"), "reason": None}
        if sender.get("state") == "failing":
            return {"state": "failing", "since": sender.get("since"), "last_ok": sender.get("last_ok"),
                    "reason": sender.get("last_code")}
        return {"state": "ok", "since": None, "last_ok": sender.get("last_ok"), "reason": None}

    # ---- ingress: the create paths record their item in the request (spec §3.5) ----------------------------------
    def record_guest(self, email, *, pending=None, guild=None, app=None):
        """The open item's code for this request, or None (not recorded). Never raises."""
        try:
            email = str(email or "").strip().lower()
            if not email:
                return None
            versions, facts = [], {}
            if pending and pending.get("token"):
                versions.append(src.Sources.pending_version(pending["token"]))
                facts["requested_at"] = src.epoch(pending.get("requested_at"))
            if guild and guild.get("id") is not None:
                versions.append(src.Sources.guild_version(guild["id"], guild.get("requested_at")))
                facts.setdefault("requested_at", src.epoch(guild.get("requested_at")))
            if not versions:
                return None
            key = self.sources.guest_key(email)
            fields = {"app": src.APPS.get(str(app or "").lower()), "facts": facts}
            with self.store.edit() as tx:
                _outcome, item = st.observe(tx, key=key, kind="guest_access", versions=versions, now=self.clock(),
                                            fields=fields)
                return item["code"] if item is not None and item["state"] == "open" else None
        except Exception as exc:
            _log.warning("action notices: guest request not recorded (%s)", type(exc).__name__)
            return None

    def record_reset(self, request, *, account_expired=None):
        try:
            if account_expired is None:
                from minimoi_portal import auth
                guest = next((g for g in auth.list_guests() if g.get("username") == request.get("username")), None)
                account_expired = bool(guest and guest.get("expired"))
            at = src.epoch(request.get("requested_at")) or self.clock()
            key = self.sources.reset_key(request["username"])
            fields = {"facts": {"requested_at": at, "expires_at": at + RESET_TTL_S,
                                "account_expired": bool(account_expired)}}
            with self.store.edit() as tx:
                _outcome, item = st.observe(tx, key=key, kind="password_reset", versions=[f"reset:{request['id']}"],
                                            now=self.clock(), fields=fields)
                if item is not None and (item["facts"].get("expires_at") or 0) < at + RESET_TTL_S:
                    item["facts"]["expires_at"] = at + RESET_TTL_S
                return item["code"] if item is not None and item["state"] == "open" else None
        except Exception as exc:
            _log.warning("action notices: reset request not recorded (%s)", type(exc).__name__)
            return None

    def direct_notice(self, kind, code, *, facts=None, app=None, refs=()):
        """The safe direct notice on the legacy bot (D11): no name or email, sanitized errors. A delivered notice for
        a recorded item marks it told (via direct), so MiniMoi does not send it again (Codex B3)."""
        item = None
        if code:
            try:
                item = self.store.read()["items"].get(code)
            except st.StoreUnavailable:
                item = None
        if item is None:
            item = {"code": code, "kind": kind, "app": src.APPS.get(str(app or "").lower()), "facts": facts or {},
                    "refs": list(refs), "opened_at": self.clock()}
        text = words.direct(self.settings, item, recorded=bool(code))
        try:
            message_id = self.legacy_bot().send(self.settings.chat_id or LEGACY_CHAT_ID, text)
        except Exception as exc:
            _log.warning("action notices: direct notice failed (%s)", failure_code(exc))
            return None
        if code:
            try:
                with self.store.edit() as tx:
                    stored = tx.data["items"].get(code)
                    if stored and stored["delivery"]["state"] in ("pending", "failing"):
                        stored["delivery"].update(state="told", told_at=self.clock(), message_id=message_id,
                                                  via="direct", next_try_at=None, failing_since=None)
                        tx.event("told", code, reason="direct", message_id=message_id)
            except st.StoreUnavailable:
                _log.warning("action notices: direct notice for %s sent but not recorded", code)
        return message_id

    # ---- agent asks (the door, §3.8) ----------------------------------------------------------------------------
    @staticmethod
    def _field(body, name, pattern, *, required=False):
        value = body.get(name)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not pattern.match(value):
            raise Refused(f"{name}_invalid")
        return value

    def deliverability(self, item) -> str:
        if self.settings.mode == "record":
            return "record"
        if item is not None and item["delivery"]["state"] in ("told", "by_supervisor"):
            return "told"
        reasons = self.health()["reasons"]
        for reason in ("not_configured", "failing", "stalled"):
            if reason in reasons:
                return reason
        if "store_unavailable" in reasons:
            return "stalled"
        return "pending"

    def raise_agent(self, agent, body) -> dict:
        if not isinstance(body, dict):
            raise Refused("body_invalid")
        key = self._field(body, "key", KEY, required=True)
        text = body.get("words")
        if not isinstance(text, str) or not 10 <= len(text) <= 1500 or _CONTROL.search(text):
            raise Refused("words_invalid")
        session = self._field(body, "session", src.SESSION)
        branch = self._field(body, "branch", src.BRANCH)
        cleaned = src.scrub_words(text)
        sha = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
        full_key = f"agent:{agent}:{key}"
        now = self.clock()
        with self.store.edit() as tx:
            current = st.by_key(tx.data, full_key)
            if current is not None and current["state"] == "open":
                if current.get("words_sha256") != sha:
                    raise KeyConflict("key_conflict")
                outcome, item = "open", current
            else:
                mine = [i for i in st.open_items(tx.data)
                        if i["kind"] == "agent_ask" and i["facts"].get("agent") == agent and not i.get("stale")]
                if len(mine) >= AGENT_OPEN_CAP:
                    raise TooManyOpen("too_many_open")
                facts = {"agent": agent, **({"session": session} if session else {}),
                         **({"branch": branch} if branch else {})}
                outcome, item = st.open_agent(tx, key=full_key, now=now,
                                              fields={"facts": facts, "words": cleaned, "words_sha256": sha})
            answer = {"code": item["code"], "state": item["state"], "new": outcome == "created"}
            snapshot = dict(item, delivery=dict(item["delivery"]))
        if answer["new"]:
            self.wake()
        answer["delivery"] = self.deliverability(snapshot)
        return answer

    def resolve_agent(self, agent, body) -> dict:
        if not isinstance(body, dict):
            raise Refused("body_invalid")
        key = self._field(body, "key", KEY, required=True)
        with self.store.edit() as tx:
            item = st.by_key(tx.data, f"agent:{agent}:{key}")
            if item is None:
                return {"code": None, "state": "not_found"}
            st.resolve(tx, item, "agent_resolved", self.clock())
            return {"code": item["code"], "state": item["state"]}

    # ---- MC ------------------------------------------------------------------------------------------------------
    def dismiss(self, code):
        """Robert's 'dismiss W-n': (outcome, item) with outcome dismissed | already | refused | not_found."""
        with self.store.edit() as tx:
            item = tx.data["items"].get(code)
            if item is None:
                return "not_found", None
            if item["state"] != "open":
                return "already", dict(item)
            if item["kind"] != "agent_ask" and not item.get("source_lost"):
                return "refused", dict(item)
            st.resolve(tx, item, "owner_dismissed", self.clock())
            return "dismissed", dict(item)

    def coverage_view(self) -> dict:
        unavailable = sorted(n for n, c in self.coverage.items() if c in ("unavailable", "missing"))
        return {"complete": not unavailable, "unavailable": unavailable}

    def item_view(self, item) -> dict:
        now = self.clock()
        facts = item.get("facts") or {}
        d = item.get("delivery") or {}
        requester = None
        if item.get("kind") == "agent_ask":
            requester = {"agent": facts.get("agent"), "name": src.agent_name(facts.get("agent")),
                         "branch": facts.get("branch"), "session": (facts.get("session") or "")[:8] or None}
        return {"code": item["code"], "kind": item["kind"], "state": item["state"], "headline": words.headline(item),
                "words": item.get("words") or "", "app": item.get("app"), "opened_at": src.iso(item.get("opened_at")),
                "age_minutes": int(max(0, now - (item.get("opened_at") or now)) // 60),
                "acts_on": words.acts_on(item), "answer": words.answer(item), "stale": bool(item.get("stale")),
                "source_lost": bool(item.get("source_lost")), "resolution": item.get("resolution"),
                "requester": requester,
                "delivery": {"state": d.get("state"), "attempts": d.get("attempts", 0), "last_code": d.get("last_code"),
                             "failing_since": src.iso(d.get("failing_since")), "told_at": src.iso(d.get("told_at")),
                             "via": d.get("via"), "reminded_at": src.iso(d.get("reminded_at"))}}

    def payload(self, focus=None) -> dict:
        """GET /api/v1/waiting (spec Appendix A)."""
        base = {"schema": "action-waiting/1", "mode": self.settings.mode}
        try:
            data = self.store.read()
        except st.StoreUnavailable:
            return dict(base, available=False, health=self.health(), sender=self.sender_view(),
                        coverage=dict(self.coverage_view(), source_lost=[]), open_count=0, items=[], focus=None)
        items = st.open_items(data)
        found = data["items"].get(focus) if focus else None
        return dict(base, available=True, health=self.health(), sender=self.sender_view(),
                    coverage=dict(self.coverage_view(), source_lost=[i["code"] for i in items if i.get("source_lost")]),
                    open_count=len(items), items=[self.item_view(i) for i in items],
                    focus=self.item_view(found) if found else None)

    def status_line(self) -> str:
        """For `notices status` on the host: counts and states, never words."""
        try:
            data = self.store.read()
        except st.StoreUnavailable as exc:
            return f"mode={self.settings.mode} store={exc}"
        items = st.open_items(data)
        state = self.store.read_state()
        beat = (state.get("sweep") or {}).get("at")
        age = f"{int(self.clock() - beat)}s" if isinstance(beat, (int, float)) else "never"
        sender = self.sender_view()
        return (f"mode={self.settings.mode} open={len(items)} pending="
                f"{sum(1 for i in items if i['delivery']['state'] in ('pending', 'failing'))} "
                f"sender={sender['state']}{'(' + sender['reason'] + ')' if sender.get('reason') else ''} "
                f"heartbeat_age={age} next_code=W-{data['next_code']}")

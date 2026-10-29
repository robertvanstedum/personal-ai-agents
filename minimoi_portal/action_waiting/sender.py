"""Telegram delivery for the notices service (spec v0.2 §3.5–3.6).

Send-only, through ``core.supervision.telegram.Bot`` (token kept in the object; errors never echo the URL). Only the
holder of ``.sender.lock`` sends. An item is marked told only after ``sendMessage`` returned a message id; a message
sent whose record could not be committed is held in memory and never sent again by this process. A failed send keeps
its item and retries with backoff, forever. Recorded failure codes are fixed strings, never text from the error.
"""
from __future__ import annotations

import fcntl
import logging
import os
import re
import stat
from pathlib import Path

from . import store as st
from . import words
from .settings import BACKOFF_FIRST_S, BACKOFF_MAX_S, GROUP_MAX

_log = logging.getLogger("action_waiting")
TOKEN = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")
_CODE = re.compile(r"^telegram_sendMessage_(?:failed:[A-Za-z_][A-Za-z0-9_]{0,60}|refused)$")
_DECISION = re.compile(r"^D-(\d{1,9})$")
LEGACY_CHAT_ID = 8379221702                          # today's hard-coded chat (app.py), for the safe direct notice


def failure_code(exc) -> str:
    text = str(exc) if isinstance(exc, RuntimeError) else ""
    return text if _CODE.match(text) else f"send_failed:{type(exc).__name__}"


def backoff(attempts: int) -> float:
    return float(min(BACKOFF_FIRST_S * 2 ** max(0, attempts - 1), BACKOFF_MAX_S))


def load_bot(settings, *, opener=None):
    """(Bot, None) when the sender is configured, else (None, fixed reason). Read each sweep, so a fixed token file
    is picked up without a restart. A token file readable by group or others is refused (Codex S4)."""
    if settings.mode != "on":
        return None, "record_only" if settings.mode == "record" else "off"
    if not settings.env_agrees():
        return None, "env_mismatch"
    if settings.chat_id is None:
        return None, "chat_id_invalid"
    if not settings.token_file:
        return None, "token_file_unset"
    try:
        info = os.stat(settings.token_file)
    except FileNotFoundError:
        return None, "token_file_missing"
    except OSError:
        return None, "token_file_unreadable"
    if not stat.S_ISREG(info.st_mode):
        return None, "token_file_not_a_file"
    if info.st_mode & 0o077:
        return None, "token_file_permissions"
    try:
        token = Path(settings.token_file).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None, "token_file_unreadable"
    if not TOKEN.match(token):
        return None, "token_invalid"
    from core.supervision.telegram import Bot
    try:
        bot = Bot(token, opener=opener)
    except ValueError:
        return None, "token_invalid"
    if settings.bot_id is not None and bot.id != settings.bot_id:
        return None, "bot_id_mismatch"
    return bot, None


class Sender:
    def __init__(self, settings, store, *, opener=None, clock):
        self.settings = settings
        self.store = store
        self.opener = opener
        self.clock = clock
        self._lock_fd = None
        self._held = {}                                 # code → (message_id, reminder): sent, record not committed
        self.state = {"state": "ok", "since": None, "last_ok": None, "last_code": None, "reason": None}

    # ---- the one sender -----------------------------------------------------------------------------------------
    def holds_lock(self) -> bool:
        if self._lock_fd is not None:
            return True
        if self.store.root is None:
            return False
        try:
            self.store.root.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.store.root / ".sender.lock", os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        return True

    def release(self):
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    # ---- state --------------------------------------------------------------------------------------------------
    def _set(self, state, now, *, reason=None, code=None):
        if self.state["state"] != state:
            self.state["since"] = now
        self.state.update(state=state, reason=reason)
        if code is not None:
            self.state["last_code"] = code

    def _ok(self, now):
        self._set("ok", now)
        self.state["last_ok"] = now

    # ---- delivery -----------------------------------------------------------------------------------------------
    def deliver(self, sources, now):
        """Tell what is due, then the reminders that are due. Called by the sweep, from the sender-lock holder."""
        bot, reason = load_bot(self.settings, opener=self.opener)
        if bot is None:
            self._set("not_configured" if reason not in ("record_only", "off") else reason, now, reason=reason)
            return
        if self.state["state"] in ("not_configured", "record_only", "off"):
            self._set("ok", now)
        self._commit_held(now)
        data = self.store.read()
        due = [i for i in st.open_items(data)
               if i["delivery"]["state"] in ("pending", "failing") and i["code"] not in self._held
               and (i["delivery"].get("next_try_at") or 0) <= now and (i["delivery"].get("notify_after") or 0) <= now]
        due = self._recheck_decisions(sources, due, now)
        for start in range(0, len(due), GROUP_MAX):
            self._send(bot, due[start:start + GROUP_MAX], now, reminder=False)
        hours = self.settings.remind_hours
        if hours > 0:
            data = self.store.read()
            reminders = [i for i in st.open_items(data)
                         if i["delivery"]["state"] == "told" and not i["delivery"].get("reminded_at")
                         and i["code"] not in self._held
                         and (i["delivery"].get("told_at") or now) + hours * 3600 <= now
                         and (i["delivery"].get("remind_next_try_at") or 0) <= now]
            for start in range(0, len(reminders), GROUP_MAX):
                self._send(bot, reminders[start:start + GROUP_MAX], now, reminder=True)

    def _recheck_decisions(self, sources, due, now):
        """A D-code is re-read just before it is sent: if the supervisor sent it meanwhile, its message counts."""
        keep, by_supervisor = [], []
        for item in due:
            if item["kind"] != "decision" or sources is None:
                keep.append(item)
                continue
            m = _DECISION.match(str(item["facts"].get("decision") or ""))
            state = sources.decision_state(int(m.group(1))) if m else None
            if state == "sent":
                by_supervisor.append(item["code"])
            elif state in (None, "new"):
                keep.append(item)
        if by_supervisor:
            with self.store.edit() as tx:
                for code in by_supervisor:
                    item = tx.data["items"].get(code)
                    if item and item["delivery"]["state"] in ("pending", "failing"):
                        item["delivery"]["state"] = "by_supervisor"
                        tx.event("by_supervisor", code)
        return keep

    def _send(self, bot, items, now, *, reminder):
        codes = [i["code"] for i in items]
        text = (words.single(self.settings, items[0], reminder=reminder) if len(items) == 1
                else words.group(self.settings, items, reminder=reminder))
        try:
            message_id = bot.send(self.settings.chat_id, text)
        except Exception as exc:                       # an ambiguous outcome counts as a failure: retried (Codex B5)
            code = failure_code(exc)
            _log.warning("action notices: send failed (%s) for %s", code, ",".join(codes))
            with self.store.edit() as tx:
                for c in codes:
                    item = tx.data["items"].get(c)
                    if item is None:
                        continue
                    d = item["delivery"]
                    if reminder:
                        d["remind_attempts"] = d.get("remind_attempts", 0) + 1
                        d["remind_next_try_at"] = now + backoff(d["remind_attempts"])
                    else:
                        d["attempts"] = d.get("attempts", 0) + 1
                        d["next_try_at"] = now + backoff(d["attempts"])
                        d["state"] = "failing"
                        d["failing_since"] = d.get("failing_since") or now
                    d["last_code"] = code
                    tx.event("send_failed", c, reason=code)
            self._set("failing", now, code=code)
            return False
        for c in codes:
            self._held[c] = (message_id, reminder)
        self._ok(now)
        self._commit_held(now)
        return True

    def _commit_held(self, now):
        """Record sends whose record is not yet committed. Never sends; a failure keeps them held."""
        if not self._held:
            return
        held = dict(self._held)
        try:
            with self.store.edit() as tx:
                for code, (message_id, reminder) in held.items():
                    item = tx.data["items"].get(code)
                    if item is None:
                        continue
                    d = item["delivery"]
                    if reminder:
                        d["reminded_at"] = now
                        tx.event("reminded", code, message_id=message_id)
                    else:
                        d.update(state="told", told_at=now, message_id=message_id, via="notices",
                                 next_try_at=None, failing_since=None)
                        tx.event("told", code, message_id=message_id)
        except st.StoreUnavailable:
            _log.warning("action notices: %d sent notice(s) not yet recorded; held, not resent", len(held))
            return
        for code in held:
            self._held.pop(code, None)

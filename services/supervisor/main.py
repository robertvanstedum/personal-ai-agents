"""The build supervisor (#365): one process, the only poller of the approvals bot.

  * sends each decision an agent asked, word for word, to Robert's private chat, and records the message it went in;
  * reads his replies; only his own reply to that exact message answers it (core/supervision/telegram.py), and the
    receipt keeps his words; a Guild-chat answer (portal) lands in the same record and is announced here;
  * every CHECKIN_MINUTES runs one check-in (core/supervision/watch.py): nudges agents with fixed texts on work that
    does not need him, and tells him once about what does, about errors and stalls.

No model, no approval of its own. Environment: DATABASE_URL, TELEGRAM_CHAT_ID (Robert's private chat; his user id is
the same number unless MINIMOI_OWNER_TELEGRAM_USER_ID says otherwise), and the approvals bot's token (``_token``).

Host runtime S1-G, each off unless a release sets it (then see core/supervision/runtime.py and watch.py):
  * MINIMOI_RUNTIME_*: the link to this environment's runtime authority. Its decisions go out as D-codes (a routine
    notice's together in one message, a solo one alone); a reply answers them only as plainly as it says (the whole
    "yes" or "no", or one code at a time; "wait" changes nothing); each answer is taken to the authority with its
    receipt and Robert is told what it did; its rule notices are told as they are raised; and the check-in's
    delegation items come from the runtime's snapshot;
  * MINIMOI_SUPERVISION_SYNC_VIEW_BOUND_S: the Mac sync's mirror is read with its age (R4.6-b);
  * MINIMOI_SUPERVISION_WATCH_TOKEN_SHA256 with MINIMOI_SUPERVISION_WATCH_HOST (the same credential the portal
    takes; MINIMOI_SUPERVISION_WATCH_TIMEOUT_S, default 90): production's notify-only watch of staging (R5.6-b),
    armed by the credential alone, so removing it disarms the watch.
Host runtime P1-a, each off unless a release sets it:
  * with the runtime link configured, its decisions are notices: Robert answers them in MiniMoi (his Guild turn,
    "approve D-n" / "no D-n"), and a Telegram reply to one is not counted (Robert, 29 Oct). S1-G's reply-to-approve
    path stays built: MINIMOI_RUNTIME_TELEGRAM_ANSWERS=1 turns it on;
  * MINIMOI_RUNTIME_STATE_DIR with MINIMOI_RUNTIME_ENDPOINT_ID and MINIMOI_RUNTIME_HOST_ID (with the runtime link):
    this process also runs the authority's own timers over the link's store (core/mc_delegation_dispatcher/server.py
    AuthorityLoops: liveness, the sweeper publishing state.json there, and the sweeper watch), each a thread of its
    own; SIGUSR1 stops the sweeper alone (E11).
With none set it is exactly today's supervisor.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.supervision import telegram as tg, watch  # noqa: E402
from core.supervision.store import Store, public_receipt  # noqa: E402
from services.supervisor import activity_facts  # noqa: E402

log = logging.getLogger("supervisor")
OUTBOX_PER_MESSAGE = 10                 # notices per message from the supervisor's outbox (P2-3)
OUTBOX_MESSAGE_CHARS = 3500             # and at most this long: the bot cuts at 4,000 (independent review N-B)
HEALTH = Path(os.environ.get("SUPERVISOR_HEALTH_FILE", "/tmp/supervisor.alive"))
_stop = False


def _token():
    """Prod: a root-only file the host projects from Parameter Store (scripts/operations/supervisor_ec2.sh), mounted
    read-only; never in .env. Elsewhere: the role-aware lookup (env, Keychain, Parameter Store)."""
    path = os.environ.get("TELEGRAM_APPROVALS_BOT_TOKEN_FILE")
    if path:
        return Path(path).read_text().strip()
    from utils.telegram import _get_token
    return _get_token("telegram_approvals_bot_token", "telegram", "approvals_bot_token", "TELEGRAM_APPROVALS_BOT_TOKEN")


def _code(exc) -> str:
    text = str(exc)
    return text if 0 < len(text) <= 64 and text.replace("_", "").isalnum() and text == text.lower() \
        else type(exc).__name__


def _runtime_row(decision) -> bool:
    """A decision the runtime authority queued (S1-G): the runtime link sends it, and takes the answer back."""
    return (decision.get("requester") or {}).get("agent") == "runtime"


class Supervisor:
    def __init__(self, store, bot, github, *, chat_id, user_id, checkin_minutes=15, clock=time.monotonic,
                 runtime=None, sync_view_bound_s=None, watch=None, runtime_answers=True, loops=None):
        """``runtime_answers``: Robert may answer a runtime decision by replying on Telegram (S1-G's path; built
        directly, as the tests and gate M do). Production's configuration (``_s1g_options``) sets it False unless
        MINIMOI_RUNTIME_TELEGRAM_ANSWERS=1: decisions are then notices, answered in MiniMoi. ``loops``: the authority's
        own timers (P1-a), started by ``main``."""
        self.store, self.bot, self.github = store, bot, github
        self.runtime_answers, self.loops = runtime_answers, loops
        self.chat_id, self.user_id = chat_id, user_id
        self.every = checkin_minutes * 60
        self.clock = clock
        self.next_checkin = clock()
        self.runtime, self.sync_view_bound_s, self.watch = runtime, sync_view_bound_s, watch
        self._outbox_unrecorded = set()     # outbox keys sent, not yet recorded as sent (never sent twice meanwhile)

    # -- decisions out -------------------------------------------------------------------------------------------
    def runtime_tick(self):
        """S1-G, with a runtime link only, on every tick (its own short timer): answers already given go to the
        authority, and Robert is told what each did; decisions the authority closed itself are closed here; the
        decision notices it raised go out (a routine notice's in one message, a solo one alone); then the rule notices
        it raised at its sweeps (the outbox), and a dead sweeper's. Every message is marked told only once it went
        out, so one lost to a Telegram error goes out next tick (C-SF1, C-N4). Nothing here stops the tick (SF3): a
        failure is logged by its code and told once per key and day, and today's approvals and check-ins go on."""
        bot = self.bot if self.runtime_answers else NoticeBot(self.bot)    # P1-a: answered in MiniMoi
        steps = (("apply", lambda: self.runtime.apply(self.store, bot, self.chat_id)),
                 ("tell", lambda: self.runtime.tell_outcomes(self.store, bot, self.chat_id)),
                 ("close", lambda: self.runtime.close_closed(self.store, bot, self.chat_id)),
                 ("deliver", lambda: self.runtime.deliver(self.store, bot, self.chat_id)),
                 ("drain", lambda: self.runtime.drain(self.store, bot, self.chat_id)))
        for name, step in steps:
            try:
                step()
            except Exception as exc:
                self.runtime.errors.append(f"{name}:{_code(exc)}")
        self._runtime_failed(self.runtime.take_errors())

    def _runtime_failed(self, codes):
        """The runtime link's failures this tick, each told once per key and day, in one message (SF3). Queued in the
        supervisor's outbox first and told by its delivery, so a failed send is tried again (Codex part 2 P2-3)."""
        if codes:
            for code in codes:
                log.warning("runtime link: %s", code)
            day = f"{datetime.now(timezone.utc):%Y%m%d}"
            try:
                self.store.queue_notices("runtime_link", [{"key": f"runtime-link-error:{c}:{day}", "kind": "error",
                                                           "text": c} for c in sorted(set(codes))])
            except Exception:
                log.exception("runtime link failure not queued")
        self.tell_outbox()

    def watch_tick(self):
        """Production's notify-only watch of staging (R5.6-b), on every tick: silent once past the timeout, back once,
        told at once rather than at the next check-in. Each transition is queued with its notice (one transaction),
        and told by the outbox's delivery (Codex part 2 P2-3). Never stops the tick."""
        try:
            watch.staging_watch(self.store, timeout_s=self.watch["timeout_s"], hosts=self.watch.get("hosts"))
        except Exception:
            log.exception("staging watch failed")
        self.tell_outbox()

    def tell_outbox(self):
        """Every notice the outbox holds unsent (the staging watch's, the runtime link's errors), oldest first, one
        message per channel and at most OUTBOX_PER_MESSAGE notices, each marked sent only after its message went out
        (Codex part 2 P2-3). A failed send is counted on its notices and leaves them, and everything after, for the
        next tick. A message sent whose record fails is held here, so this process never sends it twice; a restart in
        between could repeat that one message, never lose it. Never stops the tick."""
        self._record_outbox()
        try:
            rows = [r for r in self.store.outbox_unsent() if r["key"] not in self._outbox_unrecorded]
        except Exception:
            log.exception("supervisor outbox unreadable")
            return
        for channel in ("staging_watch", "runtime_link"):
            group = [r for r in rows if r["channel"] == channel]
            for chunk in _outbox_chunks(channel, group):
                keys = [r["key"] for r in chunk]
                try:
                    self.bot.send(self.chat_id, watch.outbox_message(channel, chunk))
                except Exception as exc:
                    log.warning("supervisor outbox: %s not sent (%s)", ", ".join(keys), _code(exc))
                    try:
                        self.store.outbox_failed(keys, _code(exc))
                    except Exception:
                        log.exception("supervisor outbox failure not recorded")
                    self._record_outbox()
                    return                                        # the rest next tick, oldest first
                self._outbox_unrecorded.update(keys)
        self._record_outbox()

    def _record_outbox(self):
        if not self._outbox_unrecorded:
            return
        try:
            self.store.outbox_sent(sorted(self._outbox_unrecorded))
            self._outbox_unrecorded.clear()
        except Exception:
            log.exception("supervisor outbox not recorded; held until it is")

    def send_new(self):
        for d in self.store.pending():
            if d["state"] == "new" and not _runtime_row(d):
                message_id = self.bot.send(self.chat_id, tg.decision_message(d))
                self.store.mark_sent(d["id"], message_id)
                log.info("decision %s sent (message %s)", d["code"], message_id)

    def announce(self):
        """Tell Robert how each decision ended (answered here, in the Guild chat, or expired)."""
        for d in self.store.unannounced():
            code, r = f"D-{d['id']}", public_receipt(d["receipt"]) or {}
            if d["state"] == "expired":
                text = f"{code} expired unanswered after 24 h. The agent was told; it will ask again if still needed."
            else:
                where = "here" if r.get("channel") == "telegram" else "in the Guild chat"
                text = (f"Recorded: {code} {d['state']} {where}, in your words: “{r.get('text', '')}”. "
                        f"The agent that asked gets it next.")
            self.bot.send(self.chat_id, text, reply_to=d["telegram_message_id"])
            self.store.mark_announced(d["id"])

    # -- answers in ----------------------------------------------------------------------------------------------
    def read_answers(self, wait=25):
        offset = self.store.offset()
        for update in self.bot.updates(offset, wait=wait):
            self.store.set_offset(update["update_id"] + 1)
            self.handle(update)

    def handle(self, update):
        reply_to, msg = tg.verify(update, owner_chat_id=self.chat_id, owner_user_id=self.user_id, bot_id=self.bot.id)
        if reply_to is None:
            if msg in ("not_a_reply", "not_a_reply_to_the_bot") and isinstance(update.get("message"), dict):
                self.bot.send(self.chat_id, "Not counted: to answer a decision, reply to the message that asked it.",
                              reply_to=update["message"].get("message_id"))
            elif msg != "not_a_message":
                log.warning("telegram update ignored (%s)", msg)    # someone else, or a forward: never an answer
            return
        decision = self.store.by_message(reply_to)
        if decision is not None and _runtime_row(decision):
            if not self.runtime_answers:
                return self._answer_in_minimoi(msg, [decision])
            return self.handle_runtime(update, msg, [decision], batch=False)
        if decision is None and self.runtime is not None:
            batch = self.store.by_batch(reply_to)
            if batch:
                if not self.runtime_answers:
                    return self._answer_in_minimoi(msg, batch)
                return self.handle_runtime(update, msg, batch, batch=True)
        if decision is None:
            self.bot.send(self.chat_id, "Not counted: that message is not a decision.", reply_to=msg["message_id"])
            return
        code = f"D-{decision['id']}"
        if decision["state"] not in ("new", "sent"):
            self.bot.send(self.chat_id, f"{code} is already {decision['state']}; nothing changed.",
                          reply_to=msg["message_id"])
            return
        answer = tg.parse_answer(msg["text"])
        if answer is None:
            self.bot.send(self.chat_id, f"Not counted for {code}: reply just “yes” to approve, or “no” (with any "
                                        "reason) to decline.", reply_to=msg["message_id"])
            return
        done = self.store.decide(decision["id"], answer, tg.receipt(update, msg, decision),
                                 text_sha256=decision["text_sha256"])
        if done is None:
            self.bot.send(self.chat_id, f"{code} could not be recorded (expired or changed); nothing changed.",
                          reply_to=msg["message_id"])
        log.info("decision %s %s by Telegram reply", code, answer if done else "not recorded")

    def _answer_in_minimoi(self, msg, rows):
        """P1-a: a reply to a runtime decision's notice while answers are taken only in MiniMoi: not counted, and
        Robert is told where to answer it. Nothing is recorded."""
        codes = [d.get("code") or f"D-{d['id']}" for d in rows]
        self.bot.send(self.chat_id, tg.not_counted_here(codes), reply_to=msg["message_id"])
        log.info("runtime decisions %s: a Telegram reply not counted (answered in MiniMoi)", ",".join(codes))

    def handle_runtime(self, update, msg, rows, *, batch):
        """Robert's reply to a runtime decision's message, alone or batched (R7.4-c, OWN-6, SF1). A runtime decision
        acts on work, so a reply never declines more than it says: the whole short "yes" approves every decision the
        message lists that still waits, the whole short "no" declines them (each does what its words say), "wait",
        "hold", "not now", "stop" change nothing, and anything else is not counted; one code at a time ("approve
        D-12", "no D-13") answers them differently. A solo decision is never answered by a batched reply (N2). Each
        answer is recorded once, with its own receipt: the same words, bound to that decision's own digest. What it
        did is told once the authority has applied it."""
        reply = msg["message_id"]
        rows = [dict(d, code=d.get("code") or f"D-{d['id']}") for d in rows]     # by_message's rows carry no code
        codes = ", ".join(d["code"] for d in rows)
        per_code = tg.parse_code_command(msg["text"])
        if per_code is not None:
            answer, code = per_code
            targets = [d for d in rows if d["code"] == code]
            if not targets:
                self.bot.send(self.chat_id, f"Not counted: {code} is not in that message.", reply_to=reply)
                return
            if batch and len(rows) > 1 and (targets[0]["requester"] or {}).get("solo"):
                self.bot.send(self.chat_id, f"Not counted: {code} arrives on its own; answer it in its own message.",
                              reply_to=reply)
                return
        else:
            answer = tg.parse_runtime_reply(msg["text"])
            targets = [d for d in rows if not (batch and len(rows) > 1 and (d["requester"] or {}).get("solo"))]
            if answer is None:
                self.bot.send(self.chat_id, f"Not counted for {codes}: reply just “yes” or just “no” (each does what "
                                            "its words say), “wait” to leave them, or one code at a time "
                                            f"(approve {rows[0]['code']}, no {rows[-1]['code']}).", reply_to=reply)
                return
        live = [d for d in targets if d["state"] in ("new", "sent")]
        if answer == "held":
            self.bot.send(self.chat_id, f"Nothing changed: {', '.join(d['code'] for d in live) or codes} still "
                                        "wait. Reply just “no” to decline, or name one code at a time.", reply_to=reply)
            return
        done = [d["code"] for d in live if self.store.decide(d["id"], answer, tg.receipt(update, msg, d),
                                                             text_sha256=d["text_sha256"])]
        log.info("runtime decisions %s %s by Telegram reply", ",".join(done) or "none", answer)
        reason = getattr(self.runtime, "closed_reason_for", None)
        for d in targets:
            if d["code"] not in done:
                why = reason(d) if reason is not None else f"already {d['state']}"
                self.bot.send(self.chat_id, f"{d['code']} is no longer open ({why}); nothing changed.", reply_to=reply)

    # -- check-ins -----------------------------------------------------------------------------------------------
    def checkin_due(self):
        return self.clock() >= self.next_checkin

    def checkin(self, now=None):
        self.next_checkin = self.clock() + self.every
        summary, items = watch.check_in(self.store, self.github, now=now, runtime=self.runtime,
                                        sync_view_bound_s=self.sync_view_bound_s)   # the watch runs on the tick
        # The runtime link's notices (``drained``) are told by its drain alone, recorded once they went out (C-SF1);
        # the check-in only lists them. Without a link there are none, and this is today's check-in.
        fresh = set(self.store.new_alert_keys([i["key"] for i in items if not i.get("drained")]))
        new_items = [i for i in items if i["key"] in fresh and not i.get("drained")]
        status = "none"
        if new_items:
            try:
                self.bot.send(self.chat_id, watch.message(new_items, summary))
                status = "sent"
            except Exception:
                log.exception("check-in message not sent")
                status = "failed"
        summary["open_items"] = [{"kind": i["kind"], "text": i["text"]} for i in items][:20]
        row = self.store.record_checkin(summary, [i["key"] for i in new_items], status)
        log.info("check-in %s: %d items, %d new, telegram %s", row["id"], len(items), len(new_items), status)
        # guild-build's activity facts for CoS (CoS 1.2 spec section 4.4): off unless configured, never fails this loop.
        activity_facts.after_checkin(self.store)
        return summary, new_items

    def tick(self):
        if self.runtime is not None:
            try:                    # S1-G: the runtime link never stops today's approvals, check-ins or health (SF3)
                self.runtime_tick()
            except Exception:
                log.exception("runtime tick failed")
        if self.watch is not None:
            self.watch_tick()
        self.send_new()
        self.announce()
        if self.checkin_due():
            self.checkin()
        self.read_answers()
        HEALTH.write_text(datetime.now(timezone.utc).isoformat())


class NoticeBot:
    """The approvals bot as the runtime link sees it while Robert answers in MiniMoi (P1-a): every decision message
    becomes a notice (``telegram.answer_in_minimoi``: the decision's words kept, S1-G's reply instruction replaced);
    everything else passes unchanged."""

    def __init__(self, bot):
        self._bot = bot

    def __getattr__(self, name):
        return getattr(self._bot, name)

    def send(self, chat_id, text, reply_to=None):
        if reply_to is None:
            return self._bot.send(chat_id, tg.answer_in_minimoi(text))
        return self._bot.send(chat_id, tg.answer_in_minimoi(text), reply_to=reply_to)


def _outbox_chunks(channel, group):
    """The outbox's notices for one channel, oldest first, in messages of at most OUTBOX_PER_MESSAGE notices and
    OUTBOX_MESSAGE_CHARS characters, so no message is cut by Telegram after its notices were marked sent (N-B). A
    single notice longer than that goes alone; watch.outbox_message's words carry it, bounded at send."""
    chunk = []
    for row in group:
        trial = chunk + [row]
        if chunk and (len(trial) > OUTBOX_PER_MESSAGE or len(watch.outbox_message(channel, trial)) > OUTBOX_MESSAGE_CHARS):
            yield chunk
            trial = [row]
        chunk = trial
    if chunk:
        yield chunk


RUNTIME_LOOP_KEYS = ("MINIMOI_RUNTIME_STATE_DIR", "MINIMOI_RUNTIME_ENDPOINT_ID", "MINIMOI_RUNTIME_HOST_ID")


def _authority_loops(environ, link):
    """P1-a: the authority's own timers in this process, over the runtime link's store, or None (none of
    RUNTIME_LOOP_KEYS set). All three or none, and only with the link: a missing value admits nothing."""
    given = [k for k in RUNTIME_LOOP_KEYS if environ.get(k)]
    if not given:
        return None
    if len(given) != len(RUNTIME_LOOP_KEYS) or link is None:
        raise ValueError("authority_loops_incomplete")
    if not link.authority.sweeps:
        raise ValueError("authority_loops_need_a_sweeper")
    ids = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
    if not (ids.match(environ["MINIMOI_RUNTIME_ENDPOINT_ID"]) and ids.match(environ["MINIMOI_RUNTIME_HOST_ID"])):
        raise ValueError("authority_loops_invalid")
    from core.mc_delegation_dispatcher import server
    return server.AuthorityLoops(link.authority, state_dir=Path(environ["MINIMOI_RUNTIME_STATE_DIR"]),
                                 endpoint_id=environ["MINIMOI_RUNTIME_ENDPOINT_ID"],
                                 host_id=environ["MINIMOI_RUNTIME_HOST_ID"], log=log)


def runtime_answers(environ) -> bool:
    """P1-a (Robert, 29 Oct): production answers runtime decisions in MiniMoi; a Telegram reply to one counts only when
    MINIMOI_RUNTIME_TELEGRAM_ANSWERS=1, whether or not the runtime link is on (review SF4)."""
    return environ.get("MINIMOI_RUNTIME_TELEGRAM_ANSWERS") == "1"


def runtime_options(environ, connect) -> tuple:
    """P1-a: the runtime link and the authority's timers, or neither, never a crash (review SF5): a configuration
    either refuses (a partial set, a file that is not version 2, timers without the link) is logged by its code and
    left off, and today's approvals and check-ins go on. -> (options, refused codes)."""
    from core.supervision.runtime import from_environ
    out, refused = {}, []
    try:
        link = from_environ(environ, connect)
    except (ValueError, OSError) as exc:
        link = None
        refused.append(f"runtime_link_refused:{_code(exc)}")
    if link is not None:
        out["runtime"] = link
        from core.mc_delegation_dispatcher.production import file_digest
        log.info("runtime link on: authority file %s", file_digest(environ["MINIMOI_RUNTIME_AUTHORITY_FILE"]))
    try:
        loops = _authority_loops(environ, link)
    except ValueError as exc:
        loops = None
        refused.append(f"authority_loops_refused:{_code(exc)}")
    if loops is not None:
        out["loops"] = loops
    for code in refused:
        log.error("supervisor: %s; it stays off, and today's approvals and check-ins go on", code)
    return out, refused


def _s1g_options(environ, connect) -> dict:
    """S1-G's parts a release turns on by configuration; {} (today's supervisor) when none is set."""
    out, _ = runtime_options(environ, connect)
    return {**out, **_view_and_watch_options(environ)}


def _view_and_watch_options(environ) -> dict:
    out = {}
    bound = environ.get("MINIMOI_SUPERVISION_SYNC_VIEW_BOUND_S")
    if bound:
        out["sync_view_bound_s"] = int(bound)
    # The watch is armed by staging's credential itself (R5.6-b): removing the credential alone disarms it.
    credential = (environ.get("MINIMOI_SUPERVISION_WATCH_TOKEN_SHA256") or "").strip().lower()
    host = environ.get("MINIMOI_SUPERVISION_WATCH_HOST") or ""
    if re.fullmatch(r"[0-9a-f]{64}", credential) and re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", host):
        out["watch"] = {"timeout_s": int(environ.get("MINIMOI_SUPERVISION_WATCH_TIMEOUT_S") or 90), "hosts": [host]}
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import psycopg2
    dsn = os.environ["DATABASE_URL"]
    chat_id = int(os.environ["TELEGRAM_CHAT_ID"])
    user_id = int(os.environ.get("MINIMOI_OWNER_TELEGRAM_USER_ID") or chat_id)
    connect = lambda: psycopg2.connect(dsn, connect_timeout=5, options="-c statement_timeout=10000")  # noqa: E731
    store = Store(connect)
    runtime, refused = runtime_options(os.environ, connect)
    options = {**runtime, **_view_and_watch_options(os.environ)}
    sup = Supervisor(store, tg.Bot(_token()), watch.GitHub(os.environ.get("MINIMOI_GITHUB_REPO", watch.REPO)),
                     chat_id=chat_id, user_id=user_id,
                     checkin_minutes=int(os.environ.get("SUPERVISOR_CHECKIN_MINUTES", "15")),
                     runtime_answers=runtime_answers(os.environ), **options)
    if refused:
        sup._runtime_failed(refused)                # told once a day through the outbox, never a crash

    def stop(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if sup.loops is not None:                       # P1-a: the authority's timers, before the first tick
        signal.signal(signal.SIGUSR1, lambda *_: sup.loops.stop_sweeper())        # E11: the sweeper alone stops
        sup.loops.start()
        log.info("authority timers up: liveness, sweeper and its watch")
    log.info("supervisor up: check-in every %s min", sup.every // 60)
    backoff = 5
    try:
        while not _stop:
            try:
                sup.tick()
                backoff = 5
            except Exception:
                log.exception("supervisor tick failed; retrying in %ss", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
    finally:
        if sup.loops is not None:
            sup.loops.stop()


if __name__ == "__main__":
    main()

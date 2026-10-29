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
    notice's together in one message, a solo one alone), a reply to a batched message answers every decision it lists
    (or one code at a time), each answer is taken to the authority with its receipt, and the check-in's delegation
    items come from the runtime's snapshot;
  * MINIMOI_SUPERVISION_SYNC_VIEW_BOUND_S: the Mac sync's mirror is read with its age (R4.6-b);
  * MINIMOI_SUPERVISION_WATCH_TIMEOUT_S: production's notify-only watch of staging (R5.6-b).
With none set it is exactly today's supervisor.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from core.supervision import telegram as tg, watch  # noqa: E402
from core.supervision.store import Store, public_receipt  # noqa: E402

log = logging.getLogger("supervisor")
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


def _runtime_row(decision) -> bool:
    """A decision the runtime authority queued (S1-G): the runtime link sends it, and takes the answer back."""
    return (decision.get("requester") or {}).get("agent") == "runtime"


class Supervisor:
    def __init__(self, store, bot, github, *, chat_id, user_id, checkin_minutes=15, clock=time.monotonic,
                 runtime=None, sync_view_bound_s=None, watch=None):
        self.store, self.bot, self.github = store, bot, github
        self.chat_id, self.user_id = chat_id, user_id
        self.every = checkin_minutes * 60
        self.clock = clock
        self.next_checkin = clock()
        self.runtime, self.sync_view_bound_s, self.watch = runtime, sync_view_bound_s, watch

    # -- decisions out -------------------------------------------------------------------------------------------
    def runtime_tick(self):
        """S1-G, with a runtime link only: answers already given go to the authority first, then the decision notices
        it raised go out (a routine notice's decisions in one message, a solo one alone)."""
        for code, outcome in self.runtime.apply(self.store):
            log.info("runtime decision %s applied: %s", code, outcome.get("state"))
        for message_id in self.runtime.deliver(self.store, self.bot, self.chat_id):
            log.info("runtime decision notice sent (message %s)", message_id)

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
            if _runtime_row(d):
                self._announce_runtime(d, code, r)
                continue
            if d["state"] == "expired":
                text = f"{code} expired unanswered after 24 h. The agent was told; it will ask again if still needed."
            else:
                where = "here" if r.get("channel") == "telegram" else "in the Guild chat"
                text = (f"Recorded: {code} {d['state']} {where}, in your words: “{r.get('text', '')}”. "
                        f"The agent that asked gets it next.")
            self.bot.send(self.chat_id, text, reply_to=d["telegram_message_id"])
            self.store.mark_announced(d["id"])

    def _announce_runtime(self, d, code, r):
        """A runtime decision's end, answered in the message (alone, or in a batched one) that carried it."""
        if d["state"] == "expired":
            text = f"{code} expired unanswered. The runtime keeps it waiting; nothing was done."
        else:
            where = "here" if r.get("channel") == "telegram" else "in the Guild chat"
            text = (f"Recorded: {code} {d['state']} {where}, in your words: “{r.get('text', '')}”. "
                    f"The runtime applies it next.")
        reply_to = d["telegram_message_id"] or self.store.batch_of(d["id"])
        self.bot.send(self.chat_id, text, reply_to=reply_to)
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
        if decision is None and self.runtime is not None:
            batch = self.store.by_batch(reply_to)
            if batch:
                return self.handle_batch(update, msg, batch)
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

    def handle_batch(self, update, msg, batch):
        """Robert's reply to a batched message (R7.4-c, OWN-6). The whole short reply "yes" approves every decision
        it lists that still waits, "no" declines them; "approve D-12" / "no D-12" answers one of them. Each answer is
        recorded once, with its own receipt: the same words, bound to that decision's own digest."""
        per_code = tg.parse_code_command(msg["text"])
        if per_code is not None:
            answer, code = per_code
            targets = [d for d in batch if d["code"] == code]
            if not targets:
                self.bot.send(self.chat_id, f"Not counted: {code} is not in that message.", reply_to=msg["message_id"])
                return
        else:
            answer, targets = tg.parse_answer(msg["text"]), batch
            if answer is None:
                self.bot.send(self.chat_id, "Not counted: reply just “yes” to approve every decision listed, “no” to "
                                            "decline them, or one code at a time (approve D-n).",
                              reply_to=msg["message_id"])
                return
        done = []
        for d in targets:
            if d["state"] in ("new", "sent") and self.store.decide(d["id"], answer, tg.receipt(update, msg, d),
                                                                   text_sha256=d["text_sha256"]):
                done.append(d["code"])
        left = [d["code"] for d in targets if d["code"] not in done]
        log.info("batched decisions %s %s by Telegram reply", ",".join(done) or "none", answer)
        if left:
            self.bot.send(self.chat_id, f"{', '.join(left)}: already answered or expired; nothing changed.",
                          reply_to=msg["message_id"])

    # -- check-ins -----------------------------------------------------------------------------------------------
    def checkin_due(self):
        return self.clock() >= self.next_checkin

    def checkin(self, now=None):
        self.next_checkin = self.clock() + self.every
        summary, items = watch.check_in(self.store, self.github, now=now, runtime=self.runtime,
                                        sync_view_bound_s=self.sync_view_bound_s, watch=self.watch)
        fresh = set(self.store.new_alert_keys([i["key"] for i in items]))
        new_items = [i for i in items if i["key"] in fresh]
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
        return summary, new_items

    def tick(self):
        if self.runtime is not None:
            self.runtime_tick()
        self.send_new()
        self.announce()
        if self.checkin_due():
            self.checkin()
        self.read_answers()
        HEALTH.write_text(datetime.now(timezone.utc).isoformat())


def _s1g_options(environ, connect) -> dict:
    """S1-G's parts a release turns on by configuration; {} (today's supervisor) when none is set."""
    from core.supervision.runtime import from_environ
    out = {}
    link = from_environ(environ, connect)
    if link is not None:
        out["runtime"] = link
    bound = environ.get("MINIMOI_SUPERVISION_SYNC_VIEW_BOUND_S")
    if bound:
        out["sync_view_bound_s"] = int(bound)
    timeout = environ.get("MINIMOI_SUPERVISION_WATCH_TIMEOUT_S")
    if timeout:
        out["watch"] = {"timeout_s": int(timeout)}
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import psycopg2
    dsn = os.environ["DATABASE_URL"]
    chat_id = int(os.environ["TELEGRAM_CHAT_ID"])
    user_id = int(os.environ.get("MINIMOI_OWNER_TELEGRAM_USER_ID") or chat_id)
    connect = lambda: psycopg2.connect(dsn, connect_timeout=5, options="-c statement_timeout=10000")  # noqa: E731
    store = Store(connect)
    sup = Supervisor(store, tg.Bot(_token()), watch.GitHub(os.environ.get("MINIMOI_GITHUB_REPO", watch.REPO)),
                     chat_id=chat_id, user_id=user_id,
                     checkin_minutes=int(os.environ.get("SUPERVISOR_CHECKIN_MINUTES", "15")),
                     **_s1g_options(os.environ, connect))

    def stop(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("supervisor up: check-in every %s min", sup.every // 60)
    backoff = 5
    while not _stop:
        try:
            sup.tick()
            backoff = 5
        except Exception:
            log.exception("supervisor tick failed; retrying in %ss", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    main()

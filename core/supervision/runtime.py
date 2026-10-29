"""The check-in's link to the host runtime's authority (spec v0.5 §3.3, R4.3-b, R4.6-b, R7.4-c/d, R7.6-a; S1-G).

Off unless a release configures it (``from_environ``); with no link the supervisor and its check-in are exactly today's.
With one, three things, all deterministic, none calling a model:

* **The check-in is a client of the snapshot** (``minimoi.runtime.state.v1``) for delegation items: the notices the
  authority raised (one rule set, today's three delegation keys among them, R7.4-d), its hosts and running items. A
  snapshot read as a view carries its age; past ``view_bound_s``, or of unknown age, no rule fires from it, and one
  ``view-stale`` item says so instead (R4.6-b).
* **Decisions get D-codes and go out** (``deliver``): each decision notice the authority raised becomes one message,
  its decisions supervision rows in the authority's own words. A routine notice carries up to five, one "yes" approving
  every decision it lists; a solo one (an ``unknown`` resolution, a reassignment, a cross-vendor admission) carries one
  (R7.4-c, OWN-6). The approval stays the whole short reply (telegram.py).
* **Robert's answers reach the authority** (``apply``): each answered runtime decision, Telegram reply or Guild turn,
  is resolved at the authority with that receipt (its channel and turn, his words, the decision's own digest), the
  owner path of R4.3-b. Nothing here answers for him: a decision is applied only once he answered it.

The notifier adapter is the approvals bot's interface (``send(chat_id, text, reply_to=None) -> message_id``): the
Telegram bot for real, a fake that writes ``notify.jsonl`` in tests.
"""
from __future__ import annotations

import os

from core.mc_delegation_dispatcher import snapshot as snap
from core.supervision import telegram as tg


class RuntimeLink:
    """One environment's authority, read as a snapshot and answered as its owner path. ``authority`` is that
    environment's version 2 ``DispatcherStore`` (its ``resolve`` is the owner path); ``source`` gives the snapshot
    (default: built live from the authority; or ``snapshot.read_state_file`` of the published ``state.json``).
    Nothing here raises into the supervisor's tick: each step's failures are kept in ``errors`` (fixed codes), for the
    supervisor to tell once per key while today's approvals and check-ins go on (SF3)."""

    def __init__(self, authority, *, owner, view_bound_s, source=None):
        if not isinstance(owner, str) or not owner or not isinstance(view_bound_s, int) or view_bound_s < 1:
            raise ValueError("runtime_link_invalid")
        self.authority, self.owner, self.view_bound_s = authority, owner, view_bound_s
        self.source = source or authority.snapshot
        self.errors = []
        self._outbox_seq = 0                 # the outbox's last seq read in this process (a restart re-reads a day)

    def take_errors(self) -> list:
        out, self.errors = self.errors, []
        return out

    def view(self):
        """(snapshot, stale): stale when its age is unknown or past the bound (R4.6-b: then nothing fires from it)."""
        doc = self.source()
        return doc, snap.view_stale(doc, self.view_bound_s)

    @staticmethod
    def sweeper_stale(doc) -> list:
        """The live ``sweeper_stale`` notice, raised or not: a dead sweeper raises nothing itself, so the check-in and
        the drain read it from the snapshot (SF5)."""
        return [n for n in doc.get("notices", []) if n.get("rule") == "sweeper_stale"]

    def check_in(self):
        """(items, running, runners, errors) for the check-in's delegation part, from the snapshot alone: the notices
        the authority raised, except decisions (``deliver`` sends those), with their keys, and a dead sweeper's notice
        whether raised or not; the items not ended; the hosts. A stale view gives one ``view-stale`` item."""
        try:
            doc, stale = self.view()
        except Exception as exc:
            return [], [], [], [f"runtime snapshot unreadable ({type(exc).__name__})"]
        if stale:
            return [_stale_item(doc)], [], [], []
        items = [{"key": n["key"], "kind": n["kind"], "text": n["text"]} for n in doc.get("notices", [])
                 if (n.get("raised_at") or n.get("rule") == "sweeper_stale")
                 and n.get("rule") not in ("decision", "decisions")]
        running = []
        for it in doc.get("items", []):
            if it["stored_state"] in snap.TERMINAL or it["stored_state"] == "proposed":
                continue
            age = it.get("progress_age_s")
            running.append({"job": it.get("job_id"), "agent": it.get("actor"), "state": it["shown_as"],
                            "minutes": None if age is None else int(age // 60), "brief": snap._label(it)})
        runners = [{"id": h["host_id"], "state": h["reachability"],
                    "seen_minutes_ago": None if h.get("contact_age_s") is None else int(h["contact_age_s"] // 60)}
                   for h in doc.get("hosts", [])]
        return items, running, runners, []

    # ── decisions out ──
    def deliver(self, store, bot, chat_id) -> list:
        """Each decision notice the authority raised, not yet sent, as one message (R7.4-c): its decisions become
        D-codes (supervision rows, the authority's words), a routine notice's together under one message
        (``add_batch``, which refuses a solo row: N2), a solo one's alone (``mark_sent``). Nothing goes out from a
        stale view; one notice's failure never holds up another's. -> message ids."""
        doc, stale = self.view()
        if stale:
            return []
        sent = []
        for n in doc.get("notices", []):
            if n.get("rule") not in ("decision", "decisions") or not n.get("raised_at") or not n.get("words"):
                continue
            try:
                rows = [store.runtime_decision(w["decision_id"], w["words"], notice=n["key"], position=i,
                                               solo=bool(n.get("solo")), environment=doc["environment"])
                        for i, w in enumerate(n["words"], 1)]
                if any(r["state"] != "new" for r in rows):
                    continue                                 # sent (or answered) already: never twice
                if n["rule"] == "decision":
                    message_id = bot.send(chat_id, tg.runtime_message(rows[0]))
                    store.mark_sent(rows[0]["id"], message_id)
                else:
                    message_id = bot.send(chat_id, tg.batch_message(rows))
                    store.add_batch(message_id, [r["id"] for r in rows])
                sent.append(message_id)
            except Exception as exc:
                self.errors.append(f"deliver:{_code(exc)}")
        return sent

    # ── answers in, and what the authority did with them ──
    def apply(self, store, bot, chat_id) -> list:
        """Robert's answers to runtime decisions, taken to the authority once each (its ``resolve``, with the receipt),
        and then told to him in the message that carried the decision, naming what happened: applied, left waiting,
        moot, stale, already closed, or refused with the reason (SF2). A refusal that leaves the decision waiting at
        the authority gives it a fresh D-code, sent alone. Any other failure leaves the answer for the next tick.
        -> [(row, outcome)]"""
        from core.mc_delegation_dispatcher.store import AuthorityRefused
        out = []
        for d in store.runtime_answered():
            r = d["receipt"] or {}
            receipt = {"channel": r.get("channel"), "turn_id": _turn(r), "words": r.get("text"),
                       "decision_sha256": d["text_sha256"]}
            did = d["requester"]["decision_id"]
            try:
                outcome = self.authority.resolve(did, d["state"], by=self.owner, receipt=receipt)
            except AuthorityRefused as exc:
                outcome = {"state": "refused", "code": str(exc)}
            except Exception as exc:
                self.errors.append(f"apply:{_code(exc)}")
                continue                                     # not taken: tried again next tick
            store.mark_applied(d["id"])
            if outcome["state"] == "refused":
                outcome["reissued"] = self._reissue(store, bot, chat_id, did)
            self._tell(store, bot, chat_id, d, outcome_text(d, outcome))
            out.append((d, outcome))
        return out

    def _reissue(self, store, bot, chat_id, decision_id):
        """A fresh D-code for a decision still waiting at the authority after a refused answer, sent alone. -> code."""
        try:
            status = self.authority.decisions_status([decision_id]).get(decision_id) or {}
            if not status.get("open") or not status.get("words"):
                return None
            rows = store.runtime_rows(decision_id)
            row = store.runtime_decision(decision_id, status["words"], notice=f"reissue:{decision_id}", position=1,
                                         solo=True, environment=self.authority.environment, reissue=len(rows) + 1)
            store.mark_sent(row["id"], bot.send(chat_id, tg.runtime_message(row)))
            return row["code"]
        except Exception as exc:
            self.errors.append(f"reissue:{_code(exc)}")
            return None

    def close_closed(self, store, bot, chat_id) -> list:
        """Runtime D-codes whose decision the authority closed itself (a revocation withdrew it, a new question
        superseded it, another owner act resolved it): closed here so they can no longer be answered, and Robert is
        told once why (SF2). -> [(D-code, reason)]"""
        rows = store.runtime_open()
        if not rows:
            return []
        status = self.authority.decisions_status(sorted({r["requester"]["decision_id"] for r in rows}))
        out = []
        for r in rows:
            st = status.get(r["requester"]["decision_id"]) or {"open": False, "answer": "unknown_decision"}
            if st.get("open"):
                continue
            if store.close_runtime(r["id"]):
                reason = closed_reason(st)
                self._tell(store, bot, chat_id, r, f"{r['code']} is no longer open: {reason}. Nothing to answer.",
                           announce=False)
                out.append((r["code"], reason))
        return out

    def _tell(self, store, bot, chat_id, row, text, *, announce=True):
        reply_to = row.get("telegram_message_id") or store.batch_of(row["id"])
        try:
            bot.send(chat_id, text, reply_to=reply_to)
            if announce:
                store.mark_announced(row["id"])
        except Exception as exc:
            self.errors.append(f"tell:{_code(exc)}")

    # ── rule notices, at the sweep that raised them (SF5) ──
    def drain(self, store, bot, chat_id) -> list:
        """The authority's outbox (notice.raised, recorded at each sweep) read on the supervisor's own short timer: each
        rule notice not told before (the check-in's once-per-key memory) goes out now, even if it has cleared since, in
        one message; decisions go out through ``deliver``. A dead sweeper raises nothing, so its live notice is read
        from the snapshot here too. -> the keys told."""
        found = self.authority.notices_since(self._outbox_seq)
        items = []
        for e in found:
            self._outbox_seq = max(self._outbox_seq, e["seq"])
            n = e["notice"]
            if n.get("rule") in ("decision", "decisions") or not n.get("key") or not n.get("text"):
                continue
            items.append({"key": n["key"], "kind": n.get("kind") or "error", "text": n["text"]})
        try:
            doc, stale = self.view()
            items.extend({"key": n["key"], "kind": n["kind"], "text": n["text"]} for n in self.sweeper_stale(doc))
            if stale:
                items.append(_stale_item(doc))
        except Exception as exc:
            self.errors.append(f"view:{_code(exc)}")
        if not items:
            return []
        fresh = set(store.new_alert_keys([i["key"] for i in items]))
        told = [i for i in items if i["key"] in fresh]
        if told:
            from core.supervision.watch import notices_message
            bot.send(chat_id, notices_message(told))
        return [i["key"] for i in told]

    def closed_reason_for(self, row) -> str:
        """Why a runtime D-code can no longer be answered, from the authority (for a reply to it, SF2)."""
        try:
            st = self.authority.decisions_status([row["requester"]["decision_id"]]).get(
                row["requester"]["decision_id"]) or {}
        except Exception:
            st = {}
        if st.get("open"):
            return f"already {row['state']} here"
        return closed_reason(st) if st else f"{row['state']}"


ACTS = {"admit": "the step is admitted under its own approval and queued",
        "decline": "the step is cancelled, and its card ended",
        "end": "the item ended (interrupted: owner_decision); nothing is retried",
        "accept": "the reported result is accepted, and its card goes on",
        "acknowledge": "acknowledged; nothing changed",
        "reassign": "the item is reassigned",
        "withdraw": "withdrawn; nothing changed",
        "authorize_review": "MC may review it once (one judgment call)",
        "leave": "nothing changed; it still waits"}


def outcome_text(row, outcome) -> str:
    """What Robert's answer did at the authority, in one line (SF2)."""
    code, state = row["code"], outcome.get("state")
    if state == "resolved":
        return f"Applied: {code} {row['state']}: {ACTS.get(outcome.get('action'), outcome.get('action'))}."
    if state == "waiting":
        return (f"Recorded: {code} {row['state']}: nothing changed, and the item still waits at the runtime. "
                f"MC can propose ending or reassigning it.")
    if state == "moot":
        return f"Not applied: {code}'s item had already moved on ({outcome.get('item_state')}); nothing was done."
    if state == "stale":
        return (f"Not applied: {code} named an earlier generation; the item is now at generation "
                f"{outcome.get('generation_now')}, so nothing was done"
                + (". A fresh decision follows." if outcome.get("fresh_decision_id") else "."))
    if state == "already_resolved":
        return f"Not applied: the runtime had already closed {code} ({outcome.get('answer')}); nothing was done."
    return (f"Not applied: {code} was refused ({outcome.get('code')}); nothing was done"
            + (f". It still waits, as {outcome['reissued']}." if outcome.get("reissued") else "."))


def closed_reason(status) -> str:
    answer = status.get("answer")
    if answer == "withdrawn":
        return f"the runtime withdrew it ({status.get('code') or 'withdrawn'})"
    if answer == "superseded":
        return "a newer question replaced it"
    if answer in ("approved", "declined"):
        return f"it was answered another way ({answer}: {status.get('action')})"
    if answer in ("moot", "stale"):
        return f"it became {answer}"
    if answer == "unknown_decision":
        return "the runtime does not know it"
    return f"the runtime closed it ({answer})"


def _stale_item(doc) -> dict:
    return {"key": f"view-stale:runtime:{doc.get('as_of')}", "kind": "error",
            "text": f"The runtime's state is {_age_words(doc.get('view_age_s'))} old (as of {doc.get('as_of')}); no "
                    f"delegation rule fires from it until it is fresh."}


def _code(exc) -> str:
    text = str(exc)
    return text if 0 < len(text) <= 64 and text.replace("_", "").isalnum() and text == text.lower() \
        else type(exc).__name__


def _turn(receipt) -> str:
    """The owner turn a receipt names: the Telegram message, or the Guild note."""
    if receipt.get("channel") == "telegram":
        return f"telegram:{receipt.get('chat_id')}:{receipt.get('message_id')}"
    if receipt.get("channel") == "guild":
        return f"guild:{receipt.get('conversation_id')}:{receipt.get('note_id')}"
    return str(receipt.get("turn_id") or "")


def _age_words(age) -> str:
    return "of unknown age" if age is None else f"{int(age)} s"


def from_environ(environ, connect):
    """The link a release configures (all of them, or none: a missing value admits nothing):
    MINIMOI_RUNTIME_AUTHORITY_FILE  the authority's values (store.Authority; version 2)
    MINIMOI_RUNTIME_PRINCIPAL       the principal its items serve
    MINIMOI_RUNTIME_OWNER           the owner identity Robert's answers are recorded under
    MINIMOI_RUNTIME_VIEW_BOUND_S    how old a snapshot may be before no rule fires from it (R4.6-b)
    MINIMOI_RUNTIME_STATE_FILE      optional: read the published state.json (a view) instead of building it live
    None when none is set (today's supervisor). Raises ValueError when only some are, or one is invalid."""
    keys = ("MINIMOI_RUNTIME_AUTHORITY_FILE", "MINIMOI_RUNTIME_PRINCIPAL", "MINIMOI_RUNTIME_OWNER",
            "MINIMOI_RUNTIME_VIEW_BOUND_S")
    given = [k for k in keys if environ.get(k)]
    if not given:
        return None
    if len(given) != len(keys):
        raise ValueError("runtime_link_incomplete")
    from core.mc_delegation_dispatcher import cards
    from core.mc_delegation_dispatcher.store import Authority, DispatcherStore
    from core.minimoi_actions.grants import CapabilityRule, GrantStore
    auth = Authority.load(environ["MINIMOI_RUNTIME_AUTHORITY_FILE"])
    grants = GrantStore({"delegate.start_build": CapabilityRule(1, True, True),
                         "delegate.start_research": CapabilityRule(1, True, True), **cards.grant_rows()})
    store = DispatcherStore(connect, principal=environ["MINIMOI_RUNTIME_PRINCIPAL"], environment=auth.environment,
                            authority=auth, grants=grants)
    bound = environ["MINIMOI_RUNTIME_VIEW_BOUND_S"]
    if not bound.isdigit():
        raise ValueError("runtime_link_invalid")
    state_file = environ.get("MINIMOI_RUNTIME_STATE_FILE")
    source = None if not state_file else (lambda: snap.read_state_file(os.path.expanduser(state_file)))
    return RuntimeLink(store, owner=environ["MINIMOI_RUNTIME_OWNER"], view_bound_s=int(bound), source=source)

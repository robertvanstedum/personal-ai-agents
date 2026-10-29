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

_NOT_WRITTEN = object()        # _reissue: the fresh D-code could not be written (the answer stays unapplied)
OUTBOX_PAGE = 500              # notices_since's page
OUTBOX_PAGES = 20              # pages read per tick at most: a restart catches up within a tick or two
NOTICES_PER_MESSAGE = 10
MESSAGE_CHARS = 3500           # under Telegram's 4096 (the bot cuts at 4000): a notice is never cut off unseen
MESSAGES_PER_TICK = 3          # a backlog goes out a few messages a tick, oldest first


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
        # Every message is marked told only after it went out (C-SF1, C-N4): a failed send is tried again next tick.
        # What went out but could not be recorded yet is held here, so it is never sent twice while this process
        # lives (DUP-7); a restart between a send and its record could repeat that one message, never lose it.
        self._outbox_seq = 0                 # the outbox's cursor: every notice at or before it is told (0: re-read all)
        self._unrecorded = set()             # notice keys sent, not yet recorded as told
        self._outcomes = {}                  # D-code row id -> what the authority did with the answer (this process)
        self._told_rows = set()              # D-code rows whose outcome went out, not yet recorded as announced
        self._closed_told = set()            # D-code rows told closed, not yet closed here

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
        whether raised or not; the items not ended; the hosts. A stale view gives one ``view-stale`` item. Each item is
        marked ``drained``: ``drain`` alone tells it (sent first, recorded after: C-SF1), so the check-in lists it in
        its summary and never marks it told itself."""
        try:
            doc, stale = self.view()
        except Exception as exc:
            return [], [], [], [f"runtime snapshot unreadable ({type(exc).__name__})"]
        if stale:
            return [dict(_stale_item(doc), drained=True)], [], [], []
        items = [{"key": n["key"], "kind": n["kind"], "text": n["text"], "drained": True}
                 for n in doc.get("notices", [])
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
        (``add_batch``, which refuses a solo row: N2), a solo one's alone (``mark_sent``). A decision still waiting
        whose answer was refused has a fresh D-code (``_reissue``): that row, kept with its words and digest until it
        went out, is sent here, alone, whatever became of the answered row before it, until its send succeeds; the
        same D-code each time, never a new one per try (Codex part 2 P2-4). Nothing goes out from a stale view; one
        notice's failure never holds up another's. -> message ids."""
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
                    for r in rows:                           # its own message went out already: never twice
                        if r["state"] in ("approved", "declined"):
                            sent.extend(self._send_reissued(store, bot, chat_id, r))
                    continue
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
        """Robert's answers to runtime decisions, taken to the authority once each (its ``resolve``, with the receipt).
        A refusal that leaves the decision waiting at the authority gives it a fresh D-code, sent alone. When the
        authority says the decision is already closed, its records are read again before anything is said (C-N1): an
        answer applied just before a restart (resolved, not yet marked applied here) is reported as applied, never as
        "Not applied". Any other failure leaves the answer for the next tick. What each did is told by
        ``tell_outcomes``. -> [(row, outcome)]"""
        from core.mc_delegation_dispatcher.store import AuthorityRefused
        out = []
        for d in store.runtime_answered():
            if d.get("superseded_by"):
                # Refused before, and replaced: a later D-code of the same decision exists (its insert consumed this
                # answer). Never taken to the authority again, where it could override Robert's newer answer to the
                # replacement (Codex's fix2 re-check); only completed here. deliver sends the replacement if unsent.
                outcome = {"state": "superseded", "by": d["superseded_by"]}
                store.mark_applied(d["id"])
                self._outcomes[d["id"]] = outcome
                out.append((d, outcome))
                continue
            r = d["receipt"] or {}
            receipt = {"channel": r.get("channel"), "turn_id": _turn(r), "words": r.get("text"),
                       "decision_sha256": d["text_sha256"]}
            did = d["requester"]["decision_id"]
            try:
                outcome = self.authority.resolve(did, d["state"], by=self.owner, receipt=receipt)
                if outcome["state"] == "already_resolved":
                    outcome = self._recorded_outcome(store, d)
            except AuthorityRefused as exc:
                outcome = {"state": "refused", "code": str(exc)}
            except Exception as exc:
                self.errors.append(f"apply:{_code(exc)}")
                continue                                     # not taken: tried again next tick
            if outcome["state"] == "refused":
                # The fresh D-code is written before the answer is marked applied (Codex's fix1 delta check): if its
                # write fails, the answer stays unapplied and is taken again, and the fresh D-code's key is fixed by the
                # answered row, so it is made once. Once written it consumes this answer (``superseded_by``): a stop
                # before the mark below never lets this answer reach the authority again (Codex's fix2 re-check).
                made = self._reissue(store, bot, chat_id, d)
                if made is _NOT_WRITTEN:
                    continue                                 # the answer waits; refused again next tick, then written
                outcome["reissued"] = made
            store.mark_applied(d["id"])
            self._outcomes[d["id"]] = outcome
            out.append((d, outcome))
        return out

    def _recorded_outcome(self, store, row) -> dict:
        """What the authority's own records say this answer did (C-N1, C-N4), read now, never assumed: closed by this
        very answer's turn (applied, stale or moot), closed another way, left waiting, or refused and given a fresh
        D-code."""
        did = row["requester"]["decision_id"]
        st = self.authority.decisions_status([did]).get(did) or {"open": False, "answer": "unknown_decision"}
        if not st.get("open"):
            if st.get("turn_id") and st.get("turn_id") == _turn(row["receipt"] or {}):
                answer = st.get("answer")
                if answer in ("approved", "declined"):
                    return {"state": "resolved", "action": st.get("action")}
                if answer == "stale":
                    return {"state": "stale", "action": st.get("action"), "generation_now": st.get("generation_now")}
                if answer == "moot":
                    return {"state": "moot", "action": st.get("action"), "item_state": st.get("item_state")}
            return {"state": "already_resolved", "answer": st.get("answer"), "action": st.get("action")}
        later = [r for r in store.runtime_rows(did) if r["id"] > row["id"]]
        if later and later[-1]["state"] in ("new", "sent"):
            return {"state": "refused", "code": None, "reissued": later[-1]["code"]}
        if later:
            return {"state": "superseded", "by": later[-1]["code"]}
        return {"state": "waiting", "action": "leave"}

    def tell_outcomes(self, store, bot, chat_id) -> list:
        """What each applied answer did, told to Robert in reply to the message that carried the decision (SF2):
        marked told only once it went out (C-N4), so a message lost to a Telegram error goes out at the next tick;
        after a restart the outcome is read again from the authority's records. -> the D-codes told."""
        told = []
        for d in store.runtime_untold():
            if d["id"] not in self._told_rows:
                try:
                    outcome = self._outcomes.get(d["id"]) or self._recorded_outcome(store, d)
                    bot.send(chat_id, outcome_text(d, outcome), reply_to=self._reply_to(store, d))
                except Exception as exc:
                    self.errors.append(f"tell:{_code(exc)}")
                    continue                                 # told next tick
                self._told_rows.add(d["id"])
                self._outcomes.pop(d["id"], None)
                told.append(d["code"])
            try:
                store.mark_announced(d["id"])
                self._told_rows.discard(d["id"])
            except Exception as exc:
                self.errors.append(f"tell_record:{_code(exc)}")   # recorded next tick, never sent again here
        return told

    def _reissue(self, store, bot, chat_id, answered):
        """The fresh D-code for a decision still waiting at the authority after ``answered`` was refused, sent alone.
        Its row is written first (its words and digest) under a key fixed by the answered row: the n-th D-code of a
        decision is replaced by the (n+1)-th, so writing it again finds the same row and never makes another (Codex's
        fix1 delta check). A send that fails is tried again by ``deliver``, after a restart too (P2-4); a row already
        sent is never sent again. -> its code, sent yet or not; None when the decision no longer waits (nothing to
        make); _NOT_WRITTEN when it could not be written (the caller leaves the answer unapplied)."""
        decision_id = answered["requester"]["decision_id"]
        try:
            status = self.authority.decisions_status([decision_id]).get(decision_id) or {}
            if not status.get("open") or not status.get("words"):
                return None
            ids = [r["id"] for r in store.runtime_rows(decision_id)]
            n = (ids.index(answered["id"]) + 1 if answered["id"] in ids else len(ids)) + 1
            row = store.runtime_decision(decision_id, status["words"], notice=f"reissue:{decision_id}", position=1,
                                         solo=True, environment=self.authority.environment, reissue=n)
        except Exception as exc:
            self.errors.append(f"reissue:{_code(exc)}")
            return _NOT_WRITTEN
        if row["state"] == "new":
            try:
                store.mark_sent(row["id"], bot.send(chat_id, tg.runtime_message(row)))
            except Exception as exc:
                self.errors.append(f"reissue_send:{_code(exc)}")     # kept: deliver sends it at the next tick
        return row["code"]

    def _send_reissued(self, store, bot, chat_id, answered) -> list:
        """Every D-code made for the same decision after ``answered`` and never sent (a refused answer's fresh code
        whose send failed, or a process that stopped before it), each sent alone and marked sent. -> message ids."""
        out = []
        for r in store.runtime_rows(answered["requester"]["decision_id"]):
            if r["id"] > answered["id"] and r["state"] == "new":
                message_id = bot.send(chat_id, tg.runtime_message(r))
                store.mark_sent(r["id"], message_id)
                out.append(message_id)
        return out

    def close_closed(self, store, bot, chat_id) -> list:
        """Runtime D-codes whose decision the authority closed itself (a revocation withdrew it, a new question
        superseded it, its item ended, another owner act resolved it): Robert is told once why (SF2), and then the
        D-code is closed here so it can no longer be answered. Told first, closed after (C-N4): a message lost to a
        Telegram error leaves the D-code open, to be told at the next tick; an answer given meanwhile is applied and
        told as already closed. -> [(D-code, reason)]"""
        rows = store.runtime_open()
        if not rows:
            return []
        status = self.authority.decisions_status(sorted({r["requester"]["decision_id"] for r in rows}))
        out = []
        for r in rows:
            st = status.get(r["requester"]["decision_id"]) or {"open": False, "answer": "unknown_decision"}
            if st.get("open"):
                continue
            reason = closed_reason(st)
            if r["id"] not in self._closed_told:
                try:
                    bot.send(chat_id, f"{r['code']} is no longer open: {reason}. Nothing to answer.",
                             reply_to=self._reply_to(store, r))
                except Exception as exc:
                    self.errors.append(f"tell:{_code(exc)}")
                    continue
                self._closed_told.add(r["id"])
            try:
                store.close_runtime(r["id"])
                self._closed_told.discard(r["id"])
            except Exception as exc:
                self.errors.append(f"close_record:{_code(exc)}")   # closed next tick, never told again here
                continue
            out.append((r["code"], reason))
        return out

    @staticmethod
    def _reply_to(store, row):
        return row.get("telegram_message_id") or store.batch_of(row["id"])

    # ── rule notices, at the sweep that raised them (SF5) ──
    def drain(self, store, bot, chat_id) -> list:
        """The authority's outbox (notice.raised, recorded at each sweep) read on the supervisor's own short timer, from
        its cursor: each rule notice not told before (the once-per-key memory the check-in shares) goes out, even if it
        has cleared since; decisions go out through ``deliver``. A dead sweeper raises nothing, so its live notice is
        read from the snapshot here too. A notice is recorded as told only after the message carrying it went out
        (C-SF1): a failed send, or a database error, leaves it for the next tick; the cursor never passes a notice not
        yet told, and a restart reads the outbox again from the start, whatever a notice's age, skipping what was told
        (DUP-7). At most ``MESSAGES_PER_TICK`` messages a tick, oldest first. -> the keys sent."""
        self._record_told(store)
        outbox, last = [], self._outbox_seq
        for _ in range(OUTBOX_PAGES):
            page = self.authority.notices_since(last, hours=None)
            for e in page:
                last = max(last, e["seq"])
                n = e["notice"]
                if n.get("rule") in ("decision", "decisions") or not n.get("key") or not n.get("text"):
                    continue
                outbox.append({"key": n["key"], "kind": n.get("kind") or "error", "text": n["text"], "seq": e["seq"]})
            if len(page) < OUTBOX_PAGE:
                break
        live = []
        try:
            doc, stale = self.view()
            live.extend({"key": n["key"], "kind": n["kind"], "text": n["text"]} for n in self.sweeper_stale(doc))
            if stale:
                live.append(_stale_item(doc))
        except Exception as exc:
            self.errors.append(f"view:{_code(exc)}")
        items = outbox + live
        if not items:
            self._outbox_seq = last
            return []
        done = set(store.told_keys(sorted({i["key"] for i in items}))) | self._unrecorded
        untold, seen = [], set()
        for i in items:
            if i["key"] not in done and i["key"] not in seen:
                seen.add(i["key"])
                untold.append(i)
        sent = []
        from core.supervision.watch import notices_message
        for chunk in _chunks(untold)[:MESSAGES_PER_TICK]:
            try:
                bot.send(chat_id, notices_message(chunk))
            except Exception as exc:
                self.errors.append(f"drain_send:{_code(exc)}")
                break                                        # the rest, and this, next tick
            keys = [i["key"] for i in chunk]
            self._unrecorded.update(keys)
            sent.extend(keys)
        self._record_told(store)
        done |= set(sent)
        waiting = [i["seq"] for i in outbox if i["key"] not in done]
        self._outbox_seq = min(waiting) - 1 if waiting else last
        return sent

    def _record_told(self, store):
        """The notice keys sent and not yet recorded, recorded now (the check-in's once-per-key memory)."""
        if not self._unrecorded:
            return
        try:
            store.new_alert_keys(sorted(self._unrecorded))
            self._unrecorded.clear()
        except Exception as exc:
            self.errors.append(f"drain_record:{_code(exc)}")

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
    if state == "superseded":
        return (f"Not applied: {code} was refused and replaced by {outcome.get('by')}; nothing was done. Your answer "
                f"to {outcome.get('by')} is the one taken.")
    why = f" ({outcome['code']})" if outcome.get("code") else ""     # after a restart the refusal's code is not kept
    return (f"Not applied: {code} was refused{why}; nothing was done"
            + (f". It still waits, as {outcome['reissued']}." if outcome.get("reissued") else "."))


def closed_reason(status) -> str:
    answer = status.get("answer")
    if answer == "withdrawn" and status.get("code") == "item_ended":
        return f"its item has ended ({status.get('item_state')})"           # C-N3: a review it no longer needs
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


def _chunks(items) -> list:
    """Notices in messages of at most NOTICES_PER_MESSAGE and MESSAGE_CHARS each, in order (C-SF1): never one message
    the bot would cut, so every notice marked told was in a message that went out whole."""
    from core.supervision.watch import notices_message
    out, cur = [], []
    for i in items:
        if cur and (len(cur) >= NOTICES_PER_MESSAGE or len(notices_message(cur + [i])) > MESSAGE_CHARS):
            out.append(cur)
            cur = []
        cur.append(i)
    if cur:
        out.append(cur)
    return out


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
    from core.mc_delegation_dispatcher import production
    from core.mc_delegation_dispatcher.store import Authority, DispatcherStore
    auth = Authority.load(environ["MINIMOI_RUNTIME_AUTHORITY_FILE"])
    # P1-a: the same grant catalogue every production process builds over this file (production.grant_store).
    store = DispatcherStore(connect, principal=environ["MINIMOI_RUNTIME_PRINCIPAL"], environment=auth.environment,
                            authority=auth, grants=production.grant_store(auth))
    bound = environ["MINIMOI_RUNTIME_VIEW_BOUND_S"]
    if not bound.isdigit():
        raise ValueError("runtime_link_invalid")
    state_file = environ.get("MINIMOI_RUNTIME_STATE_FILE")
    source = None if not state_file else (lambda: snap.read_state_file(os.path.expanduser(state_file)))
    return RuntimeLink(store, owner=environ["MINIMOI_RUNTIME_OWNER"], view_bound_s=int(bound), source=source)

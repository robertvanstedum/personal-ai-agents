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
    (default: built live from the authority; or ``snapshot.read_state_file`` of the published ``state.json``)."""

    def __init__(self, authority, *, owner, view_bound_s, source=None):
        if not isinstance(owner, str) or not owner or not isinstance(view_bound_s, int) or view_bound_s < 1:
            raise ValueError("runtime_link_invalid")
        self.authority, self.owner, self.view_bound_s = authority, owner, view_bound_s
        self.source = source or authority.snapshot

    def view(self):
        """(snapshot, stale): stale when its age is unknown or past the bound (R4.6-b: then nothing fires from it)."""
        doc = self.source()
        return doc, snap.view_stale(doc, self.view_bound_s)

    def check_in(self):
        """(items, running, runners, errors) for the check-in's delegation part, from the snapshot alone: the notices
        the authority raised, except decisions (``deliver`` sends those), with their keys; the items not ended; the
        hosts. A stale view gives one ``view-stale`` item and nothing else."""
        try:
            doc, stale = self.view()
        except Exception as exc:
            return [], [], [], [f"runtime snapshot unreadable ({type(exc).__name__})"]
        if stale:
            return [{"key": f"view-stale:runtime:{doc.get('as_of')}", "kind": "error",
                     "text": f"The runtime's state is {_age_words(doc.get('view_age_s'))} old (as of "
                             f"{doc.get('as_of')}); no delegation rule fires from it until it is fresh."}], [], [], []
        items = [{"key": n["key"], "kind": n["kind"], "text": n["text"]} for n in doc.get("notices", [])
                 if n.get("raised_at") and n.get("rule") not in ("decision", "decisions")]
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

    def deliver(self, store, bot, chat_id) -> list:
        """Each decision notice the authority raised, not yet sent, as one message (R7.4-c): its decisions become
        D-codes (supervision rows, the authority's words), a routine notice's together under one message
        (``add_batch``), a solo one's alone (``mark_sent``). Nothing goes out from a stale view. -> message ids."""
        doc, stale = self.view()
        if stale:
            return []
        sent = []
        for n in doc.get("notices", []):
            if n.get("rule") not in ("decision", "decisions") or not n.get("raised_at") or not n.get("words"):
                continue
            rows = [store.runtime_decision(w["decision_id"], w["words"], notice=n["key"], position=i,
                                           solo=bool(n.get("solo")), environment=doc["environment"])
                    for i, w in enumerate(n["words"], 1)]
            if any(r["state"] != "new" for r in rows):
                continue                                     # sent (or answered) already: never twice
            if n["rule"] == "decision":
                message_id = bot.send(chat_id, tg.runtime_message(rows[0]))
                store.mark_sent(rows[0]["id"], message_id)
            else:
                message_id = bot.send(chat_id, tg.batch_message(rows))
                store.add_batch(message_id, [r["id"] for r in rows])
            sent.append(message_id)
        return sent

    def apply(self, store) -> list:
        """Robert's answers to runtime decisions, taken to the authority once each (its ``resolve``, with the receipt).
        A refusal is recorded as the answer's outcome and not retried; any other failure leaves it for the next tick.
        -> [(D-code, outcome)]"""
        from core.mc_delegation_dispatcher.store import AuthorityRefused
        out = []
        for d in store.runtime_answered():
            r = d["receipt"] or {}
            receipt = {"channel": r.get("channel"), "turn_id": _turn(r), "words": r.get("text"),
                       "decision_sha256": d["text_sha256"]}
            try:
                outcome = self.authority.resolve(d["requester"]["decision_id"], d["state"], by=self.owner,
                                                 receipt=receipt)
            except AuthorityRefused as exc:
                outcome = {"state": "refused", "code": str(exc)}
            store.mark_applied(d["id"])
            out.append((d["code"], outcome))
        return out


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

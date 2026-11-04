"""Scoped, read-only reconstruction of a request or task from the task service's own evidence (spec v1.1 Q07, B1E).

Starting from a request or task id, it returns the chain instruction -> corrections -> interpretation(s) -> authority
-> request -> task -> assignments (as of any time) -> execution references -> reviews and acceptances, with every
journal line that touched those records, and the gaps. It reads only the journal and the documents it verifies; it
never asks an agent to remember and never calls a model.

What it is careful about:

- **Facts, reports, hypotheses.** A journal line is an observed fact that a seat recorded something at a position in
  this store. What a worker says happened elsewhere (its evidence references, a ``working`` or ``failed`` report) is
  an actor report until another authority (the harness, B2) is joined and verifies it. The query makes no hypotheses;
  the ``hypotheses`` list is for the reviewer.
- **Coverage.** Stores not joined here (harness attempts, obligation occurrences, billing; see joins.py for the B2E
  synthetic joins) are listed as ``not_joined``. Notices are task-service outbox records and are shown. A missing cost is "no cost evidence", never zero, and no per-agent charge is computed.
- **Integrity.** A held store answers with ``integrity: damaged`` and no chain at all: nothing unverified is claimed.
- **Scope.** Records and journal lines outside the caller's read scopes are omitted and never counted; an
  out-of-scope start id answers exactly like a missing one. Store-wide positions (journal sequence numbers, the
  chain head) are not returned: they would reveal how much out-of-scope work happened. Timeline entries carry a
  dense position within this answer. Reading confers no authority.
- **Reads complete abandoned writes.** Like every read of this store, a query first finishes any intent a dead
  writer left (E01), so it may append that intent's completed line. It never creates a new record.
"""
from __future__ import annotations

from datetime import datetime

from services.partnership_tasks.journal import JournalDamaged
from services.partnership_tasks.service import DIRS, ID_FIELD, StoreUnavailable, TaskService, path_of

SCHEMA = "minimoi.partnership.reconstruction/1"
NOT_JOINED_B1 = ("harness", "obligations", "billing_fixture", "artifacts", "grants", "records")
REPORT_KINDS = {"task.transition", "request.answer"}


def _t(ts: str | None):
    return None if ts is None else datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_at(at: str | None):
    """``at`` must be an ISO time with an explicit offset; anything else is refused, never guessed."""
    if at is None:
        return None
    try:
        value = _t(at)
    except (TypeError, ValueError):
        raise ValueError("at_not_iso_time") from None
    if value.tzinfo is None:
        raise ValueError("at_needs_offset")
    return value


class Reconstructor:
    def __init__(self, svc: TaskService):
        self.svc = svc

    def reconstruct(self, start_type: str, start_id: str, token: str, *, at: str | None = None) -> dict | None:
        """The reconstruction, or None when the start record is missing or outside the caller's read scope."""
        parse_at(at)
        binding = self.svc.bindings.resolve(token, self.svc._clock())
        if binding is None or start_type not in ("request", "task"):
            return None
        readable = binding.read_scopes
        try:
            return self.svc.journal.view(lambda: self._build(start_type, start_id, readable, at))
        except (JournalDamaged, OSError) as exc:
            # Do not even say whether the start record exists: the caller may lack its scope.
            return {"schema": SCHEMA, "start": {"type": start_type, "id": start_id}, "integrity": "damaged",
                    "detail": "The task store is held after an integrity check failed; no chain is reported from it.",
                    "chain": None, "timeline": [], "gaps": ["task store integrity hold: " + type(exc).__name__]}

    # ---- under the journal lock ----------------------------------------------------------------------------------

    def _doc(self, kind: str, id_: str | None, readable) -> dict | None:
        if id_ is None:
            return None
        rec = self.svc.journal.read(path_of(kind, id_))
        return rec if rec is not None and rec.get("data_scope") in readable else None

    def _build(self, start_type, start_id, readable, at):
        start = self._doc(start_type, start_id, readable)
        if start is None:
            return None
        gaps: list[str] = []
        request = start if start_type == "request" else self._doc("request", start["request_ref"], readable)
        task = start if start_type == "task" else self._doc("task", start.get("task_ref"), readable)
        if start_type == "task" and request is None:
            gaps.append("The task's request is not available to you.")
        if start_type == "request" and start.get("task_ref") and task is None:
            gaps.append("The promoted task is not available to you.")

        # instruction and corrections
        source_ids = []
        if request:
            source_ids = [request["source_event_id"]] + [e["source_event_id"] for e in request["refinements"]]
            source_ids.append(request["intent"]["source_event_id"])
        interps = self._interpretation_chain(request, task, readable)
        for it in interps:
            source_ids += it["source_event_ids"] + it["correction_event_ids"]
        sources, seen = [], set()
        for sid in source_ids:
            if sid in seen:
                continue
            seen.add(sid)
            se = self._doc("source_event", sid, readable)
            if se is None:
                gaps.append(f"Source event {sid} is referenced but not available to you.")
            else:
                sources.append(se)
        corrections = [s for s in sources if s["correction_of"]]

        # authority, interpretation status
        current_interp = None
        if task:
            current_interp = task["interpretation_ref"]
        elif request:
            current_interp = request["interpretation_ref"]
        if request and request["interpretation_ref"] is None and task is None:
            gaps.append("No interpretation was recorded for this request (light form: no consequential action).")
        authority = [{"interpretation_id": it["interpretation_id"], "disposition": it["disposition"],
                      "disposition_by": it["disposition_by"], "authority": it["authority"],
                      "authority_basis": {"none": "none recorded",
                                          "owner_confirmation": "owner confirmation from the owner's own session",
                                          "existing_grant": "a standing grant, checked when adopted"}[
                                              it["authority"]["kind"]],
                      "authority_verified_now": False,
                      "adoption_is_not_approval": True} for it in interps]
        if any(it["authority"]["kind"] == "existing_grant" for it in interps):
            gaps.append("Grant references were checked when adopted; the grant store is not joined in B1, so their "
                        "current terms are not shown.")
        for it in interps:
            if it["interpretation_id"] == current_interp and it["disposition"] == "adopted" \
                    and it["authority"]["kind"] == "none":
                gaps.append(f"{it['interpretation_id']} was adopted with no authority reference.")

        # journal timeline for these records only (observed facts) with actor reports marked
        targets = set()
        for rec_kind, rec in (("request", request), ("task", task)):
            if rec:
                targets.add(path_of(rec_kind, rec[ID_FIELD[rec_kind]]))
        for it in interps:
            targets.add(path_of("interpretation", it["interpretation_id"]))
        for se in sources:
            targets.add(path_of("source_event", se["source_event_id"]))
        timeline, revisions = [], {}
        intents = {}
        for row in self.svc.journal.entries_unlocked():
            if row["t"] == "intent":
                intents[row["command_id"]] = row
                continue
            if row["t"] != "completed" or row["data_scope"] not in readable:
                continue
            touched = [p for p in row["targets"] if p in targets]
            if not touched:
                continue
            for w in intents.get(row["command_id"], {"writes": []})["writes"]:
                if w["path"] in targets:
                    revisions.setdefault(w["path"], []).append({"recorded_at": row["recorded_at"],
                                                                "record": w["record"]})
            # The journal line is the observed fact; what a transition or an answer says about the world is the
            # actor's report, whoever sent it (a lead's own "working" is still its claim).
            report = row["kind"] in REPORT_KINDS
            timeline.append({"position": len(timeline) + 1, "recorded_at": row["recorded_at"],
                             "event_at": row["event_at"],
                             "command_id": row["command_id"], "kind": row["kind"], "targets": touched,
                             "actor_seat": row["actor_seat"], "origin": row["origin"],
                             "session_id": row["session_id"], "runtime": row["runtime"],
                             "causal_parent": row["causal_parent"], "basis_ref": row["basis_ref"],
                             "links": row["links"], "recovered": row["recovered"],
                             "classification": "actor_report" if report else "observed_record"})

        # as-of view: the latest revision recorded at or before ``at``
        as_of = None
        if at is not None:
            as_of = {}
            for path, revs in revisions.items():
                chosen = None
                for r in revs:
                    if _t(r["recorded_at"]) <= _t(at):              # the store's own observation time
                        chosen = r["record"]
                as_of[path] = chosen

        assignments = []
        if task:
            for a in task["assignments"]:
                live = at is None or (_t(a["effective_from_at"]) <= _t(at)
                                      and (a["effective_to_at"] is None or _t(at) < _t(a["effective_to_at"])))
                assignments.append(dict(a, in_force_at_query_time=live))
            for r in task["execution_refs"]:
                if r["store"] != "task_service":
                    gaps.append(f"Execution reference {r['store']}:{r['id']} is an actor report; "
                                f"{r['store']} evidence is not joined in B1.")
            current_acc = [a for a in task["acceptances"] if a["status"] == "current"]
            if task["state"] == "completed" and not current_acc:
                gaps.append("Task is completed with no current acceptance.")

        gaps.append("Dispatch checks are answered but not recorded in B1; fencing is shown by fence_generation "
                    "changes in the record revisions.")
        coverage = [{"store": "task_service", "status": "verified"}]
        coverage += [{"store": s, "status": "not_joined"} for s in NOT_JOINED_B1]
        return {
            "schema": SCHEMA,
            "synthetic": self.svc.environment == "synthetic",
            "environment": self.svc.environment,
            "start": {"type": start_type, "id": start_id},
            "at": at,
            "as_of_basis": "journal recorded_at" if at else None,
            "integrity": "verified",
            "chain": {
                "instructions": [s for s in sources if not s["correction_of"]],
                "corrections": corrections,
                "interpretations": interps,
                "current_interpretation": current_interp,
                "authority": authority,
                "request": request,
                "task": task,
            },
            "as_of": as_of,
            "assignments": assignments,
            "execution_refs": task["execution_refs"] if task else [],
            "reviews": task["reviews"] if task else [],
            "artifact_sets": task["artifact_sets"] if task else [],
            "notices": self._notices(task, readable) if task else [],
            "divergence": self._divergence(interps, sources, timeline),
            "acceptances": task["acceptances"] if task else [],
            "costs": {"status": "no_cost_evidence", "detail": "Billing and usage receipts are not joined in B1; "
                      "no estimate, reservation or charge is attributed to any seat."},
            "timeline": timeline,
            "hypotheses": [],
            "coverage": coverage,
            "gaps": gaps,
            "scope_note": "Records outside your read scopes are neither shown nor counted.",
        }

    @staticmethod
    def _divergence(interps, sources, timeline) -> list[dict]:
        """F15: where a reading first stopped matching the instruction, as recorded facts only: each interpretation
        that was later corrected or superseded, the correction events that followed, and the timeline position at
        which the superseded reading was recorded and adopted. No judgement of who was at fault."""
        out = []
        corrections = {s["source_event_id"]: s for s in sources if s["correction_of"]}
        for it in interps:
            if it["superseded_by"] is None and it["disposition"] != "corrected":
                continue
            succ = next((x for x in interps if x["interpretation_id"] == it["superseded_by"]), None)
            cited = [] if succ is None else [corrections[c]["source_event_id"] for c in succ["correction_event_ids"]
                                             if c in corrections]
            positions = [t["position"] for t in timeline if f"interpretations/{it['interpretation_id']}.json"
                         in t["targets"]]
            out.append({"interpretation_id": it["interpretation_id"], "interpreter": it["interpreter"],
                        "proposed_outcome": it["proposed"]["outcome"], "disposition": it["disposition"],
                        "authority": it["authority"], "superseded_by": it["superseded_by"],
                        "corrections": cited, "recorded_at_positions": positions})
        return out

    def _notices(self, task, readable) -> list[dict]:
        """B2D: the notices recorded for this task (and its children): what was sent, delivered and read."""
        folder = self.svc.journal.root / DIRS["outbox"]
        ids = {task["task_id"]}
        tdir = self.svc.journal.root / DIRS["task"]
        for p in tdir.glob("TK-*.json") if tdir.is_dir() else []:
            t = self._doc("task", p.stem, readable)
            if t and t["parent_ref"] == task["task_id"]:
                ids.add(t["task_id"])
        out = []
        for p in sorted(folder.glob("OB-notice-*.json")) if folder.is_dir() else []:
            ob = self._doc("outbox", p.stem, readable)
            if ob and ob["task_id"] in ids:
                out.append({"operation_id": ob["operation_id"], "kind": ob["packet"]["kind"], "state": ob["state"],
                            "packet": ob["packet"], "attempts": ob["attempts"]})
        return out

    def _interpretation_chain(self, request, task, readable) -> list[dict]:
        """Every interpretation linked from the request/task, following supersession both ways."""
        ids = [x for x in ((request or {}).get("interpretation_ref"), (task or {}).get("interpretation_ref")) if x]
        out, seen = [], set()
        folder = self.svc.journal.root / DIRS["interpretation"]
        by_successor = {}
        if folder.is_dir():
            for p in folder.glob("IN-*.json"):
                rec = self._doc("interpretation", p.stem, readable)
                if rec and rec["superseded_by"]:
                    by_successor[rec["superseded_by"]] = rec["interpretation_id"]
        queue = list(ids)
        while queue:
            iid = queue.pop()
            if iid in seen:
                continue
            seen.add(iid)
            rec = self._doc("interpretation", iid, readable)
            if rec is None:
                continue
            out.append(rec)
            if rec["superseded_by"]:
                queue.append(rec["superseded_by"])
            if iid in by_successor:
                queue.append(by_successor[iid])
        return sorted(out, key=lambda r: r["created_at"] + r["interpretation_id"])


def open_work(svc: TaskService, token: str) -> list[dict]:
    """D03/J1: the open-work list: active tasks and requests still awaiting an answer. Answered, promoted, withdrawn
    and failed requests and terminal tasks stay in history, not here."""
    binding = svc.bindings.resolve(token, svc._clock())
    if binding is None:
        return []
    def read():
        items = []
        for kind, open_states in (("task", ("submitted", "working", "waiting")), ("request", ("recorded", "active"))):
            folder = svc.journal.root / DIRS[kind]
            for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
                rec = svc.journal.read(f"{DIRS[kind]}/{p.name}")
                if rec and rec["data_scope"] in binding.read_scopes and rec["state"] in open_states:
                    items.append({"type": kind, "id": rec[ID_FIELD[kind]], "state": rec["state"]})
        return items
    try:
        return svc.journal.view(read)
    except (JournalDamaged, OSError) as exc:
        raise StoreUnavailable(type(exc).__name__) from exc


def history(svc: TaskService, token: str, words: str) -> list[dict]:
    """J1 next-session recall: requests whose intent, any earlier edition, or source words contain all ``words``
    (case-insensitive), with the coverage and results each edition had. Scoped like every read."""
    binding = svc.bindings.resolve(token, svc._clock())
    if binding is None:
        return []
    terms = [w.lower() for w in words.split() if w]
    def read():
        found = []
        folder = svc.journal.root / DIRS["request"]
        for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            rec = svc.journal.read(f"{DIRS['request']}/{p.name}")
            if not rec or rec["data_scope"] not in binding.read_scopes:
                continue
            texts = [rec["intent"]["text"]] + [e["text"] for e in rec["refinements"]]
            for sid in {rec["source_event_id"], rec["intent"]["source_event_id"]} | {
                    e["source_event_id"] for e in rec["refinements"]}:
                src = svc.journal.read(path_of("source_event", sid))
                if src and src["data_scope"] in binding.read_scopes:
                    texts.append(src["content"])
            blob = " ".join(texts).lower()
            if all(t in blob for t in terms):
                found.append({"request_id": rec["request_id"], "state": rec["state"], "intent": rec["intent"],
                              "result_refs": rec["result_refs"], "coverage": rec["coverage"],
                              "editions": rec["refinements"], "task_ref": rec["task_ref"]})
        return found
    try:
        return svc.journal.view(read)
    except (JournalDamaged, OSError) as exc:
        raise StoreUnavailable(type(exc).__name__) from exc

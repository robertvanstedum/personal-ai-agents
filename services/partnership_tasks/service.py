"""The partnership task service (spec v1.1 sections 2-5 and 8): source events, interpretations, requests and tasks.

Every change is one command through ``submit(command, token)``:

    authenticate the token to a seat binding (A02)  ->  validate the contract (payload bound to kind)
    -> environment, scope and operation from the binding  ->  command digest (whole command + calling seat)
    -> the journal: replay / refuse a reused id / plan under the lock / one intent / read-back / receipt

Planning enforces the authority rules of the spec: who may accept (E07: the current lead), who may change the lead
(D01), what promotion needs (Q02: an adopted interpretation with real authority, never adoption alone), and fencing
(C06: every correction or reassignment bumps ``fence_generation``; anything carrying an older generation is refused
as ``fenced``). The service imports nothing from CoS, Records or the harness (A01, F11); it links to them by ``ref``.

Reads are scoped (Q07): a record outside the caller's read scopes is answered exactly like a missing one.
"""
from __future__ import annotations

import json

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.canonical_json import digest
from services.partnership_tasks import contracts
from services.partnership_tasks.auth import Binding, Bindings, Grants
from services.partnership_tasks.charters import SCHEDULE_STORE
from services.partnership_tasks.journal import Conflict, Journal, JournalDamaged, Plan, Refused, Write


# The one seat that records outbox sends and destination answers (E03, Q05; Codex finding 2, 4 Nov).
DRAINER_SEAT = "outbox-drainer"
# Where the portal records Robert's own signed confirm/decline/correct/cancel actions (B4 design v0.2 §2, v0.3 R-M1).
OWNER_ACTION_STORE = "portal-owner-action"
APPROVAL_WINDOW_S = 900          # an admission approval is good for 15 minutes from when it was recorded (v0.5 M3)

class StoreUnavailable(Exception):
    """The store is held after an integrity failure (or cannot be read): never answered as "not found"."""

RESULT = "minimoi.partnership.result/1"
DIRS = {"source_event": "source_events", "interpretation": "interpretations", "request": "requests", "task": "tasks",
        "outbox": "outbox"}
SCHEMAS = {t: f"minimoi.partnership.{t}/1" for t in DIRS}
ID_FIELD = {"source_event": "source_event_id", "interpretation": "interpretation_id", "request": "request_id",
            "task": "task_id", "outbox": "operation_id"}
ACTIVE_TASK = ("submitted", "working", "waiting")
TERMINAL_TASK = ("completed", "failed", "canceled", "rejected")
SINGLE_HOLDER_ROLES = ("lead", "holder", "verifier", "duty")
FENCE_OP_PREFIX = "OB-fence-"
NOTICE_OP_PREFIX = "OB-notice-"
WAKE_OP_PREFIX = "OB-wake-"
MAX_CHILD_ROUNDS = 3          # J3: bounded revision of a delegated step
REPORTS_EFFECTS = ("task.transition", "task.assign", "task.reopen", "task.adopt_interpretation", "correction.apply")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path_of(target_type: str, target_id: str) -> str:
    return f"{DIRS[target_type]}/{target_id}.json"


class TaskService:
    def __init__(self, root: Path | str, environment: str, bindings: Bindings, *, grants: Grants | None = None,
                 clock: Callable[[], str] = _now, charters=None, helper_returns: bool = False):
        if environment not in ("synthetic", "dev", "staging", "prod"):
            raise ValueError("environment")
        self.environment = environment
        self.helper_returns = helper_returns
        self.bindings = bindings
        self.grants = grants or Grants()
        from services.partnership_tasks.charters import SCHEDULE_STORE, Charters
        self.charters = charters or Charters()              # helper seats spec v0.4 §3.1: none until a charter is loaded
        self._clock = clock
        self.journal = Journal(root, result_schema=RESULT, clock=clock, doc_dirs=tuple(DIRS.values()))

    # ---- commands ------------------------------------------------------------------------------------------------

    def submit(self, command: dict, token: str) -> dict:
        command_id = command.get("command_id") if isinstance(command, dict) else None
        command_id = command_id if isinstance(command_id, str) else "unknown"
        binding = self.bindings.resolve(token, self._clock())
        if binding is None:
            return _result(command_id, "refused", code="not_authenticated")
        if contracts.errors("command", "command", command):
            return _result(command_id, "refused", code="payload_invalid")
        if command["environment"] != self.environment or binding.environment != self.environment:
            return _result(command_id, "refused", code="wrong_environment")
        if command["data_scope"] not in binding.scopes:
            return _result(command_id, "refused", code="not_authorized", detail="scope")
        if command["kind"] not in binding.operations:
            return _result(command_id, "refused", code="not_authorized", detail="operation")
        if digest(command["payload"]) != command["payload_digest"]:
            return _result(command_id, "refused", code="payload_digest_mismatch")
        command_digest = digest({"command": command, "seat": binding.seat, "binding": binding.binding_id})
        def plan():
            # Publication authority must still exist after waiting for the writer lock.
            if command["kind"] == "task.commit_helper_return" and self.bindings.resolve(token, self._clock()) != binding:
                raise Refused("not_authenticated", "binding_changed")
            return _Planner(self, command, binding).plan()
        result = self.journal.run(command["command_id"], command_digest, plan)
        if result["outcome"] == "accepted" and command["kind"] in REPORTS_EFFECTS:
            # E07/Q05: a cancel, failure, reassignment or correction says which effects may already exist.
            result = dict(result, open_effects=self._open_effects(command, binding))
        return result

    def _open_effects(self, command, binding) -> list[str]:
        task_id = command["target"]["id"]
        if command["target"]["type"] == "request":
            rec = self.journal.view(lambda: self.journal.read(path_of("request", command["target"]["id"])))
            task_id = rec["task_ref"] if rec else None
        if task_id is None:
            return []
        def read():
            task_ids = {task_id}
            tdir = self.journal.root / DIRS["task"]
            for path in sorted(tdir.glob("TK-*.json")) if tdir.is_dir() else []:
                t = self.journal.read(f"{DIRS['task']}/{path.name}")
                if t and t["parent_ref"] == task_id:
                    task_ids.add(t["task_id"])
            folder = self.journal.root / DIRS["outbox"]
            out = []
            for path in sorted(folder.glob("OB-*.json")) if folder.is_dir() else []:
                ob = self.journal.read(f"{DIRS['outbox']}/{path.name}")
                if ob and ob["task_id"] in task_ids and ((ob["operation"] == "admit_bundle" and ob["state"] in ("attempted", "unknown", "conflict"))
                             or (ob["operation"] == "publish_return" and ob["state"] in ("pending", "attempted", "unknown", "conflict"))) \
                        and ob["data_scope"] in binding.read_scopes:
                    out.append(ob["operation_id"])
            return out
        return self.journal.view(read)

    def receipt(self, command: dict, token: str) -> dict:
        """recorded / different / absent / unknown for one command as this caller would send it."""
        binding = self.bindings.resolve(token, self._clock())
        cid = command.get("command_id", "unknown")
        if binding is None:
            return {"schema": "minimoi.partnership.receipt_lookup/1", "command_id": cid, "answer": "unknown",
                    "result": None}
        return self.journal.receipt(cid, digest({"command": command, "seat": binding.seat,
                                                 "binding": binding.binding_id}))

    # ---- scoped reads ----------------------------------------------------------------------------------------------

    def get(self, target_type: str, target_id: str, token: str) -> dict | None:
        """The record, or None when it is missing, out of the caller's read scope, or the caller is unknown. Raises
        StoreUnavailable when the store is held."""
        binding = self.bindings.resolve(token, self._clock())
        if binding is None or target_type not in DIRS:
            return None
        try:
            record = self.journal.view(lambda: self.journal.read(path_of(target_type, target_id)))
        except (JournalDamaged, OSError) as exc:
            raise StoreUnavailable(type(exc).__name__) from exc
        if record is None or record.get("data_scope") not in binding.read_scopes:
            return None
        return record

    def release_gate(self, task_id: str, token: str, artifact_set_digest: str | None = None) -> dict:
        """E06: a downstream gate compares current, reviewed and accepted sets from the record, even if notices lag.
        With ``artifact_set_digest`` it checks that pinned edition instead of the current one. Open only when that set
        is the accepted one, has an applicable non-author approval and the task is completed. Never opens on error."""
        try:
            task = self.get("task", task_id, token)
        except StoreUnavailable:
            return {"gate": "closed", "code": "store_unavailable"}
        if task is None:
            return {"gate": "closed", "code": "not_found"}
        current = task["artifact_sets"][-1]["digest"] if task["artifact_sets"] else None
        want = artifact_set_digest or current
        accepted = next((a["artifact_set_digest"] for a in reversed(task["acceptances"]) if a["status"] == "current"),
                        None)
        authors = _Planner.authors_of(task, want) if want else set()
        approved = [r for r in task["reviews"] if r["reviewed_set_digest"] == want and r["verdict"] == "approve" and not r.get("no_verdict")
                    and r["reviewer"] not in authors and r["interpretation_ref"] == task["interpretation_ref"]]
        ok = want is not None and accepted == want and bool(approved) and task["state"] == "completed"
        return {"gate": "open" if ok else "closed", "checked": want, "current": current, "accepted": accepted,
                "approved_by": sorted({r["reviewer"] for r in approved})}

    def dispatch_check(self, task_id: str, fence_generation: int, interpretation_id: str, token: str) -> dict:
        """C06/Q05 for an execution authority about to dispatch: is this candidate still current? Never grants
        anything; it only says whether the task's fence and adopted interpretation still match the candidate, and
        whether that interpretation is still actionable (adopted, current, real authority, no unreconciled
        correction the caller can see)."""
        binding = self.bindings.resolve(token, self._clock())
        if binding is None:
            return {"dispatch": "refused", "code": "not_found"}

        def check():
            task = self.journal.read(path_of("task", task_id))
            if task is None or task["data_scope"] not in binding.read_scopes:
                return {"dispatch": "refused", "code": "not_found"}
            if task["fence_generation"] != fence_generation or task["interpretation_ref"] != interpretation_id:
                return {"dispatch": "refused", "code": "fenced", "current_fence": task["fence_generation"],
                        "current_interpretation": task["interpretation_ref"]}
            if task["state"] not in ("submitted", "working"):
                return {"dispatch": "refused", "code": "task_not_active", "state": task["state"]}
            req = self.journal.read(path_of("request", task["request_ref"]))
            problem = interpretation_problem(self.journal, binding.read_scopes, task["interpretation_ref"],
                                             lineage(req) if req else set())
            if problem:
                return {"dispatch": "refused", "code": "fenced", "detail": problem}
            return {"dispatch": "current", "task_revision": task["revision"]}
        try:
            return self.journal.view(check)
        except (JournalDamaged, OSError):
            return {"dispatch": "unknown", "code": "store_unavailable"}


def _edition(req: dict) -> dict:
    """The request's current intent edition, kept with the results and coverage that answered it."""
    return {"intent_revision": req["intent"]["revision"], "text": req["intent"]["text"],
            "source_event_id": req["intent"]["source_event_id"], "result_refs": req["result_refs"],
            "coverage": req["coverage"]}


def lineage(req: dict) -> set:
    """Every source event a request's intent editions came from."""
    return ({req["source_event_id"], req["intent"]["source_event_id"]}
            | {e["source_event_id"] for e in req["refinements"]})


def unreconciled(journal: Journal, readable, source_ids, cited) -> list[str]:
    """C06: corrections (transitively: corrections of corrections) of these source events, visible to the caller,
    that ``cited`` does not include. Out-of-scope corrections are neither counted nor revealed."""
    folder = journal.root / DIRS["source_event"]
    events = []
    for path in sorted(folder.glob("SE-*.json")) if folder.is_dir() else []:
        ev = journal.read(f"{DIRS['source_event']}/{path.name}")
        if ev and ev["data_scope"] in readable and ev["correction_of"]:
            events.append(ev)
    targets, found, changed = set(source_ids) | set(cited), [], True
    while changed:
        changed = False
        for ev in events:
            sid = ev["source_event_id"]
            if ev["correction_of"] in targets and sid not in targets:
                targets.add(sid)
                changed = True
                if sid not in cited:
                    found.append(sid)
    return found


def interpretation_problem(journal: Journal, readable, interpretation_id, request_lineage) -> str | None:
    """Q02/C06: why an interpretation may not drive consequential work now, or None when it may."""
    interp = None if interpretation_id is None else journal.read(path_of("interpretation", interpretation_id))
    if interp is None or interp["data_scope"] not in readable:
        return "interpretation_not_found"
    if interp["superseded_by"] is not None or interp["disposition"] != "adopted":
        return "interpretation_not_adopted"
    if interp["authority"]["kind"] == "none":
        return "adoption_is_not_authority"
    if request_lineage and not (set(interp["source_event_ids"]) & request_lineage):
        return "interpretation_not_of_this_request"
    if unreconciled(journal, readable, interp["source_event_ids"], interp["correction_event_ids"]):
        return "unreconciled_correction"
    return None


def wake_problem(svc, task, readable) -> str | None:
    """Called only inside the journal lock by source admission and destination validation."""
    parent = svc.journal.read(path_of("task", task["parent_ref"])) if task["parent_ref"] else task
    if parent is None or parent["data_scope"] not in readable:
        return "parent_unavailable"
    if any(t["state"] not in ("working", "waiting", "completed") for t in (task, parent)):
        return "task_not_active"
    if any(t["waiting"] and t["waiting"]["reason"] == "lead_unavailable" for t in (task, parent)):
        return "lead_unavailable"
    found = svc.charters.chartered(task.get("charter_ref"))
    if found is None or not task["artifact_sets"]:
        return "charter_or_result_unavailable"
    charter, kind = found
    current = task["artifact_sets"][-1]
    if (parent["lead"] not in charter.leads or task["data_scope"] not in charter.scopes
            or task["holder"] != charter.seat or set(current["authored_by"]) != {charter.seat}
            or kind["effects"] not in ("none", "internal")):
        return "charter_no_longer_applicable"
    request = svc.journal.read(path_of("request", task["request_ref"]))
    if request is None or request["data_scope"] not in readable:
        return "request_unavailable"
    return interpretation_problem(svc.journal, readable, task["interpretation_ref"], lineage(request))


def _result(command_id: str, outcome: str, **extra) -> dict:
    return dict({"schema": RESULT, "command_id": command_id, "outcome": outcome}, **extra)


class _Planner:
    """Plans one command under the journal lock. Raises Refused or Conflict; returns a Plan."""

    def __init__(self, svc: TaskService, command: dict, binding: Binding):
        self.svc, self.cmd, self.b = svc, command, binding
        self.p = command["payload"]
        self.now = svc._clock()
        self.links = {"source_event_id": None, "interpretation_id": None, "request_id": None, "task_id": None,
                      "authority_ref": None}

    # -- helpers --

    def load(self, target_type: str, target_id: str | None, *, refuse_as: str = "not_found") -> dict:
        """A record the caller may reference: missing and out-of-scope look the same (no existence leak)."""
        rec = None if target_id is None else self.svc.journal.read(path_of(target_type, target_id))
        if rec is None or rec.get("data_scope") not in self.b.read_scopes:
            raise Refused(refuse_as, target_type)
        return rec

    def target(self) -> dict | None:
        t = self.cmd["target"]
        return self.svc.journal.read(path_of(t["type"], t["id"]))

    def expect(self, current: dict | None) -> None:
        expected = self.cmd["expected_revision"]
        if current is None:
            if expected is not None:
                raise Conflict(None)
            return
        if current.get("data_scope") not in self.b.read_scopes:
            raise Refused("not_found", self.cmd["target"]["type"])
        if expected != current["revision"]:
            raise Conflict(current["revision"])
        if current["data_scope"] != self.cmd["data_scope"]:
            raise Refused("wrong_scope")

    def stamp(self, record: dict, previous: dict | None) -> dict:
        record = dict(record)
        record["schema"] = SCHEMAS[self.cmd["target"]["type"]] if "schema" not in record else record["schema"]
        record["revision"] = 1 if previous is None else previous["revision"] + 1
        record["environment"] = self.svc.environment
        record["data_scope"] = self.cmd["data_scope"] if previous is None else previous["data_scope"]
        record["created_at"] = self.now if previous is None else previous["created_at"]
        record["updated_at"] = self.now
        record["provenance"] = {"actor_seat": self.b.seat, "origin": self.b.origin,
                                "session_id": self.cmd["session_id"], "command_id": self.cmd["command_id"]}
        return record

    def checked(self, kind: str, record: dict) -> dict:
        problems = contracts.errors("records", kind, record)
        if problems:
            raise Refused("payload_invalid", problems[0][:200])
        return record

    def finish(self, writes: list[tuple[str, dict]], revision: int) -> Plan:
        for _, rec in writes:
            if rec["data_scope"] != self.cmd["data_scope"]:
                raise Refused("wrong_scope", "write_outside_command_scope")      # S1-b: one scope per command
        evidence = {"revisions": {p: r["revision"] for p, r in writes},"actor_seat": self.b.seat, "origin": self.b.origin, "session_id": self.cmd["session_id"],
                    "runtime": self.cmd["runtime"], "causal_parent": self.cmd["causal_parent"],
                    "basis_ref": self.cmd["basis_ref"], "environment": self.svc.environment,
                    "data_scope": self.cmd["data_scope"], "event_at": self.cmd["event_at"],
                    "targets": [p for p, _ in writes], "binding_id": self.b.binding_id, "links": self.links}
        return Plan([Write(p, r) for p, r in writes], revision, self.cmd["kind"], evidence)

    # -- dispatch --

    def plan(self) -> Plan:
        if self.cmd["target"]["type"] != self.cmd["kind"].split(".")[0] and self.cmd["kind"] != "correction.apply":
            raise Refused("payload_invalid", "target_type")
        return getattr(self, "k_" + self.cmd["kind"].replace(".", "_"))()

    # -- source events (C05) --

    def k_source_event_record(self) -> Plan:
        if self.target() is not None:
            raise Refused("id_taken")                                     # same answer whatever its scope
        self.expect(None)
        if self.p["correction_of"] is not None:
            self.same_scope(self.load("source_event", self.p["correction_of"]), "source_event")
        sid = self.cmd["target"]["id"]
        self.links["source_event_id"] = sid
        rec = self.stamp({"source_event_id": sid, "producer": self.b.seat, "source": self.p["source"],
                          "content": self.p["content"], "correction_of": self.p["correction_of"],
                          "event_at": self.cmd["event_at"]}, None)
        return self.finish([(path_of("source_event", sid), self.checked("source_event", rec))], 1)

    # -- interpretations (Q02) --

    def k_interpretation_record(self) -> Plan:
        if self.target() is not None:
            raise Refused("id_taken")
        self.expect(None)
        for sid in self.p["source_event_ids"] + self.p["correction_event_ids"]:
            self.same_scope(self.load("source_event", sid), "source_event")
        if self.unreconciled_corrections(self.p["source_event_ids"], self.p["correction_event_ids"]):
            raise Refused("fenced", "unreconciled_correction")
        iid = self.cmd["target"]["id"]
        writes: list = []
        if self.p["supersedes"] is not None:
            old = self.load("interpretation", self.p["supersedes"])
            if old["data_scope"] != self.cmd["data_scope"]:
                raise Refused("not_found", "interpretation")
            if old["superseded_by"] is not None:
                raise Refused("transition_not_allowed", "already_superseded")
            if not set(old["source_event_ids"]) & set(self.p["source_event_ids"]):
                raise Refused("payload_invalid", "supersede_needs_the_same_source")
            citing = self.tasks_citing(old["interpretation_id"])
            if not (self.b.is_owner or self.b.seat == old["interpreter"]["seat"]
                    or any(self.b.seat == self.effective_lead(t) for t in citing)):
                raise Refused("not_authorized", "may_not_supersede")      # N2
            # Tasks acting on the superseded reading are fenced in the same intent and wait for authority (S1-a).
            for task in citing:
                t2 = dict(task, fence_generation=task["fence_generation"] + 1)
                t2["reviews"] = self.reapply(t2, superseded={old["interpretation_id"]})
                if task["state"] in ACTIVE_TASK:
                    t2.update(state="waiting", waiting={"reason": "authorization", "who_can_unblock": self.effective_lead(task)},
                              reason="interpretation superseded")
                writes.append((path_of("task", task["task_id"]), self.checked("task", self.stamp(t2, task))))
                writes += self.fence_outbox(task["task_id"], "interpretation_superseded")
            old2 = self.stamp(dict(old, superseded_by=iid,
                                   disposition="corrected" if old["disposition"] in ("proposed", "adopted")
                                   else old["disposition"],
                                   disposition_by=old["disposition_by"] or self.b.seat), old)
            writes.append((path_of("interpretation", old["interpretation_id"]), self.checked("interpretation", old2)))
        self.links.update(interpretation_id=iid, source_event_id=self.p["source_event_ids"][0])
        rec = self.stamp({"interpretation_id": iid, "source_event_ids": self.p["source_event_ids"],
                          "correction_event_ids": self.p["correction_event_ids"],
                          "interpreter": {"seat": self.b.seat, "session_id": self.cmd["session_id"],
                                          "runtime": self.cmd["runtime"]},
                          "proposed": self.p["proposed"], "context_refs": self.p["context_refs"],
                          "disposition": "proposed", "disposition_by": None,
                          "authority": {"kind": "none", "ref": None}, "superseded_by": None}, None)
        writes.insert(0, (path_of("interpretation", iid), self.checked("interpretation", rec)))
        return self.finish(writes, 1)

    def k_interpretation_dispose(self) -> Plan:
        cur = self.target()
        if cur is None:
            raise Refused("not_found", "interpretation")
        self.expect(cur)
        if cur["superseded_by"] is not None:
            raise Refused("transition_not_allowed", "superseded")
        if cur["disposition"] != "proposed" and not (cur["disposition"] == "unresolved"):
            raise Refused("transition_not_allowed", "already_disposed")
        auth = self.p["authority"]
        if self.p["disposition"] == "adopted" and self.unreconciled_corrections(cur["source_event_ids"],
                                                                                cur["correction_event_ids"]):
            raise Refused("fenced", "unreconciled_correction")
        if self.p["disposition"] == "adopted" or auth["kind"] != "none":
            self.check_authority(auth, cur["proposed"]["scope"])
            if auth["kind"] == "owner_confirmation" and auth["ref"]["store"] == "task_service":
                self.check_owner_action(auth["ref"]["id"], cur)
        self.links.update(interpretation_id=cur["interpretation_id"], source_event_id=cur["source_event_ids"][0],
                          authority_ref=auth["ref"])
        rec = self.stamp(dict(cur, disposition=self.p["disposition"], disposition_by=self.b.seat, authority=auth), cur)
        return self.finish([(path_of("interpretation", cur["interpretation_id"]), self.checked("interpretation", rec))],
                           rec["revision"])

    def unreconciled_corrections(self, source_ids: list[str], cited: list[str]) -> list[str]:
        """C06: a late interpretation must reconcile corrections it can see; it cannot be recorded or adopted as if
        they never arrived."""
        return unreconciled(self.svc.journal, self.b.read_scopes, source_ids, cited)

    def tasks_citing(self, interpretation_id: str) -> list[dict]:
        folder = self.svc.journal.root / DIRS["task"]
        found = []
        for path in sorted(folder.glob("TK-*.json")) if folder.is_dir() else []:
            t = self.svc.journal.read(f"{DIRS['task']}/{path.name}")
            if t and t["interpretation_ref"] == interpretation_id and t["state"] not in TERMINAL_TASK:
                if t["data_scope"] != self.cmd["data_scope"]:
                    raise Refused("transition_not_allowed", "cited_across_scopes")
                found.append(t)
        return found

    def outbox_entries(self, task_id: str, operation: str = "admit_bundle") -> list[dict]:
        """This task's outbox entries. Every write is in the command's scope (finish), so entries share the task's."""
        folder = self.svc.journal.root / DIRS["outbox"]
        found = []
        for path in sorted(folder.glob("OB-*.json")) if folder.is_dir() else []:
            ob = self.svc.journal.read(f"{DIRS['outbox']}/{path.name}")
            if ob and ob["task_id"] == task_id and ob["operation"] == operation:
                found.append(ob)
        return found

    def fence_outbox(self, task_id: str, why: str) -> list[tuple[str, dict]]:
        """E03/C06. Called by every command that bumps a task's fence by one, in the same intent:
        - a pending (never sent) admission becomes fenced, so no drainer can send it afterwards;
        - when the task has ever had an admission, a ``set_fence`` operation for the new generation is queued for the
          execution authority. The authority refuses any admission below its own fence inside its own transaction,
          which is where amendment fencing and effect admission serialize. Until it acknowledges, the change is saved
          and pending reconciliation there, not "stopped".
        Attempted or unknown admissions are not touched: their effect may already exist and is reconciled by lookup."""
        task = self.svc.journal.read(path_of("task", task_id))
        new_fence = task["fence_generation"] + 1
        writes, entries = [], self.outbox_entries(task_id)
        for ob in entries:
            if ob["state"] == "pending":
                row = dict(self.attempt_row("fenced", why, "drainer"), attempt=len(ob["attempts"]) + 1)
                rec = self.stamp(dict(ob, state="fenced", reason=why, attempts=ob["attempts"] + [row]), ob)
                writes.append((path_of("outbox", ob["operation_id"]), self.checked("outbox", rec)))
        if entries:
            body = {"task_id": task_id, "fence_generation": new_fence}
            op = FENCE_OP_PREFIX + digest(body)[7:47]          # reserved namespace, fixed length (S2-A, S2-B)
            if self.svc.journal.read(path_of("outbox", op)) is not None:
                raise Refused("transition_not_allowed", "fence_operation_exists")
            ob = {"schema": SCHEMAS["outbox"], "operation_id": op, "task_id": task_id, "destination": "harness",
                  "operation": "set_fence", "bundle_digest": digest(body),
                  "bundle_ref": {"store": "task_service", "id": task_id, "edition": str(new_fence), "digest": None},
                  "fence_generation": new_fence, "interpretation_ref": task["interpretation_ref"], "state": "pending",
                  "attempts": [], "destination_receipt": None, "reason": why, "packet": None}
            writes.append((path_of("outbox", op), self.checked("outbox", self.stamp(ob, None))))
        return writes

    def notice(self, task: dict, episode: str, kind: str, payload_ref: dict, packet: dict) -> list[tuple[str, dict]]:
        """B2D/E08/E09: one durable notice per (task, episode), written in the same intent as the change that needs it
        and delivered by the outbox drainer to the notice channel. The id is derived from the episode, so a repeat of
        the same episode is deduplicated rather than notified again; no model is woken to send it."""
        body = {"task_id": task["task_id"], "episode": episode, "kind": kind}
        op = NOTICE_OP_PREFIX + digest(body)[7:47]
        if self.svc.journal.read(path_of("outbox", op)) is not None:
            return []
        ob = {"schema": SCHEMAS["outbox"], "operation_id": op, "task_id": task["task_id"], "destination": "notices",
              "operation": "notify", "bundle_digest": digest(packet), "packet": packet,
              "bundle_ref": payload_ref, "fence_generation": task["fence_generation"],
              "interpretation_ref": task["interpretation_ref"], "state": "pending", "attempts": [],
              "destination_receipt": None, "reason": kind}
        return [(path_of("outbox", op), self.checked("outbox", self.stamp(ob, None)))]

    def helper_wake(self, task: dict) -> list[tuple[str, dict]]:
        """Atomic intent only. No prompt/authority is supplied by the helper; the
        destination must validate these references before a service-origin continuation.
        """
        if not task.get("charter_ref") or self.effective_lead(task) not in ("mc", "cos"):
            return []
        current = task["artifact_sets"][-1]
        request = self.load("request", task["request_ref"])
        source = self.load("source_event", request["source_event_id"])
        packet = {"kind": "helper_result_ready", "task_id": task["task_id"],
                  "lead": self.effective_lead(task), "source_ref": source["source"],
                  "source_event_id": source["source_event_id"], "result_digest": current["digest"],
                  "charter_ref": task["charter_ref"], "fence_generation": task["fence_generation"],
                  "environment": self.cmd["environment"], "data_scope": self.cmd["data_scope"]}
        op = WAKE_OP_PREFIX + digest(packet)[7:47]
        if self.svc.journal.read(path_of("outbox", op)) is not None:
            return []
        ob = {"schema": SCHEMAS["outbox"], "operation_id": op, "task_id": task["task_id"],
              "destination": "lead_wake", "operation": "wake_lead", "bundle_digest": digest(packet),
              "packet": packet, "bundle_ref": {"store": "artifacts", "id": task["task_id"],
              "edition": str(current["task_revision"]), "digest": current["digest"]},
              "fence_generation": task["fence_generation"], "interpretation_ref": task["interpretation_ref"],
              "state": "pending", "attempts": [], "destination_receipt": None, "reason": "helper_result_ready"}
        return [(path_of("outbox", op), self.checked("outbox", self.stamp(ob, None)))]

    def attempt_row(self, observation, code, method, send_attempt=None) -> dict:
        return {"attempt": 0, "observation": observation, "drainer_seat": self.b.seat, "at": self.now, "code": code,
                "method": method, "send_attempt": send_attempt}

    def same_scope(self, rec: dict, what: str) -> None:
        """N3/S2-1: a request, its source events and its interpretation live in one scope."""
        if rec["data_scope"] != self.cmd["data_scope"]:
            raise Refused("not_found", what)

    def source_owner(self, sid: str) -> str | None:
        """S2-1: the request whose intent lineage already contains this source event, if any (same scope only)."""
        folder = self.svc.journal.root / DIRS["request"]
        for path in sorted(folder.glob("RQ-*.json")) if folder.is_dir() else []:
            r = self.svc.journal.read(f"{DIRS['request']}/{path.name}")
            if r and r["data_scope"] == self.cmd["data_scope"] and (sid in lineage(r)
                                                                   or sid in r.get("linked_sources", ())):
                return r["request_id"]
        return None

    def effective_lead(self, task: dict) -> str:
        """E07/S1-2: a child's accountable lead is its parent's lead as it is now; the child's own lead field is the
        value copied at delegation and is never used for authority."""
        if task["parent_ref"] is None:
            return task["lead"]
        return self.load("task", task["parent_ref"])["lead"]

    def lead_blocked(self, task: dict) -> bool:
        """F05: the task or its parent is waiting because its lead is unavailable."""
        chain = [task] + ([self.load("task", task["parent_ref"])] if task["parent_ref"] else [])
        return any(t["waiting"] and t["waiting"]["reason"] == "lead_unavailable" for t in chain)

    def reapply(self, task: dict, superseded: set | None = None) -> list[dict]:
        """E06: a review applies to the current set only, only under the interpretation it was made under, and never
        once that interpretation has been superseded (even before the task is rebased)."""
        current = task["artifact_sets"][-1]["digest"] if task["artifact_sets"] else None
        gone = set(superseded or ())
        for iid in {r["interpretation_ref"] for r in task["reviews"]} - gone:
            rec = self.svc.journal.read(path_of("interpretation", iid))
            if rec is not None and rec["superseded_by"] is not None:
                gone.add(iid)
        return [dict(r, applicable_to_current=(r["reviewed_set_digest"] == current
                                               and r["interpretation_ref"] == task["interpretation_ref"]
                                               and r["interpretation_ref"] not in gone))
                for r in task["reviews"]]

    @staticmethod
    def authors_of(task: dict, set_digest: str) -> set:
        return {seat for s_ in task["artifact_sets"] if s_["digest"] == set_digest for seat in s_["authored_by"]}

    def carried_reviews(self, task: dict, set_digest: str) -> tuple[list[dict], set]:
        """Blue Lantern, 5 Nov 2026 (option A): a parent's set that is exactly a completed child's current accepted set
        carries the reviews that acceptance cited (its approvals and the dissent it delivered) and the child's authors.
        Reviews are per task, so without this the parent could never be accepted. The carried review keeps the
        original reviewer, verdict, review_ref and interpretation; its id names the child and the original review."""
        out, authors, seen = [], set(), set()
        for child in self.children(task["task_id"]):
            if child["state"] != "completed" or child.get("charter_ref"):   # a helper's acceptance never reviews build work
                continue
            acc = [a for a in child["acceptances"] if a["status"] == "current" and a["artifact_set_digest"] == set_digest]
            if not acc:
                continue
            a = acc[-1]
            authors |= self.authors_of(child, set_digest)
            for r in child["reviews"]:
                if r["reviewed_set_digest"] != set_digest:
                    continue
                if r["review_ref"] not in a["review_refs"] and r["review_id"] not in a["disagreements_delivered"]:
                    continue
                key = json.dumps(r["review_ref"], sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"review_id": "RV-carry-" + digest({"child": child["task_id"],
                                                               "review": r["review_id"]})[7:47],
                            "reviewer": r["reviewer"], "reviewed_set_digest": set_digest,
                            "input_digest": r["input_digest"], "verdict": r["verdict"], "review_ref": r["review_ref"],
                            "findings_ref": r["findings_ref"], "interpretation_ref": r["interpretation_ref"],
                            "applicable_to_current": False, "task_revision": task["revision"] + 1,
                            **({"no_verdict": True} if r.get("no_verdict") else {})})
        return out, authors

    def delivered_review(self, task: dict, current: dict) -> bool:
        """A review child's set is the review it delivered: exactly one artifact, equal to a review its holder recorded
        on a sibling (same parent). Accepting it needs no further approval: a review is never reviewed again."""
        if task["parent_ref"] is None or len(current["artifacts"]) != 1:
            return False
        art = current["artifacts"][0]
        for sib in self.children(task["parent_ref"]):
            if sib["task_id"] == task["task_id"]:
                continue
            for r in sib["reviews"]:
                ref = r["review_ref"]
                if r["reviewer"] == task["holder"] and (ref["store"], ref["id"], ref["digest"]) == (
                        art["store"], art["id"], art["digest"]):
                    return True
        return False

    def cascade(self, parent: dict, why: str) -> list[tuple[str, dict]]:
        """Children of a parent that ends are canceled in the same intent, their pending sends fenced. Their effects
        already admitted stay actual or unknown and are reported (open_effects), never undone here."""
        out = []
        for child in self.children(parent["task_id"]):
            if child["state"] in ACTIVE_TASK:
                out += self.fence_outbox(child["task_id"], why.replace(" ", "_"))
                c2 = self.stamp(dict(child, state="canceled", waiting=None, reason=why,
                                     fence_generation=child["fence_generation"] + 1), child)
                out.append((path_of("task", child["task_id"]), self.checked("task", c2)))
        return out

    def children(self, task_id: str) -> list[dict]:
        folder = self.svc.journal.root / DIRS["task"]
        out = []
        for path in sorted(folder.glob("TK-*.json")) if folder.is_dir() else []:
            t = self.svc.journal.read(f"{DIRS['task']}/{path.name}")
            if t and t["parent_ref"] == task_id:
                out.append(t)
        return out

    def open_effects(self, task_id: str, states=("attempted", "unknown", "conflict")) -> list[str]:
        # A notice tells someone; it is not an effect to reconcile, so one that cannot be sent yet never holds work back.
        return [ob["operation_id"] for ob in self.outbox_entries(task_id)
                if ob["state"] in states and ob["operation"] not in ("notify", "wake_lead", "publish_return")]

    def actionable(self, interpretation_id, request) -> None:
        problem = interpretation_problem(self.svc.journal, self.b.read_scopes, interpretation_id,
                                         lineage(request) if request else set())
        if problem:
            raise Refused("fenced" if problem == "unreconciled_correction" else "not_authorized", problem)

    def check_owner_action(self, source_id: str, interp: dict) -> None:
        """B4 design v0.3 R-S1: an owner confirmation recorded in this store counts only for the proposal it names,
        exactly as Robert saw it, and only once. The proposal digest is computed here, never taken from the event."""
        ev = self.svc.journal.read(path_of("source_event", source_id))
        if (ev is None or ev["data_scope"] != interp["data_scope"] or ev["source"]["store"] != OWNER_ACTION_STORE
                or ev.get("provenance", {}).get("origin") != "owner_session"):
            raise Refused("not_authorized", "confirmation_not_for_this_proposal")
        try:
            action = json.loads(ev["content"])
        except ValueError:
            action = None
        want = {"action": "confirm", "interpretation_id": interp["interpretation_id"],
                "proposal_digest": digest(interp["proposed"])}
        if action != want:
            raise Refused("not_authorized", "confirmation_not_for_this_proposal")
        folder = self.svc.journal.root / DIRS["interpretation"]
        for path in sorted(folder.glob("IN-*.json")) if folder.is_dir() else []:
            other = self.svc.journal.read(f"{DIRS['interpretation']}/{path.name}")
            ref = (other or {}).get("authority", {}).get("ref") or {}
            if other and other["interpretation_id"] != interp["interpretation_id"] and ref.get("id") == source_id:
                raise Refused("not_authorized", "confirmation_already_used")

    def check_authority(self, auth: dict, scope: str) -> None:
        """Q02: adoption is recorded by whoever adopts; authority must be real. An owner confirmation is accepted only
        from the owner's own session binding (the principal confirming), never asserted by an agent; an existing
        grant must cover the adopting seat and the interpretation's scope now."""
        if auth["kind"] != "none" and self.b.origin in ("platform", "peer"):
            raise Refused("not_authorized", "platform_or_peer_cannot_carry_authority")
        if auth["kind"] == "owner_confirmation":
            if not self.b.is_owner:
                raise Refused("not_authorized", "owner_confirmation_needs_owner_session")
        elif auth["kind"] == "existing_grant":
            if str(auth["ref"]["id"]).startswith("charter:"):
                if not self.svc.charters.covers(auth["ref"]["id"], self.b.seat, scope):
                    raise Refused("not_authorized", "charter_does_not_cover")
            elif not self.svc.grants.covers(auth["ref"]["id"], self.b.seat, scope, self.now):
                raise Refused("not_authorized", "grant_does_not_cover")

    # -- requests (D02, D03) --

    def k_request_record(self) -> Plan:
        if self.target() is not None:
            raise Refused("id_taken")
        self.expect(None)
        se = self.load("source_event", self.p["source_event_id"])
        self.same_scope(se, "source_event")
        if self.source_owner(se["source_event_id"]) is not None:
            raise Refused("transition_not_allowed", "source_already_has_a_request")   # D02, S2-1
        if self.p["interpretation_ref"] is not None:
            interp = self.load("interpretation", self.p["interpretation_ref"])
            self.same_scope(interp, "interpretation")
            if self.p["source_event_id"] not in interp["source_event_ids"]:
                raise Refused("payload_invalid", "interpretation_not_of_this_source")
        rid = self.cmd["target"]["id"]
        self.links.update(request_id=rid, source_event_id=se["source_event_id"],
                          interpretation_id=self.p["interpretation_ref"])
        rec = self.stamp({"request_id": rid, "source_event_id": se["source_event_id"],
                          "intent": {"text": self.p["intent_text"], "revision": 1,
                                     "source_event_id": se["source_event_id"]},
                          "interpretation_ref": self.p["interpretation_ref"], "responder": self.p["responder"],
                          "state": "recorded", "reason": None, "coverage": {"sources": [], "gaps": []},
                          "result_refs": [], "refinements": [], "task_ref": None, "pending_operation": None,
                          "fence_generation": 0}, None)
        return self.finish([(path_of("request", rid), self.checked("request", rec))], 1)

    def request_for_change(self) -> dict:
        cur = self.target()
        if cur is None:
            raise Refused("not_found", "request")
        self.expect(cur)
        self.links.update(request_id=cur["request_id"], source_event_id=cur["source_event_id"],
                          interpretation_id=cur["interpretation_ref"], task_id=cur["task_ref"])
        return cur

    def may_act_for_request(self, req: dict) -> None:
        if not (self.b.is_owner or self.b.seat == req["responder"]):
            raise Refused("not_authorized", "not_responder")

    def k_request_refine(self) -> Plan:
        req = self.request_for_change()
        self.may_act_for_request(req)
        if req["state"] in ("failed", "withdrawn", "promoted"):
            # A promoted request changes through correction.apply, which fences its task (S2-1).
            raise Refused("transition_not_allowed", req["state"])
        se = self.load("source_event", self.p["source_event_id"])
        self.same_scope(se, "source_event")
        owner = self.source_owner(se["source_event_id"])
        if owner not in (None, req["request_id"]):
            raise Refused("transition_not_allowed", "source_belongs_to_another_request")   # S2-1
        if self.p["interpretation_ref"] is not None:
            interp = self.load("interpretation", self.p["interpretation_ref"])
            self.same_scope(interp, "interpretation")
            if not set(interp["source_event_ids"]) & (lineage(req) | {se["source_event_id"]}):
                raise Refused("payload_invalid", "interpretation_not_of_this_request")
        new = dict(copy.deepcopy(req), intent={"text": self.p["intent_text"], "revision": req["intent"]["revision"] + 1,
                                               "source_event_id": se["source_event_id"]},
                   refinements=req["refinements"] + [_edition(req)], result_refs=[], coverage={"sources": [], "gaps": []},
                   interpretation_ref=self.p["interpretation_ref"] or req["interpretation_ref"],
                   fence_generation=req["fence_generation"] + 1,
                   state="active" if req["state"] in ("answered", "recorded", "active") else req["state"])
        self.links["source_event_id"] = se["source_event_id"]
        rec = self.stamp(new, req)
        return self.finish([(path_of("request", req["request_id"]), self.checked("request", rec))], rec["revision"])

    def k_request_answer(self) -> Plan:
        req = self.request_for_change()
        if self.b.seat != req["responder"] and not self.b.is_owner:      # the principal may record a result
            raise Refused("not_authorized", "not_responder")
        if req["state"] != "active":
            raise Refused("transition_not_allowed", req["state"])
        if self.p["fence_generation"] != req["fence_generation"]:
            raise Refused("fenced", "answer_for_an_older_edition")
        rec = self.stamp(dict(req, state="answered", result_refs=self.p["result_refs"], coverage=self.p["coverage"]), req)
        return self.finish([(path_of("request", req["request_id"]), self.checked("request", rec))], rec["revision"])

    def k_request_set_state(self) -> Plan:
        req = self.request_for_change()
        self.may_act_for_request(req)
        to = self.p["state"]
        allowed = {"active": ("recorded",), "failed": ("active",), "withdrawn": ("recorded", "active")}
        if req["state"] not in allowed[to]:
            raise Refused("transition_not_allowed", f"{req['state']}->{to}")
        if to in ("failed", "withdrawn") and not self.p["reason"]:
            raise Refused("payload_invalid", "reason_required")
        fence = req["fence_generation"] + (1 if to == "withdrawn" else 0)
        rec = self.stamp(dict(req, state=to, reason=self.p["reason"], fence_generation=fence), req)
        return self.finish([(path_of("request", req["request_id"]), self.checked("request", rec))], rec["revision"])

    def k_request_promote(self) -> Plan:
        """E01/D02: one accepted intent, at most one promotion. Request and task are written under one journal intent
        and finished forward after any crash, so the reserved task id is the only task this promotion can create."""
        req = self.request_for_change()
        self.may_act_for_request(req)
        if req["state"] not in ("recorded", "active"):
            raise Refused("transition_not_allowed", req["state"])
        if req["task_ref"] is not None:
            raise Refused("transition_not_allowed", "already_promoted")
        self.actionable(req["interpretation_ref"], req)
        interp = self.load("interpretation", req["interpretation_ref"])
        self.same_scope(interp, "interpretation")
        terms = interp["proposed"].get("task")
        if terms is None and interp["authority"]["kind"] == "owner_confirmation":
            raise Refused("payload_invalid", "promote_needs_proposed_task")     # B4 design v0.3 R-S2
        if terms is not None and any(self.p[k] != terms[k] for k in ("lead", "holder", "outcome", "done_when",
                                                                       "return_window")):
            raise Refused("payload_invalid", "promote_not_as_confirmed")         # B4 design v0.2 §3
        charter_ref = self.p.get("charter_ref")
        if str((interp["authority"].get("ref") or {}).get("id") or "").startswith("charter:"):
            self.check_chartered(interp, charter_ref)
        elif charter_ref is not None:
            raise Refused("not_authorized", "charter_ref_needs_the_charter_grant")
        tid = self.p["task_id"]
        if self.svc.journal.read(path_of("task", tid)) is not None:
            raise Refused("id_taken", "task")
        if self.p["parent_ref"] is not None:
            raise Refused("transition_not_allowed", "children_come_from_task_delegate")    # S2-1
        self.links.update(task_id=tid, authority_ref=interp["authority"]["ref"])
        req2 = self.stamp(dict(req, state="promoted", task_ref=tid), req)
        assignments = [{"role": role, "seat": self.p[role], "from_revision": 1, "to_revision": None,
                        "command_id": self.cmd["command_id"], "reason": None, "effective_from_at": self.now,
                        "effective_to_at": None} for role in ("lead", "holder")]
        task = {"schema": SCHEMAS["task"], "task_id": tid, "request_ref": req["request_id"],
                "interpretation_ref": interp["interpretation_id"], "lead": self.p["lead"], "holder": self.p["holder"],
                "outcome": self.p["outcome"], "done_when": self.p["done_when"], "return_window": self.p["return_window"],
                "state": "submitted", "reason": None, "waiting": None, "parent_ref": self.p["parent_ref"],
                "execution_refs": [], "review_refs": [], "acceptances": [], "assignments": assignments,
                "artifact_sets": [], "reviews": [], "rounds": 0,
                "fence_generation": 0, "pending_operation": None, **({"charter_ref": charter_ref} if charter_ref else {})}
        task = self.stamp(task, None)
        return self.finish([(path_of("task", tid), self.checked("task", task)),
                            (path_of("request", req["request_id"]), self.checked("request", req2))], req2["revision"])

    OWNER_NOTE_STORES = ("mc-floor", "cos-history")

    def check_chartered(self, interp: dict, ref: dict | None) -> None:
        """Helper seats spec v0.4 §3.1: the grant is bound to the charter's digest, type, scope and holder, and it starts only
        from Robert's own kept note, so a lead can't promote its own words."""
        found = self.svc.charters.chartered(ref)
        if found is None:
            raise Refused("not_authorized", "not_chartered")
        charter, kind = found
        if interp["authority"]["ref"]["id"] != charter.grant_id:
            raise Refused("not_authorized", "not_this_charter")
        if self.p["holder"] != charter.seat or self.p["lead"] not in charter.leads or self.cmd["data_scope"] not in charter.scopes:
            raise Refused("not_authorized", "outside_the_charter")
        if self.p["lead"] != self.b.seat and not self.b.is_owner:
            raise Refused("not_authorized", "only_its_lead_starts_it")
        for sid in interp["source_event_ids"]:
            source = self.load("source_event", sid)
            if source["producer"] != "platform":
                raise Refused("not_authorized", "not_the_owners_kept_note")
            store = source["source"]["store"]
            if store in self.OWNER_NOTE_STORES:
                continue
            # A scheduled start (FF-19): only for a type its approved charter marks as scheduled, from the platform's schedule
            # record for that very type. The lead still cannot promote its own words: it holds no platform token.
            if store == SCHEDULE_STORE and kind.get("schedule") and str(source["source"]["id"]).startswith(ref["type_id"] + ":"):
                continue
            raise Refused("not_authorized", "not_the_owners_kept_note")

    def k_request_link_source(self) -> Plan:
        """B4 design v0.3 R-M2: the owner attaches a source event (a meeting message) to a request, explicitly. The
        intent does not change and nothing is fenced. D02: a source belongs to at most one request."""
        if not self.b.is_owner:
            raise Refused("not_authorized", "only_the_owner_links_sources")
        req = self.request_for_change()
        se = self.load("source_event", self.p["source_event_id"])
        self.same_scope(se, "source_event")
        owner = self.source_owner(se["source_event_id"])
        if owner == req["request_id"]:
            raise Refused("transition_not_allowed", "already_linked")
        if owner is not None:
            raise Refused("transition_not_allowed", "source_belongs_to_another_request")
        self.links["source_event_id"] = se["source_event_id"]
        rec = self.stamp(dict(req, linked_sources=list(req.get("linked_sources", [])) + [se["source_event_id"]]), req)
        return self.finish([(path_of("request", req["request_id"]), self.checked("request", rec))], rec["revision"])

    # -- tasks (D01, D04, E07) --

    def task_for_change(self) -> dict:
        cur = self.target()
        if cur is None:
            raise Refused("not_found", "task")
        self.expect(cur)
        interp = self.svc.journal.read(path_of("interpretation", cur["interpretation_ref"]))
        self.links.update(task_id=cur["task_id"], request_id=cur["request_ref"],
                          interpretation_id=cur["interpretation_ref"],
                          authority_ref=interp["authority"]["ref"] if interp else None)
        return cur

    def k_task_transition(self) -> Plan:
        task = self.task_for_change()
        to, frm, seat = self.p["to"], task["state"], self.b.seat
        if self.p["fence_generation"] != task["fence_generation"]:
            raise Refused("fenced", "stale_generation")
        is_lead, is_holder = seat == self.effective_lead(task), seat == task["holder"]
        rules = {
            "working": (("submitted", "waiting"), is_lead or is_holder or (self.b.is_owner and frm == "waiting"), True),
            "waiting": (("submitted", "working"), is_lead or is_holder or self.b.is_owner, False),
            "rejected": (("submitted",), is_lead or self.b.is_owner, False),
            "failed": (ACTIVE_TASK, is_lead or is_holder, True),
            "canceled": (ACTIVE_TASK, is_lead or self.b.is_owner, True),
        }
        sources, permitted, needs_evidence = rules[to]
        if frm not in sources:
            raise Refused("transition_not_allowed", f"{frm}->{to}")
        if not permitted:
            raise Refused("not_authorized", "seat_may_not_" + to)
        if needs_evidence and not self.p["evidence_refs"]:
            raise Refused("payload_invalid", "evidence_required")
        if (to == "waiting") != (self.p["waiting"] is not None):
            raise Refused("payload_invalid", "waiting_detail")
        if to in ("failed", "canceled", "rejected") and not self.p["reason"]:
            raise Refused("payload_invalid", "reason_required")
        if to in ("working", "waiting") and self.lead_blocked(task) and not self.b.is_owner and not (
                is_holder and not is_lead):
            # S3: during the episode the absent lead does not steer work; a holder may still report its own state.
            raise Refused("fenced", "lead_unavailable")
        if to == "failed" and self.lead_blocked(task) and not self.b.is_owner and (
                is_lead or not is_holder or any(c["state"] in ACTIVE_TASK for c in self.children(task["task_id"]))):
            # S2-B: during the episode "failed" is only a non-lead holder's report about its own attempt, and never
            # one that would cascade to children (that is the principal's decision).
            raise Refused("fenced", "lead_unavailable")
        if to in ("canceled", "rejected") and self.lead_blocked(task) and not self.b.is_owner:
            # During a lead_unavailable episode only the principal decides the outcome; a holder may still report
            # the failure of its own work ("failed"), which is a fact about the attempt, not a decision.
            raise Refused("fenced", "lead_unavailable")
        if to == "working" and frm == "waiting" and task["waiting"]["reason"] == "lead_unavailable" \
                and not self.b.is_owner:
            # F05/S1-1: only the principal (or a reassignment, which turns this into an ordinary wait) unblocks it.
            raise Refused("not_authorized", "lead_unavailable_needs_the_principal")
        if to == "working":
            # "waiting -> working only after the condition and authority are rechecked" (section 3; S1-a).
            self.actionable(task["interpretation_ref"], self.svc.journal.read(path_of("request", task["request_ref"])))
        new = dict(task, state=to, waiting=self.p["waiting"], reason=self.p["reason"],
                   execution_refs=task["execution_refs"] + [r for r in self.p["evidence_refs"]
                                                            if r not in task["execution_refs"]])
        extra = []
        if to in ("canceled", "failed", "rejected"):
            new["fence_generation"] = task["fence_generation"] + 1          # stale drainers cannot resurrect it
            extra = self.fence_outbox(task["task_id"], "task_" + to)
        if to in ("canceled", "failed", "rejected"):
            extra += self.cascade(task, f"parent {to}")                    # J3/S2-2: delegated steps stop too
        rec = self.stamp(new, task)
        if to == "failed" and task["parent_ref"] is not None:
            # A failed child tells the owner, once per failure: same intent as the change, id derived from this
            # revision. A child canceled because its parent ended is not a failure and raises nothing.
            extra += self.notice(rec, f"child_failed@{rec['revision']}", "child_failed",
                                 {"store": "task_service", "id": task["task_id"], "edition": str(rec["revision"]),
                                  "digest": None},
                                 {"kind": "child_failed", "task_id": task["task_id"], "parent_task_id": task["parent_ref"],
                                  "holder": task["holder"], "outcome": task["outcome"], "reason": self.p["reason"],
                                  "evidence": self.p["evidence_refs"]})
        return self.finish([(path_of("task", task["task_id"]), self.checked("task", rec))] + extra, rec["revision"])

    def k_task_assign(self) -> Plan:
        task = self.task_for_change()
        if task["state"] in TERMINAL_TASK:
            raise Refused("transition_not_allowed", task["state"])
        role, seat = self.p["role"], self.b.seat
        lead = self.effective_lead(task)
        if self.lead_blocked(task) and not self.b.is_owner:
            raise Refused("fenced", "lead_unavailable")      # S1-A: only the principal acts during the episode
        if role == "lead" and task["parent_ref"] is not None:
            raise Refused("transition_not_allowed", "child_lead_follows_parent")    # S1-2
        allowed = {"lead": seat == lead or self.b.is_owner, "holder": seat == lead or self.b.is_owner}
        if not allowed.get(role, seat == lead or self.b.is_owner):
            raise Refused("not_authorized", f"may_not_assign_{role}")
        revision = task["revision"] + 1
        assignments = copy.deepcopy(task["assignments"])
        if role in SINGLE_HOLDER_ROLES:
            # One seat at a time owes these; a handover closes the previous interval (Q03, F14). Delegates may be
            # several at once.
            if any(a["role"] == role and a["seat"] == self.p["seat"] and a["to_revision"] is None for a in assignments):
                raise Refused("transition_not_allowed", "already_assigned")
            for a in assignments:
                if a["role"] == role and a["to_revision"] is None:
                    a["to_revision"], a["effective_to_at"] = revision, self.now
        assignments.append({"role": role, "seat": self.p["seat"], "from_revision": revision, "to_revision": None,
                            "command_id": self.cmd["command_id"], "reason": self.p["reason"],
                            "effective_from_at": self.now, "effective_to_at": None})
        new = dict(task, assignments=assignments)
        if role in ("lead", "holder"):
            new[role] = self.p["seat"]
            new["fence_generation"] = task["fence_generation"] + 1      # E07: reassignment fences stale execution
        if role == "lead" and task["waiting"] and task["waiting"]["reason"] == "lead_unavailable":
            # F05: the principal's reassignment ends the blocked episode; the new lead resumes it.
            new["waiting"] = {"reason": "input", "who_can_unblock": self.p["seat"]}
            new["reason"] = "lead reassigned after a missed checkpoint"
        extra = self.fence_outbox(task["task_id"], f"{role}_reassigned") if role in ("lead", "holder") else []
        if role == "lead":
            for child in self.children(task["task_id"]):                 # S3: children's stale execution fenced too
                if child["state"] in ACTIVE_TASK:
                    extra += self.fence_outbox(child["task_id"], "parent_lead_reassigned")
                    c2 = self.stamp(dict(child, fence_generation=child["fence_generation"] + 1), child)
                    extra.append((path_of("task", child["task_id"]), self.checked("task", c2)))
        rec = self.stamp(new, task)
        return self.finish([(path_of("task", task["task_id"]), self.checked("task", rec))] + extra, rec["revision"])

    def k_task_accept(self) -> Plan:
        """E06/E07: only the current lead (or the owner's override) accepts, and only an exact artifact set that the
        cited reviews reviewed. A worker's report never reaches here."""
        return self._accept_task(self.task_for_change())

    def _accept_task(self, task) -> Plan:
        lead = self.effective_lead(task)                         # E07: a child's lead is the parent's, now
        if not (self.b.seat == lead or self.b.is_owner):
            raise Refused("not_authorized", "only_the_lead_accepts")
        if task["parent_ref"] is not None:
            parent = self.load("task", task["parent_ref"])
            if parent["state"] in TERMINAL_TASK:
                raise Refused("transition_not_allowed", "parent_terminal")
        if self.lead_blocked(task) and not self.b.is_owner:
            raise Refused("fenced", "lead_unavailable")                  # S2-4
        if task["state"] not in ("working", "waiting"):
            raise Refused("transition_not_allowed", task["state"])
        if self.p["fence_generation"] != task["fence_generation"]:
            raise Refused("fenced", "stale_generation")
        if task["waiting"] and task["waiting"]["reason"] == "authorization":
            raise Refused("fenced", "waiting_for_authorization")
        self.actionable(task["interpretation_ref"], self.svc.journal.read(path_of("request", task["request_ref"])))
        if self.open_effects(task["task_id"], ("pending", "attempted", "unknown", "conflict")):
            raise Refused("fenced", "effects_unreconciled")    # Q05: settle every admission first (S2-2)
        kids = self.children(task["task_id"])
        if [c for c in kids if c["state"] not in TERMINAL_TASK]:
            raise Refused("transition_not_allowed", "children_open")      # J3: a parent is never auto-closed
        if any(self.open_effects(c["task_id"]) for c in kids):
            raise Refused("fenced", "child_effects_unreconciled")          # S2-3
        current = task["artifact_sets"][-1] if task["artifact_sets"] else None
        if current is None:
            raise Refused("transition_not_allowed", "no_submitted_artifacts")
        if self.p["artifact_set_digest"] != current["digest"]:
            raise Refused("transition_not_allowed", "not_the_current_artifact_set")      # F04: a newer set arrived
        applicable = [r for r in task["reviews"] if r["applicable_to_current"]]
        authors = self.authors_of(task, current["digest"])
        approvals = [r for r in applicable if r["verdict"] == "approve" and not r.get("no_verdict") and r["reviewer"] not in authors
                     and r["reviewer"] != self.b.seat]           # the accepting lead's own approval never counts
        if not approvals and not self.delivered_review(task, current) and not self.lead_may_accept_helper(task, current, applicable):
            raise Refused("transition_not_allowed", "no_non_author_approval_of_this_set")
        dissent = {r["review_id"] for r in applicable if r["verdict"] != "approve"}
        latest = {}
        for r in task["reviews"]:
            latest[r["reviewer"]] = r
        for r in latest.values():                               # S2-1: an objection outlives a resubmission
            if r["verdict"] != "approve" and not r["applicable_to_current"]:
                dissent.add(r["review_id"])
        dissent = sorted(dissent)
        if sorted(set(self.p["acknowledged_disagreements"])) != dissent:
            raise Refused("transition_not_allowed", "disagreement_not_delivered")       # E06: never hidden
        acceptances = [dict(a, status="superseded") for a in task["acceptances"]]
        acceptances.append({"artifact_set_digest": current["digest"],
                            "reviewed_set_digests": sorted({r["reviewed_set_digest"] for r in applicable}),
                            "review_refs": [r["review_ref"] for r in applicable], "by": self.b.seat,
                            "task_revision": task["revision"] + 1, "status": "current",
                            "disagreements_delivered": dissent})
        rec = self.stamp(dict(task, state="completed", waiting=None, acceptances=acceptances), task)
        if task["parent_ref"] is None and not task.get("charter_ref"):       # the lead presents a helper's result itself
            # J2: the return packet reaches the principal with the browser closed; disagreement travels with it.
            note = self.notice(task, f"return@{rec['revision']}", "return_packet",      # B2D S2-5: per acceptance
                               {"store": "task_service", "id": task["task_id"], "edition": str(rec["revision"]),
                                "digest": current["digest"]},
                               {"kind": "return_packet", "task_id": task["task_id"], "outcome": task["outcome"],
                                "accepted_set": current["digest"], "artifacts": current["artifacts"],
                                "accepted_by": self.b.seat,
                                "disagreements_delivered": [
                                    {"review_id": r["review_id"], "reviewer": r["reviewer"], "verdict": r["verdict"],
                                     "review_ref": r["review_ref"], "findings_ref": r["findings_ref"],
                                     **({"no_verdict": True} if r.get("no_verdict") else {})}
                                    for r in task["reviews"] if r["review_id"] in dissent],
                                "children": [{"task_id": c["task_id"], "state": c["state"]} for c in kids]})
            return self.finish([(path_of("task", task["task_id"]), self.checked("task", rec))] + note,
                               rec["revision"])
        return self.finish([(path_of("task", task["task_id"]), self.checked("task", rec))], rec["revision"])

    def lead_may_accept_helper(self, task: dict, current: dict, applicable: list) -> bool:
        """Helper seats spec v0.4 §3.1 row 1: a lead's own review accepts only chartered work with effects none or internal, by
        that charter's seat, authored by it alone, approved by this lead on this exact set."""
        found = self.svc.charters.chartered(task.get("charter_ref"))
        if found is None:
            return False
        charter, kind = found
        return (kind["effects"] in ("none", "internal") and task["holder"] == charter.seat and set(current["authored_by"]) == {charter.seat}
                and any(r["verdict"] == "approve" and not r.get("no_verdict") and r["reviewer"] == self.b.seat for r in applicable))

    def k_task_submit_artifacts(self) -> Plan:
        """E06/E07: the holder (or the lead) submits a new current artifact set. Submitted, not accepted. Every review
        of another set stops applying to the current one; nothing is erased."""
        task = self.task_for_change()
        if not (self.b.seat in (task["holder"], self.effective_lead(task)) or self.b.is_owner):
            raise Refused("not_authorized", "only_the_holder_or_lead_submits")
        if self.lead_blocked(task) and self.b.seat != task["holder"] and not self.b.is_owner:
            raise Refused("fenced", "lead_unavailable")
        if task["state"] not in ("working", "waiting"):
            raise Refused("transition_not_allowed", task["state"])
        if self.p["fence_generation"] != task["fence_generation"]:
            raise Refused("fenced", "stale_generation")
        found = self.svc.charters.chartered(task.get("charter_ref"))
        if found is not None and len(task["artifact_sets"]) > found[1]["max_revisions"]:
            raise Refused("transition_not_allowed", "revision_limit")
        keys = [(a["store"], a["id"], a.get("edition") or "") for a in self.p["artifacts"]]
        if len(set(keys)) != len(keys):
            raise Refused("payload_invalid", "duplicate_artifact")
        arts = sorted(self.p["artifacts"], key=lambda a: (a["store"], a["id"], a.get("edition") or "", a["digest"]))
        set_digest = digest(arts)
        carried, child_authors = self.carried_reviews(task, set_digest)
        new = [r for r in carried if r["review_ref"] not in task["review_refs"]]
        if task["artifact_sets"] and task["artifact_sets"][-1]["digest"] == set_digest:
            last = task["artifact_sets"][-1]
            if not new and child_authors <= set(last["authored_by"]):
                raise Refused("transition_not_allowed", "already_current")
            # The same set again only to carry a completed child's reviews it lacks (a set submitted before carrying
            # existed): authors only grow, nothing is replaced.
            sets = task["artifact_sets"][:-1] + [dict(last, authored_by=sorted(set(last["authored_by"]) | child_authors))]
        else:
            sets = task["artifact_sets"] + [{"digest": set_digest, "artifacts": arts, "submitted_by": self.b.seat,
                                             "authored_by": sorted({self.b.seat, task["holder"]} | child_authors),
                                             "interpretation_ref": task["interpretation_ref"],
                                             "task_revision": task["revision"] + 1}]
        t2 = dict(task, artifact_sets=sets, reviews=task["reviews"] + new,
                  review_refs=task["review_refs"] + [r["review_ref"] for r in new])
        rec = self.stamp(dict(t2, reviews=self.reapply(t2)), task)
        writes = [(path_of("task", task["task_id"]), self.checked("task", rec))]
        writes.extend(self.helper_wake(rec))
        return self.finish(writes, rec["revision"])

    def k_task_record_review(self) -> Plan:
        """E06/F04: a review names the exact set it reviewed. A late review of an older set is kept as history and
        does not apply to the current one. The reviewer may not be the author of the set it reviews."""
        return self._record_task_review(self.task_for_change())

    def _record_task_review(self, task) -> Plan:
        if task["state"] in TERMINAL_TASK:
            raise Refused("transition_not_allowed", task["state"])
        reviewed = [s_ for s_ in task["artifact_sets"] if s_["digest"] == self.p["reviewed_set_digest"]]
        if not reviewed:
            raise Refused("payload_invalid", "unknown_artifact_set")
        if self.b.seat in self.authors_of(task, self.p["reviewed_set_digest"]):
            raise Refused("not_authorized", "author_cannot_review_own_set")
        if self.p["input_digest"] != self.p["reviewed_set_digest"]:
            raise Refused("payload_invalid", "review_input_is_not_this_set")
        verifiers = {a["seat"] for a in task["assignments"] if a["role"] == "verifier" and a["to_revision"] is None}
        if verifiers and self.b.seat not in verifiers and not self.b.is_owner:
            raise Refused("not_authorized", "not_an_assigned_verifier")   # a verifier assignment narrows who reviews
        if any(r["review_id"] == self.p["review_id"] for r in task["reviews"]):
            raise Refused("id_taken", "review")
        if self.p["review_ref"] in task["review_refs"]:
            raise Refused("id_taken", "review_ref")
        review = {"review_id": self.p["review_id"], "reviewer": self.b.seat,
                  "reviewed_set_digest": self.p["reviewed_set_digest"], "input_digest": self.p["input_digest"],
                  "verdict": self.p["verdict"], "review_ref": self.p["review_ref"],
                  "findings_ref": self.p["findings_ref"], "interpretation_ref": task["interpretation_ref"],
                  "applicable_to_current": False, "task_revision": task["revision"] + 1}
        if self.p.get("no_verdict"):
            if self.p["verdict"] == "approve":     # no verdict is never an approval, whatever the caller says
                raise Refused("invalid", "no_verdict_cannot_approve")
            review["no_verdict"] = True
        t2 = dict(task, reviews=task["reviews"] + [review], review_refs=task["review_refs"] + [self.p["review_ref"]])
        rec = self.stamp(dict(t2, reviews=self.reapply(t2)), task)
        note = []
        if self.p.get("no_verdict"):
            note = self.notice(rec, f"no_verdict@{self.p['review_id']}", "review_without_verdict",
                               {"store": "task_service", "id": task["task_id"], "edition": str(rec["revision"]),
                                "digest": None},
                               {"kind": "review_without_verdict", "task_id": task["task_id"],
                                "review_id": self.p["review_id"], "reviewer": self.b.seat,
                                "reviewed_set": self.p["reviewed_set_digest"]})
        return self.finish([(path_of("task", task["task_id"]), self.checked("task", rec))] + note, rec["revision"])

    def k_task_commit_helper_return(self) -> Plan:
        from services.partnership_tasks.helper_return import commit
        return commit(self)

    def k_task_delegate(self) -> Plan:
        """E07: the parent's lead delegates one bounded step to a holder. One level only (a child cannot delegate).
        The child carries the parent's request and interpretation; its lead is the parent's lead, so its result stays
        submitted until that lead accepts it."""
        parent = self.task_for_change()
        if self.b.seat != parent["lead"]:
            raise Refused("not_authorized", "only_the_lead_delegates")
        if parent["parent_ref"] is not None:
            raise Refused("transition_not_allowed", "one_level_only")
        if parent["state"] not in ("submitted", "working"):
            raise Refused("transition_not_allowed", parent["state"])
        if self.p["fence_generation"] != parent["fence_generation"]:
            raise Refused("fenced", "stale_generation")
        self.actionable(parent["interpretation_ref"], self.svc.journal.read(path_of("request", parent["request_ref"])))
        cid = self.p["child_task_id"]
        if self.svc.journal.read(path_of("task", cid)) is not None:
            raise Refused("id_taken", "task")
        assignments = [{"role": role, "seat": seat, "from_revision": 1, "to_revision": None,
                        "command_id": self.cmd["command_id"], "reason": None, "effective_from_at": self.now,
                        "effective_to_at": None} for role, seat in (("lead", parent["lead"]), ("holder", self.p["holder"]))]
        child = self.stamp({"schema": SCHEMAS["task"], "task_id": cid, "request_ref": parent["request_ref"],
                            "interpretation_ref": parent["interpretation_ref"], "lead": parent["lead"],
                            "holder": self.p["holder"], "outcome": self.p["outcome"], "done_when": self.p["done_when"],
                            "return_window": self.p["return_window"], "state": "submitted", "reason": None,
                            "waiting": None, "parent_ref": parent["task_id"], "execution_refs": [], "review_refs": [],
                            "acceptances": [], "assignments": assignments, "fence_generation": 0,
                            "pending_operation": None, "artifact_sets": [], "reviews": [], "rounds": 0}, None)
        delegate = {"role": "delegate", "seat": self.p["holder"], "from_revision": parent["revision"] + 1,
                    "to_revision": None, "command_id": self.cmd["command_id"], "reason": f"child {cid}",
                    "effective_from_at": self.now, "effective_to_at": None}
        p2 = self.stamp(dict(parent, assignments=parent["assignments"] + [delegate]), parent)
        return self.finish([(path_of("task", parent["task_id"]), self.checked("task", p2)),
                            (path_of("task", cid), self.checked("task", child))], p2["revision"])

    def k_task_lead_unavailable(self) -> Plan:
        """E08/F05: a missed lead checkpoint, recorded by the platform, scheduler or owner. The task waits for the
        principal; no successor is appointed and no model is woken. Holders of already-admitted child steps may
        finish within their bounds; anything needing the lead (acceptance, new admission, delegation) holds."""
        task = self.task_for_change()
        if not (self.b.origin in ("platform", "scheduler") or self.b.is_owner):
            raise Refused("not_authorized", "detection_is_not_an_agent_claim")
        if task["state"] not in ACTIVE_TASK:
            raise Refused("transition_not_allowed", task["state"])
        if task["waiting"] and task["waiting"]["reason"] == "lead_unavailable":
            raise Refused("transition_not_allowed", "already_recorded")
        if self.p["fence_generation"] != task["fence_generation"]:
            raise Refused("fenced", "stale_generation")
        before = f" (was waiting: {task['waiting']['reason']})" if task["waiting"] else ""
        rec = self.stamp(dict(task, state="waiting", reason="lead checkpoint missed" + before,
                              waiting={"reason": "lead_unavailable", "who_can_unblock": self.b.principal},
                              execution_refs=task["execution_refs"] + self.p["evidence_refs"]), task)
        note = self.notice(task, f"lead_unavailable@{task['revision'] + 1}", "needs_owner",
                           {"store": "task_service", "id": task["task_id"], "edition": str(rec["revision"]),
                            "digest": None},
                           {"kind": "needs_owner", "task_id": task["task_id"], "lead": self.effective_lead(task),
                            "evidence": self.p["evidence_refs"],
                            "choices": ["resume when the lead is back", "reassign the lead", "drop the task"]})
        return self.finish([(path_of("task", task["task_id"]), self.checked("task", rec))] + note, rec["revision"])

    def k_task_reopen(self) -> Plan:
        """Reopen a terminal task as a new revision. A delegated child may be reopened MAX_CHILD_ROUNDS times (J3);
        a top-level task is not bounded here: its lead and the principal decide."""
        task = self.task_for_change()
        if task["state"] not in TERMINAL_TASK:
            raise Refused("transition_not_allowed", "not_terminal")
        if not (self.b.seat == self.effective_lead(task) or self.b.is_owner):
            raise Refused("not_authorized", "only_the_lead_reopens")
        if self.lead_blocked(task) and not self.b.is_owner:
            raise Refused("fenced", "lead_unavailable")
        if task["parent_ref"] is not None and self.load("task", task["parent_ref"])["state"] in TERMINAL_TASK:
            raise Refused("transition_not_allowed", "parent_terminal")
        if task["parent_ref"] is not None and task["rounds"] >= MAX_CHILD_ROUNDS:
            raise Refused("transition_not_allowed", "revision_limit")     # J3: bounded; the lead decides what next
        extra = self.fence_outbox(task["task_id"], "reopened")
        rec = self.stamp(dict(task, state="submitted", reason=self.p["reason"], waiting=None, rounds=task["rounds"] + 1,
                              acceptances=[dict(a, status="superseded") for a in task["acceptances"]],
                              fence_generation=task["fence_generation"] + 1), task)
        return self.finish([(path_of("task", task["task_id"]), self.checked("task", rec))] + extra, rec["revision"])

    def k_task_adopt_interpretation(self) -> Plan:
        """N1: move a task onto the interpretation that superseded its current one. Only along the supersession
        chain, only when the new one is actionable, only by the lead or owner. Fences; pending sends are fenced."""
        task = self.task_for_change()
        if not (self.b.seat == self.effective_lead(task) or self.b.is_owner):
            raise Refused("not_authorized", "only_the_lead_rebases")
        if self.lead_blocked(task) and not self.b.is_owner:
            raise Refused("fenced", "lead_unavailable")
        if task["state"] in TERMINAL_TASK:
            raise Refused("transition_not_allowed", task["state"])
        if self.p["fence_generation"] != task["fence_generation"]:
            raise Refused("fenced", "stale_generation")
        chain, cur = [], self.load("interpretation", task["interpretation_ref"])
        while cur["superseded_by"] is not None:
            chain.append(cur["superseded_by"])
            cur = self.load("interpretation", cur["superseded_by"])
        if self.p["interpretation_ref"] not in chain:
            raise Refused("payload_invalid", "not_a_successor_of_the_current_interpretation")
        request = self.svc.journal.read(path_of("request", task["request_ref"]))
        self.actionable(self.p["interpretation_ref"], request)
        new = dict(task, interpretation_ref=self.p["interpretation_ref"],
                   fence_generation=task["fence_generation"] + 1)
        new["reviews"] = self.reapply(new)                       # S2-2: reviews of the old reading stop applying
        rec = self.stamp(new, task)
        writes = [(path_of("task", task["task_id"]), self.checked("task", rec))]
        if request is not None and request["interpretation_ref"] != self.p["interpretation_ref"]:
            r2 = self.stamp(dict(request, interpretation_ref=self.p["interpretation_ref"]), request)
            writes.append((path_of("request", request["request_id"]), self.checked("request", r2)))
        writes += self.fence_outbox(task["task_id"], "interpretation_rebased")
        self.links["interpretation_id"] = self.p["interpretation_ref"]
        return self.finish(writes, rec["revision"])

    # -- outbox to the harness (B2A: E02, E03, Q05) --

    def k_task_request_admission(self) -> Plan:
        """The lead asks the execution authority to admit a bounded bundle. The task change and the outbox intent are
        one journal intent (E02: source state plus outbox intent in the source transaction). Nothing is sent here."""
        task = self.task_for_change()
        if not (self.b.seat == self.effective_lead(task) or self.b.is_owner):
            raise Refused("not_authorized", "only_the_lead_requests_admission")
        if self.lead_blocked(task) and not self.b.is_owner:
            raise Refused("fenced", "lead_unavailable")                  # S2-4
        if task["state"] not in ("submitted", "working"):
            raise Refused("transition_not_allowed", task["state"])
        if self.p["fence_generation"] != task["fence_generation"]:
            raise Refused("fenced", "stale_generation")
        self.actionable(task["interpretation_ref"], self.svc.journal.read(path_of("request", task["request_ref"])))
        if self.open_effects(task["task_id"], ("unknown", "conflict")):
            raise Refused("fenced", "effects_unreconciled")     # Q05: no new admission over an uncertain one
        op = self.p["operation_id"]
        if op.startswith((FENCE_OP_PREFIX, NOTICE_OP_PREFIX, WAKE_OP_PREFIX)):
            raise Refused("payload_invalid", "reserved_operation_id")
        if self.svc.journal.read(path_of("outbox", op)) is not None:
            raise Refused("id_taken", "operation")
        card = self.p.get("card")
        if card is not None:
            # B4 v0.6: the digest Robert will approve is the authority's own compile of this exact card.
            from core.mc_delegation_dispatcher import cards as card_mod
            try:
                compiled = card_mod.Catalogue.load().compile(card)
            except card_mod.CardRefused as exc:
                raise Refused("payload_invalid", "card_" + str(exc)[:40]) from None
            # The harness writes bare hex; the task service's digests are "sha256:<hex>" (the worker converts back).
            if "sha256:" + compiled.digest != self.p["bundle_digest"] or self.p["bundle_ref"]["id"] != compiled.card_id:
                raise Refused("payload_invalid", "card_digest_mismatch")
        ob = {"schema": SCHEMAS["outbox"], "operation_id": op, "task_id": task["task_id"], "destination": "harness",
              "operation": "admit_bundle", "bundle_digest": self.p["bundle_digest"], "bundle_ref": self.p["bundle_ref"],
              "fence_generation": task["fence_generation"], "interpretation_ref": task["interpretation_ref"],
              "state": "pending", "attempts": [], "destination_receipt": None, "reason": None, "packet": card}
        ob = self.stamp(ob, None)
        ref_ = {"store": "harness", "id": op, "edition": None, "digest": self.p["bundle_digest"]}
        t2 = self.stamp(dict(task, execution_refs=task["execution_refs"] + [ref_]), task)
        return self.finish([(path_of("task", task["task_id"]), self.checked("task", t2)),
                            (path_of("outbox", op), self.checked("outbox", ob))], t2["revision"])

    def k_outbox_approve(self) -> Plan:
        """Robert approves one admission, from his own session (B4 execution design v0.4 §2, v0.5 M2-M3).
        - Only the owner's session binding; only a pending admit_bundle entry whose task's fence is unchanged.
        - The approval names exactly this operation and card digest: the cited owner-action event's content must be
          {"action": "admit", "operation_id": <this>, "card_digest": <this entry's bundle_digest>}, recorded from the
          owner session in this store, and no other approval may cite it.
        - A fresh unspent approval is never replaced; a spent or expired one may be (a new click, a new event).
        The authority re-checks this record inside its own admission transaction (v0.5 M2); nothing is sent here."""
        if not self.b.is_owner:
            raise Refused("not_authorized", "only_the_owner_approves")
        ob = self.target()
        if ob is None:
            raise Refused("not_found", "outbox")
        self.expect(ob)
        if ob["operation"] != "admit_bundle" or ob["state"] != "pending":
            raise Refused("transition_not_allowed", f"{ob['operation']}:{ob['state']}")
        task = self.load("task", ob["task_id"])
        self.links.update(task_id=task["task_id"], request_id=task["request_ref"],
                          interpretation_id=ob["interpretation_ref"])
        if task["fence_generation"] != ob["fence_generation"] or task["state"] not in ("submitted", "working"):
            raise Refused("fenced", "task_changed_since_enqueue")
        if self.p["card_digest"] != ob["bundle_digest"]:
            raise Refused("not_authorized", "approval_digest_mismatch")
        current = ob.get("approval")
        if current and current["spent_at"] is None and not self.approval_expired(current):
            raise Refused("id_taken", "already_approved")
        event_id = self.p["approval_event_id"]
        ev = self.svc.journal.read(path_of("source_event", event_id))
        if (ev is None or ev["data_scope"] != ob["data_scope"] or ev["source"]["store"] != OWNER_ACTION_STORE
                or ev.get("provenance", {}).get("origin") != "owner_session"):
            raise Refused("not_authorized", "approval_not_for_this_card")
        try:
            action = json.loads(ev["content"])
        except ValueError:
            action = None
        if action != {"action": "admit", "operation_id": ob["operation_id"], "card_digest": ob["bundle_digest"]}:
            raise Refused("not_authorized", "approval_not_for_this_card")
        folder = self.svc.journal.root / DIRS["outbox"]
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            other = self.svc.journal.read(f"{DIRS['outbox']}/{path.name}") or {}
            if (other.get("approval") or {}).get("event_id") == event_id:
                raise Refused("not_authorized", "approval_already_used")
        rec = self.stamp(dict(ob, approval={"event_id": event_id, "card_digest": ob["bundle_digest"],
                                            "approved_at": self.now, "spent_at": None}), ob)
        return self.finish([(path_of("outbox", ob["operation_id"]), self.checked("outbox", rec))], rec["revision"])

    def approval_expired(self, approval) -> bool:
        from datetime import datetime
        approved = datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00"))
        now = datetime.fromisoformat(self.now.replace("Z", "+00:00"))
        return (now - approved).total_seconds() > APPROVAL_WINDOW_S

    def k_outbox_record(self) -> Plan:
        """What a drainer is about to do or what the destination answered (E03, Q05).
        - Only a platform drainer binding records sends and answers; only the owner abandons (S2-3).
        - ``attempted`` is recorded before the send, only from ``pending`` with the task's fence unchanged (admissions);
          a stale drainer loses here. The send carries the attempt number.
        - ``applied`` needs the destination's receipt naming this operation and this exact digest; the method says
          whether it came from the send's answer or a lookup.
        - ``definitely_absent`` comes only from the destination's lookup for this attempt number, after which the
          destination refuses that attempt forever (S2-1); the entry goes back to pending for a resend under the same
          id, or to fenced if the task moved on.
        - An unknown answer holds. ``abandoned`` (owner) ends an unknown or conflicting entry and says the effect may
          exist."""
        ob = self.target()
        if ob is None:
            raise Refused("not_found", "outbox")
        self.expect(ob)
        task = self.load("task", ob["task_id"])
        self.links.update(task_id=task["task_id"], request_id=task["request_ref"],
                          interpretation_id=ob["interpretation_ref"])
        obs, state = self.p["observation"], ob["state"]
        if obs in ("abandoned", "read", "declined"):
            if not self.b.is_owner:
                raise Refused("not_authorized", "only_the_owner_" + {"abandoned": "abandons", "read": "reads",
                                                                     "declined": "declines"}[obs])
        elif self.b.origin != "platform" or self.b.seat != DRAINER_SEAT:
            # Codex finding 2: the dedicated drainer seat, not any platform-origin binding that lists this kind.
            raise Refused("not_authorized", "only_a_drainer_records")
        moves = {"attempted": ("pending",), "applied": ("attempted", "unknown"), "refused": ("attempted",),
                 "unknown": ("attempted",), "definitely_absent": ("unknown", "attempted"),
                 "conflict": ("attempted", "unknown"), "fenced": ("pending",), "abandoned": ("unknown", "conflict"),
                 "read": ("acknowledged",), "declined": ("pending",)}
        if state not in moves[obs]:
            raise Refused("transition_not_allowed", f"{state}+{obs}")
        admission = ob["operation"] == "admit_bundle"
        stale = task["fence_generation"] != ob["fence_generation"] or task["state"] not in ("submitted", "working")
        wake = ob["operation"] == "wake_lead"
        if wake:
            packet = ob["packet"]
            stale = (wake_problem(self.svc, task, self.b.read_scopes) is not None
                     or task["fence_generation"] != ob["fence_generation"]
                     or task["state"] not in ("working", "waiting", "completed")
                     or self.effective_lead(task) != packet["lead"]
                     or task.get("charter_ref") != packet["charter_ref"]
                     or not task["artifact_sets"]
                     or task["artifact_sets"][-1]["digest"] != packet["result_digest"])
            if obs == "attempted" and stale:
                raise Refused("fenced", "task_changed_since_enqueue")
        if obs == "attempted" and admission:
            if stale:
                raise Refused("fenced", "task_changed_since_enqueue")
            self.actionable(ob["interpretation_ref"], self.svc.journal.read(path_of("request", task["request_ref"])))
            approval = ob.get("approval")
            if not approval or approval["spent_at"] is not None or self.approval_expired(approval) \
                    or approval["card_digest"] != ob["bundle_digest"]:
                raise Refused("not_authorized", "admission_needs_robert_approval")   # v0.5 M3: nothing is sent
        if obs == "fenced" and not ((admission or wake) and stale):
            raise Refused("transition_not_allowed", "not_stale")
        receipt = self.p["destination_receipt"]
        if obs == "read" and ob["operation"] != "notify":
            raise Refused("transition_not_allowed", "only_notices_are_read")
        expected_store = ob["destination"]
        if obs == "applied" and (receipt is None or receipt["store"] != expected_store
                                 or receipt["id"] != ob["operation_id"] or receipt.get("digest") != ob["bundle_digest"]):
            raise Refused("payload_invalid", "receipt_not_for_this_operation")
        new_state = {"attempted": "attempted", "applied": "acknowledged", "refused": "refused", "unknown": "unknown",
                     "definitely_absent": "pending", "conflict": "conflict", "fenced": "fenced",
                     "abandoned": "abandoned", "read": "read", "declined": "declined"}[obs]
        if obs == "definitely_absent" and (admission or wake) and stale:
            new_state = "fenced"                    # never arrived, and now stale: it must not be sent later
        sends = sum(1 for a in ob["attempts"] if a["observation"] == "attempted")
        method = {"applied": "destination_answer" if state == "attempted" and self.p["code"] != "lookup"
                  else "destination_lookup", "definitely_absent": "destination_lookup",
                  "abandoned": "owner_decision", "read": "owner_decision",
                  "declined": "owner_decision"}.get(obs, "drainer")
        row = self.attempt_row(obs, self.p["code"], method, sends + 1 if obs == "attempted" else (sends or None))
        row["attempt"] = len(ob["attempts"]) + 1
        approval = ob.get("approval")
        if obs == "attempted" and admission:
            approval = dict(approval, spent_at=self.now)        # one approval, one send: a resend needs a new one
        rec = self.stamp(dict(ob, state=new_state, attempts=ob["attempts"] + [row],
                              destination_receipt=receipt if obs == "applied" else ob["destination_receipt"],
                              reason=self.p["code"], **({"approval": approval} if "approval" in ob or approval else {})),
                         ob)
        return self.finish([(path_of("outbox", ob["operation_id"]), self.checked("outbox", rec))], rec["revision"])

    # -- corrections (C06) --

    def k_correction_apply(self) -> Plan:
        """A recorded correction fences the request (and its task): anything planned under the old generation is
        refused. Cancel withdraws an unpromoted request and cancels a promoted task; other changes need the
        superseding interpretation, and a promoted task waits for its lead to re-check authority. Effects already
        admitted elsewhere are not undone or replayed here (B2 reconciles them)."""
        if self.cmd["target"]["type"] != "request":
            raise Refused("payload_invalid", "target_type")
        req = self.request_for_change()
        corr = self.load("source_event", self.p["correction_event_id"])
        self.same_scope(corr, "source_event")
        if corr["correction_of"] is None:
            raise Refused("payload_invalid", "not_a_correction")
        if req["state"] in ("failed", "withdrawn"):
            raise Refused("transition_not_allowed", req["state"])
        if corr["correction_of"] not in lineage(req):
            raise Refused("payload_invalid", "correction_targets_another_request")
        if not (self.b.is_owner or self.b.seat == req["responder"]):
            raise Refused("not_authorized", "not_responder")
        cancel = "cancel" in self.p["changes"]
        if not cancel and self.p["interpretation_ref"] is None:
            raise Refused("payload_invalid", "interpretation_required")
        new_interp = None
        if self.p["interpretation_ref"] is not None:
            new_interp = self.load("interpretation", self.p["interpretation_ref"])
            self.same_scope(new_interp, "interpretation")
            if corr["source_event_id"] not in new_interp["correction_event_ids"]:
                raise Refused("payload_invalid", "interpretation_does_not_cite_correction")
            if req["task_ref"] is not None and not cancel:
                # A promoted task may only move onto a reading that could itself have been promoted (S1-a).
                self.actionable(new_interp["interpretation_id"], req)
        if req["task_ref"] is not None:
            task_now = self.load("task", req["task_ref"])
            if task_now["state"] in TERMINAL_TASK:
                raise Refused("transition_not_allowed", "task_terminal_reopen_first")
        new_req = dict(req, fence_generation=req["fence_generation"] + 1)
        if new_interp is not None:
            new_req["interpretation_ref"] = new_interp["interpretation_id"]
        if self.p["intent_text"]:
            new_req["refinements"] = req["refinements"] + [_edition(req)]
            new_req["intent"] = {"text": self.p["intent_text"], "revision": req["intent"]["revision"] + 1,
                                 "source_event_id": corr["source_event_id"]}
        writes = []
        if req["task_ref"] is None:
            if cancel and req["state"] == "answered":
                raise Refused("transition_not_allowed", "answered_nothing_to_cancel")
            if cancel:
                new_req.update(state="withdrawn", reason="corrected: cancel")
            elif req["state"] == "answered":
                new_req["state"] = "active"
        else:
            task = self.load("task", req["task_ref"])
            t2 = dict(task, fence_generation=task["fence_generation"] + 1)
            if new_interp is not None:
                t2["interpretation_ref"] = new_interp["interpretation_id"]
                t2["reviews"] = self.reapply(t2)
            if task["state"] in ACTIVE_TASK:
                if cancel:
                    t2.update(state="canceled", reason="corrected: cancel", waiting=None)
                    writes += self.cascade(task, "parent canceled by correction")
                else:
                    t2.update(state="waiting", waiting={"reason": "authorization", "who_can_unblock": self.effective_lead(task)},
                              reason="corrected: " + ", ".join(self.p["changes"]))
            t2 = self.stamp(t2, task)
            writes.append((path_of("task", task["task_id"]), self.checked("task", t2)))
            writes += self.fence_outbox(task["task_id"], "corrected")
        self.links.update(source_event_id=corr["source_event_id"],
                          interpretation_id=new_req["interpretation_ref"])
        rec = self.stamp(new_req, req)
        writes.insert(0, (path_of("request", req["request_id"]), self.checked("request", rec)))
        return self.finish(writes, rec["revision"])

"""The runner door: the harness's own records become task-service commands, under the child's real holder seat
(B4 execution design v0.4 §4.2, v0.5 M4-M6/S1/S5, v0.6; facts in _working/partnership-v11/HARNESS_RESULTS_MAP).

Only what the authority recorded counts; nothing a model says reaches the task service from here.
- Pickup: an item of the child's card that the authority shows claimed, running or finished moves a ``submitted``
  child to ``working``. The evidence is the harness item (request id, generation).
- Builder result: a ``completed`` build step's recorded outputs (``cards.recorded_outputs``: kind, ref, sha256)
  become the child's artifact set (``task.submit_artifacts``) under the holder seat. The causal parent is the
  item's result event.
- Review result: a ``completed`` review step counts only when its ``output_text`` is a valid verdict
  (``review_verdict.schema.json``) AND it recorded a ``verdict`` artifact whose sha256 is sha256(output_text). Absent
  or mismatched means ``changes_requested``, never ``approve`` (v0.5 M5).
  - The review card's input artifacts, composed by MC's adapter from the builder child's submitted set, must equal,
    one to one, the builder card's recorded outputs. The door finds that builder child in the harness by exactly
    this match.
  - It then requires the set to still be the builder child's CURRENT set (``review_inputs_stale`` otherwise; v0.6
    Must 1).
  - It records ``task.record_review`` on the builder child under the review child's holder seat, which the service
    refuses for an author. It then submits the verdict as the review child's own artifact set (v0.5 S5).
- Final failure: a terminal non-completed step (failed, refused, timed_out, cancelled, output_limit, interrupted,
  expired) moves a working child to ``failed``, with the item as evidence.

Command ids derive from the harness item and its generation or result event, so a repeated pass is answered from the
journal and never doubles anything. Each pass reads the harness read-only. The only writes are task-service commands.
"""
from __future__ import annotations

import hashlib
import json
import logging

from core.canonical_json import digest
from core.mc_delegation_dispatcher.cards import recorded_outputs
from services.partnership_tasks import contracts
from services.partnership_tasks.client import TaskServiceUnavailable, command

_log = logging.getLogger("partnership.runner_door")
FAILED_STATES = ("failed", "refused", "timed_out", "cancelled", "output_limit", "interrupted", "expired")
STARTED_STATES = ("claimed", "running", "completed") + FAILED_STATES
RUNTIME = {"kind": "deterministic", "model": None, "provider": None, "build": "partnership_tasks.runner_door/1",
           "config_version": None}
VERDICT_SCHEMA = "minimoi.partnership.review_verdict/1"


def harness_ref(item):
    return {"store": "harness", "id": str(item["request_id"]), "edition": str(item["generation"]), "digest": None}


def artifact_refs(outputs):
    return sorted(({"store": "harness", "id": a["ref"], "edition": None, "digest": "sha256:" + a["sha256"]}
                   for a in outputs), key=lambda a: (a["store"], a["id"], a.get("edition") or "", a["digest"]))


def set_digest(refs):
    return digest(sorted(refs, key=lambda a: (a["store"], a["id"], a.get("edition") or "", a["digest"])))


def verdict_of(item):
    """The explicit verdict, or None (meaning changes requested): output_text must be the verdict JSON and a recorded
    'verdict' artifact must carry exactly its sha256."""
    text = item.get("output_text")
    if not isinstance(text, str) or not text.strip():
        return None
    arts = [a for a in recorded_outputs(item.get("artifacts") or []) if a["kind"] == "verdict"]
    if len(arts) != 1 or arts[0]["sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest():
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if contracts.errors("review_verdict", "verdict", doc) or doc.get("schema") != VERDICT_SCHEMA:
        return None
    return doc, arts[0]


class RunnerDoor:
    def __init__(self, connect, *, principal: str, environment: str, reader, seats: dict, task_environment: str,
                 scope: str = "personal"):
        """``reader`` reads task records (``get(type, id)``); ``seats`` maps a seat to its TaskClient (the runner
        seats only). The harness is read through ``connect`` (read-only use)."""
        self.connect, self.principal, self.environment = connect, principal, environment
        self.reader, self.seats, self.task_env, self.scope = reader, seats, task_environment, scope

    # -- harness reads ---------------------------------------------------------------------------------------------
    def _rows(self, sql, args):
        from psycopg2.extras import RealDictCursor
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def admitted(self):
        return self._rows("""SELECT operation_id, task_id, card_id, digest FROM guild.mc_task_operations
            WHERE principal=%s AND environment=%s AND kind='admit_bundle' AND applied AND card_id IS NOT NULL
            ORDER BY recorded_at""", (self.principal, self.environment))

    def items(self, card_id):
        return self._rows("""SELECT request_id, job_id, step, actor, state, generation, result_event_id, artifacts,
            output_text, input_artifacts FROM guild.mc_delegations WHERE principal_id=%s AND environment=%s
            AND card_id=%s ORDER BY queue_seq""", (self.principal, self.environment, card_id))

    # -- one pass --------------------------------------------------------------------------------------------------
    def pass_once(self) -> list:
        """Settle every admitted card once. Returns what was sent (kind, task, outcome) for the worker's log."""
        sent = []
        ops = self.admitted()
        built = {}
        for op in ops:
            its = self.items(op["card_id"])
            done = [i for i in its if i["step"] == "build" and i["state"] == "completed"]
            if done:
                built[set_digest(artifact_refs(recorded_outputs(done[-1]["artifacts"] or [])))] = op["task_id"]
            op["_items"] = its
        for op in ops:
            try:
                sent += self.settle(op, built)
            except TaskServiceUnavailable as exc:
                _log.warning("runner door: task service unconfirmed for %s (%s)", op["task_id"], exc.answer)
        return sent

    def _send(self, seat, cmd):
        client = self.seats.get(seat)
        if client is None:
            return {"outcome": "refused", "code": "no_runner_seat", "detail": seat}
        return client.submit(cmd)

    def _cmd(self, kind, task, payload, cid, parent):
        return command(kind, "task", task["task_id"], payload, command_id=cid[:80], environment=self.task_env,
                       scope=self.scope, expected_revision=task["revision"], causal_parent=parent,
                       session_id=None, runtime=RUNTIME)

    def settle(self, op, built) -> list:
        task = self.reader.get("task", op["task_id"])
        if task is None or not op["_items"]:
            return []
        out = []
        its = op["_items"]
        last = its[-1]
        holder = task["holder"]
        if task["state"] == "submitted" and any(i["state"] in STARTED_STATES for i in its):
            first = next(i for i in its if i["state"] in STARTED_STATES)
            res = self._send(holder, self._cmd("task.transition", task, {
                "to": "working", "reason": "picked up by the execution authority", "waiting": None,
                "evidence_refs": [harness_ref(first)], "fence_generation": task["fence_generation"]},
                f"cmd-door-work-{first['request_id']}-g{first['generation']}", harness_ref(first)))
            out.append(("task.transition:working", task["task_id"], res.get("outcome")))
            task = self.reader.get("task", op["task_id"]) or task
        if last["state"] == "completed" and last["step"] == "build" and task["state"] in ("working", "waiting"):
            refs = artifact_refs(recorded_outputs(last["artifacts"] or []))
            current = task["artifact_sets"][-1]["digest"] if task["artifact_sets"] else None
            if refs and set_digest(refs) != current:
                res = self._send(holder, self._cmd("task.submit_artifacts", task, {
                    "artifacts": refs, "fence_generation": task["fence_generation"]},
                    f"cmd-door-set-{last['result_event_id']}", dict(harness_ref(last), id=str(last["result_event_id"]))))
                out.append(("task.submit_artifacts", task["task_id"], res.get("outcome")))
        elif last["state"] == "completed" and last["step"] == "review" and task["state"] in ("working", "waiting"):
            out += self._review(op, task, last, built)
        elif last["state"] in FAILED_STATES and task["state"] in ("working", "waiting"):
            res = self._send(holder, self._cmd("task.transition", task, {
                "to": "failed", "reason": f"the execution authority recorded {last['state']}", "waiting": None,
                "evidence_refs": [harness_ref(last)], "fence_generation": task["fence_generation"]},
                f"cmd-door-fail-{last['request_id']}-g{last['generation']}", harness_ref(last)))
            out.append(("task.transition:failed", task["task_id"], res.get("outcome")))
        return out

    def _review(self, op, review_task, item, built):
        inputs = artifact_refs([{"ref": a["ref"], "sha256": a["sha256"]} for a in (item.get("input_artifacts") or [])])
        builder_id = built.get(set_digest(inputs)) if inputs else None
        if builder_id is None:
            _log.warning("runner door: review %s matches no builder card's outputs", review_task["task_id"])
            return [("task.record_review", review_task["task_id"], "refused:review_inputs_unmatched")]
        builder = self.reader.get("task", builder_id)
        reviewed = set_digest(inputs)
        if builder is None or not builder["artifact_sets"] or builder["artifact_sets"][-1]["digest"] != reviewed:
            return [("task.record_review", builder_id, "refused:review_inputs_stale")]
        found = verdict_of(item)
        verdict = found[0]["verdict"] if found and found[0]["reviewed_set_digest"] == reviewed else "changes_requested"
        art = found[1] if found else None
        review_ref = {"store": "harness", "id": art["ref"] if art else str(item["request_id"]),
                      "edition": str(item["generation"]),
                      "digest": "sha256:" + (art["sha256"] if art else hashlib.sha256(
                          (item.get("output_text") or "").encode()).hexdigest())}
        out = []
        review_id = "RV-" + str(item["result_event_id"]).replace("-", "")[:40]
        if any(r["review_id"] == review_id for r in builder["reviews"]):
            res = None                                          # already recorded: never sent twice
        else:
            res = self._send(review_task["holder"], self._cmd("task.record_review", builder, {
                "review_id": review_id, "reviewed_set_digest": reviewed,
                "verdict": verdict, "review_ref": review_ref, "findings_ref": None, "input_digest": reviewed},
                f"cmd-door-rev-{item['result_event_id']}", dict(harness_ref(item), id=str(item["result_event_id"]))))
        if res is not None:
            out.append(("task.record_review:" + verdict, builder["task_id"], res.get("outcome")))
        current = review_task["artifact_sets"][-1]["digest"] if review_task["artifact_sets"] else None
        if art and set_digest([review_ref | {"edition": None}]) != current:
            res = self._send(review_task["holder"], self._cmd("task.submit_artifacts", review_task, {
                "artifacts": [review_ref | {"edition": None}], "fence_generation": review_task["fence_generation"]},
                f"cmd-door-vset-{item['result_event_id']}", dict(harness_ref(item), id=str(item["result_event_id"]))))
            out.append(("task.submit_artifacts:verdict", review_task["task_id"], res.get("outcome")))
        return out

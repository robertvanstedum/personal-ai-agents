"""CoS's bound ``cos_tasks`` tool (B4 connection design v0.2 §4, v0.3; build step 3).

``propose``: CoS proposes a task from Robert's request in this turn; Robert confirms it in the portal.
- Only an owner turn (origin ``owner``) that was saved in CoS history may propose (design M5). Telegram and
  MC-relayed turns answer ``propose_needs_an_owner_conversation_turn``.
- The **platform** seat records the source event from that history turn: store ``cos-history``, id
  ``<conversation_id>:<turn_id>`` (the history turn ref, N2), and Robert's words from history, never from the model.
- The **cos** seat records the interpretation citing it, with ``proposed.task`` from the arguments. Nothing is
  adopted or promoted here.
- One turn gives at most one proposal (N1). Ids derive from the history turn, so a retry is the same command, and a
  different second proposal answers ``already_proposed``.

``status``: a task's state, for the principal's tasks CoS can read. It is allowed in an owner-private meeting (read
only), and the read is logged in the binding trace by the listener's admission.

Runtime on every command comes from here (``cos-bound/1``, model unknown stays null), and ``session_id`` is the
binding's turn. Neither ever comes from tool arguments (S3).
"""
from __future__ import annotations

import os
from core.canonical_json import digest
from services.partnership_tasks.client import TaskServiceUnavailable, command

SCHEMA = "minimoi.cos.tasks_tool/1"
ARGS = {"propose": {"outcome", "lead", "holder", "done_when"}, "status": {"task_id"},
        "ask_helper": {"type_id", "subject"}, "helper_result": {"task_id"}, "helper_review": {"task_id", "set_digest", "verdict", "reasons"}}
REQUIRED = {"propose": {"outcome", "done_when"}, "status": {"task_id"},
            "ask_helper": {"type_id"}, "helper_result": {"task_id"}, "helper_review": {"task_id", "set_digest", "verdict", "reasons"}}
ARGS.update(work_find={"query"}, work_read={"artifact_id", "revision"},
            work_save={"artifact_id", "expected_revision", "title", "content", "unresolved"})
REQUIRED.update(work_find={"query"}, work_read={"artifact_id"},
                work_save={"expected_revision", "title", "content", "unresolved"})
EXECUTOR_SEATS = frozenset({"mc"})          # leads/holders CoS may propose in B4; MC delegates further
DEFAULT_SEAT = "mc"                         # the only seat there is, so leaving lead or holder out says the same thing
RUNTIME = {"kind": "model", "model": None, "provider": "openclaw", "build": "cos-bound/1", "config_version": None}


def _refuse(status, error, **extra):
    return {"status": status, "body": dict(error=error, **extra)}


class CosTaskTools:
    def __init__(self, *, platform, cos, history, environment: str, scope: str = "personal", charter=None, sleep=None):
        self.platform, self.cos, self.history = platform, cos, history
        self.environment, self.scope = environment, scope
        self.charter, self.sleep = charter, sleep                # the EA's approved charter, or None: helpers off

    def serve(self, binding, op: str, args: dict) -> dict:
        if op not in ARGS:
            return _refuse(400, "invalid_operation")
        if not isinstance(args, dict) or set(args) - ARGS[op] or REQUIRED[op] - set(args):
            given = set(args) if isinstance(args, dict) else set()
            return _refuse(422, "unexpected_or_missing_fields", allowed=sorted(ARGS[op]),
                           required=sorted(REQUIRED[op]), missing=sorted(REQUIRED[op] - given))
        if op.startswith("work_"):
            return self._work(binding, op, args)
        if op == "propose":
            if binding.scope != "owner":
                return _refuse(403, "scope_read_only")
            return self._propose(binding, args)
        if op in ("ask_helper", "helper_result", "helper_review"):
            if binding.scope != "owner":
                return _refuse(403, "scope_read_only")
            return self._helper(binding, op, args)
        return self._status(args)

    def _work(self, binding, op, args):
        if binding.scope != "owner" or binding.origin != "owner":
            return _refuse(403, "private_owner_conversation_required")
        from services.partnership_tasks.work_artifact_source import prepare_source
        try:
            outgoing = dict(args)
            if op == "work_save":
                ref = binding.source_ref
                if not isinstance(ref, dict):
                    return _refuse(403, "kept_owner_turn_required")
                turn = self.history.turn(ref["conversation_id"], ref["turn_id"])
                if turn is None or not turn.get("user_text"):
                    return _refuse(409, "owner_words_not_found")
                sid, cmd = prepare_source(source={"store": "cos-history",
                    "id": ref["conversation_id"] + ":" + ref["turn_id"], "position": int(turn.get("seq") or 1)},
                    words=turn["user_text"], environment=self.environment, scope=self.scope)
                if self.platform.submit(cmd).get("outcome") != "accepted":
                    return _refuse(409, "source_not_recorded")
                outgoing["source_event_id"] = sid
            data = self.cos.work_artifacts(op.removeprefix("work_"), outgoing, scope=self.scope)
            good = op != "work_save" or data.get("status") == "saved"
            if not good:
                return {"status": 409, "body": {"error": data.get("reason") or data.get("status", "not_verified"), **data}}
            return {"status": 200, "body": {"message": "Private canonical working document. Draft, not action approval; indexing freshness is separate.", **data}}
        except TaskServiceUnavailable:
            return _refuse(503, "work_outcome_unconfirmed", message="Do not repeat an uncertain save; read the canonical artifact to reconcile.")
        except (ValueError, KeyError):
            return _refuse(422, "work_arguments_invalid")

    def _helper(self, binding, op, args):
        """CoS and its Executive Assistant (helper seats spec v0.4). Same chain as MC and its Apprentice."""
        from core.mc_tools import keep_here
        from services.helper_seats import lead as L
        if self.charter is None:
            return _refuse(409, "helpers_off")
        try:
            if op == "helper_result":
                task = self.cos.get("task", args["task_id"])
                if task is None or not task.get("charter_ref") or task["lead"] != "cos":
                    return _refuse(404, "not_found")
                return {"status": 200, "body": {"schema": SCHEMA, **L.view(self.cos, task)}}
            ref = binding.source_ref
            if binding.origin != "owner" or not isinstance(ref, dict):
                return _refuse(403, "needs_an_owner_conversation_turn")
            turn = self.history.turn(ref["conversation_id"], ref["turn_id"])
            if turn is None or not isinstance(turn.get("user_text"), str) or not turn["user_text"].strip():
                return _refuse(409, "owner_words_not_found")
            turn_key = f"{ref['conversation_id']}:{ref['turn_id']}"
            if keep_here.says_keep_here(turn["user_text"]):
                return _refuse(409, "kept_here", say="He said to keep this here, so nothing was handed to a helper.")
            if op == "helper_review":
                if args["verdict"] not in ("approve", "changes_requested"):
                    return _refuse(422, "invalid", field="verdict")
                task = L.review(self.cos, args["task_id"], args["set_digest"], args["verdict"], str(args["reasons"]), turn_key, self.environment, self.scope, evidence_client=self.platform)
                answer = {"status": 200, "body": {"schema": SCHEMA, "state": task["state"],
                                                "say": f"Drafted by {task['holder']}, accepted by you." if args["verdict"] == "approve" else "Sent back with your reasons."}}
                if args["verdict"] == "approve" and os.environ.get("COS_HELPER_RETURN") == "1":
                    from core.lead_wake.inline import offer
                    text = offer(lead="cos", principal=binding.principal, environment=self.environment,
                                 source_ref={"store": "cos-history", "id": turn_key}, task=task, reader=self.cos,
                                 review_id="RV-h-"+L.review_key(turn_key, args["task_id"], args["set_digest"], args["verdict"], str(args["reasons"])))
                    if text:
                        answer["body"]["presentation_text"] = text
                        answer["body"]["say"] += " Include presentation_text verbatim in your final answer so MiniMoi can verify delivery."
                    else:
                        answer["body"]["say"] += " Acceptance is saved; presentation is unconfirmed. Do not repeat the review."
                return answer
            inputs = {"subject": args["subject"]} if args.get("subject") else {}
            tid = L.ask(lead_client=self.cos, platform=self.platform, charter=self.charter, lead="cos", type_id=args["type_id"], inputs=inputs,
                        source={"store": "cos-history", "id": turn_key, "position": int(turn.get("seq") or 1)}, words=turn["user_text"], turn_key=turn_key,
                        environment=self.environment, scope=self.scope)
            task = L.wait(self.cos, tid, **({"sleep": self.sleep} if self.sleep else {}))
            return {"status": 200, "body": {"schema": SCHEMA, **L.view(self.cos, task)}}
        except L.Refused as exc:
            return _refuse(409, exc.code, detail=exc.detail)
        except TaskServiceUnavailable as exc:
            return {"status": 503, "body": {"error": "task_service_unconfirmed", "answer": exc.answer}}

    def _propose(self, binding, args):
        ref = binding.source_ref
        if binding.origin != "owner" or not isinstance(ref, dict):
            return _refuse(403, "propose_needs_an_owner_conversation_turn")
        for key in ("outcome", "done_when"):
            if not isinstance(args[key], str) or not args[key].strip() or len(args[key]) > 500:
                return _refuse(422, "invalid", field=key)
        lead, holder = args.get("lead", DEFAULT_SEAT), args.get("holder", DEFAULT_SEAT)
        if lead not in EXECUTOR_SEATS or holder not in EXECUTOR_SEATS:
            return _refuse(422, "lead_and_holder_must_be", seats=sorted(EXECUTOR_SEATS))
        turn = self.history.turn(ref["conversation_id"], ref["turn_id"])
        if turn is None or not isinstance(turn.get("user_text"), str) or not turn["user_text"].strip():
            return _refuse(409, "owner_words_not_found")
        key = digest(f"{self.environment}\0{ref['conversation_id']}\0{ref['turn_id']}")[7:47]
        sid, iid = "SE-cos-" + key[:32], "IN-cos-" + key[:32]
        source_cmd = command("source_event.record", "source_event", sid,
                             {"source": {"store": "cos-history", "id": f"{ref['conversation_id']}:{ref['turn_id']}",
                                         "position": int(turn.get("seq") or 1)},
                              "content": turn["user_text"][:8000], "correction_of": None},
                             command_id="cmd-cos-src-" + key, environment=self.environment, scope=self.scope,
                             session_id=binding.turn_id)
        proposed = {"outcome": args["outcome"].strip(), "scope": self.scope, "recipients": ["principal"],
                    "continuing": True,
                    "task": {"lead": lead, "holder": holder, "outcome": args["outcome"].strip(),
                             "done_when": args["done_when"].strip(), "return_window": None}}
        interp_cmd = command("interpretation.record", "interpretation", iid,
                             {"source_event_ids": [sid], "correction_event_ids": [], "proposed": proposed,
                              "context_refs": [], "supersedes": None},
                             command_id="cmd-cos-int-" + key, environment=self.environment, scope=self.scope,
                             session_id=binding.turn_id, runtime=RUNTIME,
                             causal_parent={"store": "task_service", "id": sid, "edition": None, "digest": None})
        try:
            src = self.platform.submit(source_cmd)
            if src.get("outcome") != "accepted":
                return _refuse(409, "source_not_recorded", code=src.get("code"))
            out = self.cos.submit(interp_cmd)
        except TaskServiceUnavailable as exc:
            return {"status": 503, "body": {"error": "task_service_unconfirmed", "answer": exc.answer}}
        if out.get("outcome") == "accepted":
            return {"status": 200, "body": {
                "schema": SCHEMA, "interpretation_id": iid, "state": "awaiting_robert",
                "replayed": bool(out.get("replayed")),
                "message": "Proposed. Nothing starts until Robert confirms it. Confer shows him the proposal with its own "
                           "Confirm button under your reply, so write no link; the task view holds it too."}}
        if out.get("code") == "command_id_reused":
            return _refuse(409, "already_proposed", interpretation_id=iid)
        return _refuse(422, out.get("code") or "refused", detail=out.get("detail"))

    def _status(self, args):
        tid = args["task_id"]
        if not isinstance(tid, str) or not tid.startswith("TK-"):
            return _refuse(422, "invalid", field="task_id")
        try:
            task = self.cos.get("task", tid)
        except TaskServiceUnavailable:
            return {"status": 503, "body": {"error": "task_service_unavailable"}}
        if task is None:
            return _refuse(404, "not_found")
        return {"status": 200, "body": {"schema": SCHEMA, "task_id": tid, "state": task["state"],
                                        "waiting": task.get("waiting"), "lead": task["lead"],
                                        "holder": task["holder"], "outcome": task["outcome"],
                                        "done_when": task["done_when"]}}

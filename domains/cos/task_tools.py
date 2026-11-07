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

from core.canonical_json import digest
from services.partnership_tasks.client import TaskServiceUnavailable, command

SCHEMA = "minimoi.cos.tasks_tool/1"
ARGS = {"propose": {"outcome", "lead", "holder", "done_when"}, "status": {"task_id"}}
REQUIRED = {"propose": {"outcome", "done_when"}, "status": {"task_id"}}
EXECUTOR_SEATS = frozenset({"mc"})          # leads/holders CoS may propose in B4; MC delegates further
DEFAULT_SEAT = "mc"                         # the only seat there is, so leaving lead or holder out says the same thing
RUNTIME = {"kind": "model", "model": None, "provider": "openclaw", "build": "cos-bound/1", "config_version": None}


def _refuse(status, error, **extra):
    return {"status": status, "body": dict(error=error, **extra)}


class CosTaskTools:
    def __init__(self, *, platform, cos, history, environment: str, scope: str = "personal"):
        self.platform, self.cos, self.history = platform, cos, history
        self.environment, self.scope = environment, scope

    def serve(self, binding, op: str, args: dict) -> dict:
        if op not in ARGS:
            return _refuse(400, "invalid_operation")
        if not isinstance(args, dict) or set(args) - ARGS[op] or REQUIRED[op] - set(args):
            given = set(args) if isinstance(args, dict) else set()
            return _refuse(422, "unexpected_or_missing_fields", allowed=sorted(ARGS[op]),
                           required=sorted(REQUIRED[op]), missing=sorted(REQUIRED[op] - given))
        if op == "propose":
            if binding.scope != "owner":
                return _refuse(403, "scope_read_only")
            return self._propose(binding, args)
        return self._status(args)

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

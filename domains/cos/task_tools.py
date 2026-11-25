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
from services.partnership_tasks.client import TaskServiceRefused, TaskServiceUnavailable, command

SCHEMA = "minimoi.cos.tasks_tool/1"
ARGS = {"propose": {"outcome", "lead", "holder", "done_when"}, "status": {"task_id"},
        "ask_helper": {"type_id", "subject"}, "helper_result": {"task_id"}, "helper_review": {"task_id", "set_digest", "verdict", "reasons"}}
REQUIRED = {"propose": {"outcome", "done_when"}, "status": {"task_id"},
            "ask_helper": {"type_id"}, "helper_result": {"task_id"}, "helper_review": {"task_id", "set_digest", "verdict", "reasons"}}
ARGS.update(work_find={"query"}, work_read={"artifact_id", "revision"},
            work_save={"artifact_id", "expected_revision", "title", "content", "unresolved"})
REQUIRED.update(work_find={"query"}, work_read={"artifact_id"},
                work_save={"expected_revision", "title", "content", "unresolved"})
# #680: the private professional-initiative store (parked PI- records with a packet). Planning Studio's INIT- numbers
# are a different series; CoS never writes there.
ARGS.update(initiative_file={"title", "summary", "packet_file_ids"}, initiative_attach={"initiative_id", "file_id"},
            initiative_list=set(), initiative_read={"initiative_id"})
REQUIRED.update(initiative_file={"title", "summary"}, initiative_attach={"initiative_id", "file_id"},
                initiative_list=set(), initiative_read={"initiative_id"})
REFUSAL_WORDS = {
    "work_artifacts_disabled": "private working documents are switched off on this server",
    "professional_initiatives_disabled": "the professional-initiative store is switched off on this server",
    "work_scope_unavailable": "CoS is not allowed to write private working documents here",
    "initiative_scope_unavailable": "CoS is not allowed to file professional initiatives here",
    "planning_studio_id_not_accepted": "that is a Planning Studio INIT number; initiatives here are PI- numbers",
    "initiative_not_found": "no initiative has that number",
    "packet_too_many_files": "a packet holds at most 20 files",
    "packet_too_large": "a packet holds at most 25 MB",
    "file_too_large": "one packet file can be at most 5 MB",
}
EXECUTOR_SEATS = frozenset({"mc"})          # leads/holders CoS may propose in B4; MC delegates further
DEFAULT_SEAT = "mc"                         # the only seat there is, so leaving lead or holder out says the same thing
RUNTIME = {"kind": "model", "model": None, "provider": "openclaw", "build": "cos-bound/1", "config_version": None}


def _refuse(status, error, **extra):
    return {"status": status, "body": dict(error=error, **extra)}


class CosTaskTools:
    def __init__(self, *, platform, cos, history, environment: str, scope: str = "personal", charter=None, sleep=None,
                 bindings=None, files=None):
        self.platform, self.cos, self.history = platform, cos, history
        self.bindings, self.files = bindings, files            # owner words of a meeting turn; the CoS inbox
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
        if op.startswith("initiative_"):
            return self._initiative(binding, op, args)
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
        except TaskServiceRefused as exc:
            return _refuse(409, exc.code, message="Nothing was saved: " + REFUSAL_WORDS.get(exc.code, exc.code) + ".")
        except TaskServiceUnavailable:
            return _refuse(503, "work_outcome_unconfirmed", message="Do not repeat an uncertain save; read the canonical artifact to reconcile.")
        except (ValueError, KeyError):
            return _refuse(422, "work_arguments_invalid")

    def _owner_source(self, binding):
        """(source, words) for the owner's own words in this turn, or a refusal. A CoS conversation turn cites its
        history record; a Guildhall turn is bound only in an owner-private room the scheduler verified, and cites the
        verified trigger text the binding store kept (store ``cos-meeting-owner``)."""
        if binding.scope == "owner" and binding.origin == "owner" and isinstance(binding.source_ref, dict):
            ref = binding.source_ref
            turn = self.history.turn(ref["conversation_id"], ref["turn_id"])
            if turn is None or not turn.get("user_text"):
                return None, _refuse(409, "owner_words_not_found")
            return ({"store": "cos-history", "id": ref["conversation_id"] + ":" + ref["turn_id"],
                     "position": int(turn.get("seq") or 1)}, turn["user_text"]), None
        if binding.scope == "meeting" and binding.origin == "meeting" and self.bindings is not None:
            words = self.bindings.owner_words(binding.turn_id)
            if not isinstance(words, str) or not words.strip():
                return None, _refuse(409, "owner_words_not_found")
            return ({"store": "cos-meeting-owner", "id": f"guildhall:{binding.audience}:{binding.turn_id}",
                     "position": int(binding.trigger_seq or 1)}, words), None
        return None, _refuse(403, "private_owner_turn_required",
                             message="Nothing was filed: an initiative is filed only from Robert's own turn, in your conversation or a private meeting.")

    def _packet(self, file_ids):
        """Stage inbox files with the task service; returns [{file_ref, name}]. Hashes are checked twice."""
        import hashlib
        if file_ids and self.files is None:
            raise ValueError("files_unavailable")
        out = []
        for file_id in list(dict.fromkeys(file_ids or []))[:20]:
            meta = self.files._meta(file_id)
            data = (self.files.inbox / meta["file_id"] / meta["name"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != meta["sha256"]:
                raise ValueError("file_changed")
            staged = self.cos.stage_initiative_file(data, scope=self.scope)
            out.append({"file_ref": staged["file_ref"], "name": meta["name"]})
        return out

    def _initiative(self, binding, op, args):
        from domains.cos.files import FileRefused
        try:
            if op == "initiative_list":
                data = self.cos.initiatives("list", {}, scope=self.scope)
                return {"status": 200, "body": {"message": "Professional initiatives (parked PI- records).", **data}}
            if op == "initiative_read":
                data = self.cos.initiatives("read", {"initiative_id": args["initiative_id"]}, scope=self.scope)
                return {"status": 200, "body": {"message": "The initiative as stored, with its packet index.", **data}}
            source, refused = self._owner_source(binding)
            if refused:
                return refused
            from services.partnership_tasks.work_artifact_source import prepare_source
            sid, cmd = prepare_source(source=source[0], words=source[1], environment=self.environment, scope=self.scope)
            if self.platform.submit(cmd).get("outcome") != "accepted":
                return _refuse(409, "source_not_recorded", message="Nothing was filed: Robert's words could not be recorded.")
            if op == "initiative_file":
                ids = args.get("packet_file_ids") or []
                if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    return _refuse(422, "invalid", field="packet_file_ids")
                data = self.cos.initiatives("file", {"title": args["title"], "summary": args["summary"],
                                                     "files": self._packet(ids), "source_event_id": sid},
                                            scope=self.scope)
                return {"status": 200, "body": {**data, "message":
                        f"Filed as {data['initiative_id']}, parked: no work starts. "
                        f"Packet: {len(data.get('packet', []))} file(s). This is not a Planning Studio INIT number."}}
            data = self.cos.initiatives("attach", {"initiative_id": args["initiative_id"],
                                                   "files": self._packet([args["file_id"]]), "source_event_id": sid},
                                        scope=self.scope)
            words = "Added to" if data.get("status") == "attached" else "Already in the packet of"
            return {"status": 200, "body": {**data, "message": f"{words} {data['initiative_id']} (still parked)."}}
        except TaskServiceRefused as exc:
            verb = "Nothing was filed: " if op in ("initiative_file", "initiative_attach") else "Nothing was read: "
            return _refuse(409, exc.code, message=verb + REFUSAL_WORDS.get(exc.code, exc.code) + ".")
        except FileRefused as exc:
            return _refuse(409, "file_not_found", message="Nothing was filed: " + str(exc))
        except TaskServiceUnavailable:
            return _refuse(503, "initiative_outcome_unconfirmed",
                           message="Not verified. Do not repeat it; list the initiatives to see whether it was filed.")
        except (ValueError, KeyError, OSError):
            return _refuse(422, "initiative_arguments_invalid", message="Nothing was filed: the request was not valid.")

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
                    from domains.cos.lead_return import lead_principal
                    text = offer(lead="cos", principal=lead_principal(binding.principal, os.environ), environment=self.environment,
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

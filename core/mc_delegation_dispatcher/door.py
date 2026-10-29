"""The runner door: the Mac delegation runner's way to a production dispatcher without a database connection.

The runner already speaks six calls to its dispatcher (heartbeat, claim, progress, cancel_requested, renew, result).
``handle`` checks one such call's shape strictly and passes it to ``DispatcherStore``, whose principal and
environment are fixed by the server, never by the caller. ``HttpDispatcher`` is the runner's side: the same
interface as ``StoreDispatcher``, over HTTPS (core/mc_delegation_runner/cli.py --door).

A body without ``protocol`` is version 1 and is checked and passed exactly as before (spec v0.5 R3.2-d). A body with
``protocol: 2`` must have every field of its call's version 2 shape (store.py's docstring), and nothing else; the
store refuses it unless the authority configuration speaks version 2. Version 2 adds two calls, reconcile and effect.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

PATH = "/internal/mc/v1/delegation-runner"
DOORS = ("https://minimoi.ai" + PATH, "https://dev.minimoi.ai" + PATH)
BODY_MAX = 160 * 1024
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_WORD = re.compile(r"^[a-z_]{1,40}$")
_CAPS = {"actors", "cost_enforced_actors", "scopes", "repositories"}      # RunnerConfig.caps()


class Refused(ValueError):
    pass


def need(ok):
    if not ok:
        raise Refused("runner_request_invalid")


def _small_json(value, limit):
    try:
        return len(json.dumps(value)) <= limit
    except (TypeError, ValueError):
        return False


def _caps(c):
    need(type(c) is dict and set(c) <= _CAPS and _small_json(c, 4096))
    for k in _CAPS:
        need(type(c.get(k, [])) is list and all(type(x) is str and _ID.match(x) for x in c.get(k, [])))
    return c


def handle(store, body):
    """One runner call -> {"value": ...}. Raises Refused for anything malformed."""
    need(type(body) is dict and type(body.get("op")) is str)
    if "protocol" in body:
        return _handle_v2(store, body)
    op = body["op"]
    args = {k: v for k, v in body.items() if k != "op"}
    rid = args.get("request_id")
    gen = args.get("generation")
    if op in ("heartbeat", "claim"):
        need(set(args) == ({"runner_id", "state", "capabilities"} if op == "heartbeat" else {"runner_id", "capabilities"}))
        need(type(args["runner_id"]) is str and _ID.match(args["runner_id"]))
        caps = _caps(args["capabilities"])
        if op == "heartbeat":
            need(type(args["state"]) is str and _WORD.match(args["state"]))
            return {"value": store.heartbeat(args["runner_id"], args["state"], caps)}
        return {"value": store.claim(args["runner_id"], caps)}
    need(type(rid) is str and _UUID.match(rid) and type(gen) is int and 0 <= gen < 10**6)
    if op == "progress":
        need(set(args) == {"request_id", "generation", "phase"} and type(args["phase"]) is str
             and _WORD.match(args["phase"]))
        return {"value": store.progress(rid, gen, args["phase"])}
    if op in ("cancel_requested", "renew"):
        need(set(args) == {"request_id", "generation"})
        return {"value": getattr(store, op)(rid, gen)}
    if op == "result":
        need(set(args) == {"request_id", "generation", "event_id", "status", "artifacts", "detail", "output_text"})
        need(type(args["event_id"]) is str and _ID.match(args["event_id"]))
        need(type(args["status"]) is str and _WORD.match(args["status"]))
        need(type(args["artifacts"]) is list and len(args["artifacts"]) <= 20 and _small_json(args["artifacts"], 8192))
        need(type(args["detail"]) is dict and _small_json(args["detail"], 8192))
        need(args["output_text"] is None or (type(args["output_text"]) is str and len(args["output_text"]) <= 64 * 1024))
        return {"value": store.result(rid, gen, event_id=args["event_id"], status=args["status"],
                                      artifacts=args["artifacts"], detail=args["detail"],
                                      output_text=args["output_text"])}
    raise Refused("runner_op_unknown")


_V2 = {"heartbeat": {"runner_id", "state", "capabilities", "facts"},
       "claim": {"runner_id", "capabilities"},
       "progress": {"runner_id", "request_id", "generation", "event_id", "at", "phase", "checkpoint"},
       "cancel_requested": {"request_id", "generation"},
       "renew": {"runner_id", "request_id", "generation"},
       "result": {"runner_id", "request_id", "generation", "event_id", "at", "status", "artifacts", "detail",
                  "output_text", "route", "usage"},
       "reconcile": {"runner_id", "request_id", "generation", "stage", "open_operations"},
       "effect": {"runner_id", "request_id", "generation", "effect"}}


def _checked(check, value):
    try:
        return check(value)
    except ValueError:
        raise Refused("runner_request_invalid") from None


def _handle_v2(store, body):
    # The store's own value checks, so the door and a direct caller are held to the same shapes.
    from .store import (check_capabilities, check_checkpoint, check_effect, check_facts, check_operations, check_route,
                        check_stage, check_usage, parse_at)
    need(type(body["protocol"]) is int and body["protocol"] == 2 and body["op"] in _V2)
    op = body["op"]
    args = {k: v for k, v in body.items() if k not in ("op", "protocol")}
    need(set(args) == _V2[op])
    if "runner_id" in args:
        need(type(args["runner_id"]) is str and _ID.match(args["runner_id"]))
    if op in ("heartbeat", "claim"):
        caps = _checked(check_capabilities, args["capabilities"])
        if op == "claim":
            return {"value": store.claim(args["runner_id"], caps, protocol=2)}
        need(type(args["state"]) is str and _WORD.match(args["state"]))
        facts = _checked(check_facts, args["facts"])
        return {"value": store.heartbeat(args["runner_id"], args["state"], caps, protocol=2, facts=facts)}
    rid, gen = args["request_id"], args["generation"]
    need(type(rid) is str and _UUID.match(rid) and type(gen) is int and 0 <= gen < 10**6)
    if op == "cancel_requested":
        return {"value": store.cancel_requested(rid, gen, protocol=2)}
    if op == "renew":
        return {"value": store.renew(rid, gen, protocol=2, runner_id=args["runner_id"])}
    if op == "reconcile":
        stage, ops = _checked(check_stage, args["stage"]), _checked(check_operations, args["open_operations"])
        return {"value": store.reconcile(args["runner_id"], rid, gen, stage, ops, protocol=2)}
    if op == "effect":
        return {"value": store.effect(args["runner_id"], rid, gen, _checked(check_effect, args["effect"]), protocol=2)}
    need(type(args["event_id"]) is str and _UUID.match(args["event_id"]))
    _checked(parse_at, args["at"])
    if op == "progress":
        need(type(args["phase"]) is str and _WORD.match(args["phase"]))
        checkpoint = _checked(check_checkpoint, args["checkpoint"])
        return {"value": store.progress(rid, gen, args["phase"], protocol=2, runner_id=args["runner_id"],
                                        event_id=args["event_id"], at=args["at"], checkpoint=checkpoint)}
    need(type(args["status"]) is str and _WORD.match(args["status"]))
    need(type(args["artifacts"]) is list and len(args["artifacts"]) <= 20 and _small_json(args["artifacts"], 8192))
    need(type(args["detail"]) is dict and _small_json(args["detail"], 8192))
    need(args["output_text"] is None or (type(args["output_text"]) is str and len(args["output_text"]) <= 64 * 1024))
    _checked(check_route, args["route"])
    _checked(check_usage, args["usage"])
    return {"value": store.result(rid, gen, event_id=args["event_id"], status=args["status"],
                                  artifacts=args["artifacts"], detail=args["detail"], output_text=args["output_text"],
                                  protocol=2, runner_id=args["runner_id"], at=args["at"], route=args["route"],
                                  usage=args["usage"])}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("runner_door_redirect_refused")


class HttpDispatcher:
    """The runner's dispatcher over the door. Same interface as dispatcher_adapter.StoreDispatcher; the output text
    and the delegate's reported cost are read here, on the Mac, and only the bounded text crosses."""

    def __init__(self, url, token, artifact_root: Path, *, opener=None):
        if url not in DOORS:
            raise ValueError("runner_door_invalid")
        if type(token) is not str or not 32 <= len(token) <= 256:
            raise ValueError("runner_token_invalid")
        self.url, self._token, self.artifact_root = url, token, Path(artifact_root)
        self._open = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open

    def _call(self, op, **args):
        raw = json.dumps({"op": op, **args}).encode()
        if len(raw) > BODY_MAX:
            raise RuntimeError("runner_request_bound")
        req = urllib.request.Request(self.url, data=raw, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self._token,
            "User-Agent": "MiniMoi-delegation-runner/1"})
        with self._open(req, timeout=20) as r:
            doc = json.loads(r.read(256 * 1024))
        if type(doc) is not dict or "value" not in doc:
            raise RuntimeError("runner_door_answer_invalid")
        return doc["value"]

    def heartbeat(self, runner_id, state, capabilities):
        return self._call("heartbeat", runner_id=runner_id, state=state, capabilities=capabilities)

    def claim(self, runner_id, capabilities):
        return self._call("claim", runner_id=runner_id, capabilities=capabilities)

    def progress(self, request_id, generation, phase):
        return self._call("progress", request_id=request_id, generation=generation, phase=phase)

    def cancel_requested(self, request_id, generation):
        return self._call("cancel_requested", request_id=request_id, generation=generation)

    def renew(self, request_id, generation):
        return self._call("renew", request_id=request_id, generation=generation)

    def result(self, request_id, generation, *, event_id, status, artifacts, detail):
        from core.mc_delegation_runner.dispatcher_adapter import output_of
        text, cost = output_of(self.artifact_root, artifacts)
        if cost is not None:
            detail = {**(detail or {}), "cost_usd": cost}
        return self._call("result", request_id=request_id, generation=generation, event_id=str(event_id),
                          status=status, artifacts=artifacts or [], detail=detail or {}, output_text=text)

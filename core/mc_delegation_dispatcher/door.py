"""The runner door: the Mac delegation runner's way to a production dispatcher without a database connection.

The runner already speaks six calls to its dispatcher (heartbeat, claim, progress, cancel_requested, renew, result).
``handle`` checks one such call's shape strictly and passes it to ``DispatcherStore``, whose principal and
environment are fixed by the server, never by the caller. ``HttpDispatcher`` is the runner's side: the same
interface as ``StoreDispatcher``, over HTTPS (core/mc_delegation_runner/cli.py --door).

A body without ``protocol`` is version 1 and is checked and passed exactly as before (spec v0.5 R3.2-d). A body with
``protocol: 2`` must have every field of its call's version 2 shape (store.py's docstring), and nothing else; the
store refuses it unless the authority configuration speaks version 2. Version 2 adds two calls, reconcile and effect.
A version 2 heartbeat may also carry ``census``: a list of at most census.BATCH_MAX_EVENTS events within
census.BATCH_MAX_BYTES (spec v0.5 R3.2-a, §6.6); the store checks and answers each event.
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
_V2_OPTIONAL = {"heartbeat": {"census"},       # S1-F: the census batch rides the heartbeat (R3.2-a)
                "claim": {"claim_id", "held"}}  # Codex F2: an idempotent claim request and the host's holdings


def _checked(check, value):
    try:
        return check(value)
    except ValueError:
        raise Refused("runner_request_invalid") from None


def _handle_v2(store, body):
    # The store's own value checks, so the door and a direct caller are held to the same shapes.
    from .store import (check_capabilities, check_checkpoint, check_effect, check_facts, check_held, check_operations,
                        check_route, check_stage, check_usage, parse_at)
    need(type(body["protocol"]) is int and body["protocol"] == 2 and body["op"] in _V2)
    op = body["op"]
    args = {k: v for k, v in body.items() if k not in ("op", "protocol")}
    need(set(args) - _V2_OPTIONAL.get(op, set()) == _V2[op])
    if "runner_id" in args:
        need(type(args["runner_id"]) is str and _ID.match(args["runner_id"]))
    if op in ("heartbeat", "claim"):
        caps = _checked(check_capabilities, args["capabilities"])
        if op == "claim":
            if "claim_id" not in args and "held" not in args:
                return {"value": store.claim(args["runner_id"], caps, protocol=2)}
            need("claim_id" in args and "held" in args and type(args["claim_id"]) is str
                 and _UUID.match(args["claim_id"]))                              # F2: both, or neither
            held = _checked(check_held, args["held"])
            return {"value": store.claim(args["runner_id"], caps, protocol=2, claim_id=args["claim_id"], held=held)}
        need(type(args["state"]) is str and _WORD.match(args["state"]))
        facts = _checked(check_facts, args["facts"])
        if "census" not in args:
            return {"value": store.heartbeat(args["runner_id"], args["state"], caps, protocol=2, facts=facts)}
        from core.mc_delegation_runner.census import BATCH_MAX_BYTES, BATCH_MAX_EVENTS
        census = args["census"]
        need(type(census) is list and len(census) <= BATCH_MAX_EVENTS and _small_json(census, BATCH_MAX_BYTES))
        return {"value": store.heartbeat(args["runner_id"], args["state"], caps, protocol=2, facts=facts,
                                         census=census)}
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


ANSWER_MAX = 256 * 1024
# What a version 2 host may read as the authority's refusal of its call: the door's own JSON error on one of these
# statuses (the thin server's, server.py). A 408 or 429 refuses nothing about the call itself; 5xx is the authority
# unavailable. Each of those, and anything that is not the door's JSON, is a lost connection (N-D5).
REFUSAL_STATUSES = (400, 401, 403, 404, 409, 413, 422)
_CODE = re.compile(r"^[a-z][a-z0-9_:]{0,63}$")


class DoorUnreachable(ConnectionError):
    """No answer from the authority: the reply was not the door's JSON (a proxy's or a captive portal's page, a body
    cut short, an empty one), or a status that is not a refusal of the call. An OSError, so the host reads it as a
    lost connection and retries, exactly as a timeout; never as the authority's answer (N-D5)."""


class DoorRefused(RuntimeError):
    """The door refused the call with its own JSON error, ``{"error": <code>}``: an authority answer with a fixed
    code (runner._error_code reads it as one)."""


class HttpDispatcherV2(HttpDispatcher):
    """Version 2 over the door (gate D): the same interface as dispatcher_adapter.StoreDispatcherV2, each body carrying
    ``protocol: 2`` and exactly the fields ``_V2`` names. The runner's pending record supplies ``at``, the output text
    and every body, so a replay is byte-faithful. ``timeout`` bounds each call (the graceful stop's own deadline).

    Only the door's own JSON is an authority answer: a 2xx ``{"value": ...}``, or a refusal status from
    REFUSAL_STATUSES carrying ``{"error": <code>}`` (DoorRefused). Anything else, whatever its status, raises
    DoorUnreachable, a lost connection (N-D5). Version 1's client (HttpDispatcher) is unchanged."""
    protocol = 2

    def __init__(self, url, token, artifact_root: Path, *, opener=None, timeout=20):
        super().__init__(url, token, artifact_root, opener=opener)
        self.timeout = timeout

    def _v2(self, op, **args):
        import urllib.error
        raw = json.dumps({"op": op, "protocol": 2, **args}).encode()
        if len(raw) > BODY_MAX:
            raise RuntimeError("runner_request_bound")
        req = urllib.request.Request(self.url, data=raw, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self._token,
            "User-Agent": "MiniMoi-delegation-runner/2"})
        try:
            with self._open(req, timeout=self.timeout) as r:
                body = r.read(ANSWER_MAX + 1)
        except urllib.error.HTTPError as exc:
            raise _http_refusal(exc) from None
        doc = _door_json(body)
        if doc is None or "value" not in doc:
            raise DoorUnreachable("runner_door_answer_not_authority")
        return doc["value"]

    def heartbeat(self, runner_id, state, capabilities, facts, census=None):
        extra = {} if census is None else {"census": census}
        return self._v2("heartbeat", runner_id=runner_id, state=state, capabilities=capabilities, facts=facts, **extra)

    def claim(self, runner_id, capabilities, *, claim_id=None, held=None):
        if claim_id is None:
            return self._v2("claim", runner_id=runner_id, capabilities=capabilities)
        return self._v2("claim", runner_id=runner_id, capabilities=capabilities, claim_id=claim_id, held=held)

    def progress(self, request_id, generation, *, runner_id, event_id, at, phase, checkpoint):
        return self._v2("progress", runner_id=runner_id, request_id=request_id, generation=generation,
                        event_id=event_id, at=at, phase=phase, checkpoint=checkpoint)

    def cancel_requested(self, request_id, generation):
        return self._v2("cancel_requested", request_id=request_id, generation=generation)

    def renew(self, request_id, generation, *, runner_id):
        return self._v2("renew", runner_id=runner_id, request_id=request_id, generation=generation)

    def result(self, request_id, generation, *, runner_id, event_id, at, status, artifacts, detail, output_text, route,
               usage):
        return self._v2("result", runner_id=runner_id, request_id=request_id, generation=generation,
                        event_id=event_id, at=at, status=status, artifacts=artifacts, detail=detail,
                        output_text=output_text, route=route, usage=usage)

    def reconcile(self, runner_id, request_id, generation, stage, open_operations):
        return self._v2("reconcile", runner_id=runner_id, request_id=request_id, generation=generation, stage=stage,
                        open_operations=open_operations)

    def effect(self, runner_id, request_id, generation, effect):
        return self._v2("effect", runner_id=runner_id, request_id=request_id, generation=generation, effect=effect)


def _door_json(body):
    """The door's JSON object, or None: a body over ANSWER_MAX (cut short), not UTF-8 JSON, or not an object."""
    if body is None or len(body) > ANSWER_MAX:
        return None
    try:
        doc = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return doc if type(doc) is dict else None


def _http_refusal(exc):
    """An HTTP error status read as the authority's refusal (DoorRefused with the door's code) only when it is one of
    REFUSAL_STATUSES and carries the door's JSON error; otherwise a lost connection (DoorUnreachable)."""
    body = None
    try:
        body = exc.read(ANSWER_MAX + 1)
    except Exception:
        pass
    doc = _door_json(body) if exc.code in REFUSAL_STATUSES else None
    code = None if doc is None else doc.get("error")
    if type(code) is str and _CODE.match(code):
        return DoorRefused(code)
    return DoorUnreachable(f"runner_door_status_{int(exc.code)}" if type(exc.code) is int else "runner_door_status")

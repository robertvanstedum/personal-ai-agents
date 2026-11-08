"""The task service over HTTP, for Dev (B4 step 2). A thin transport: every decision stays in ``TaskService``.

    POST /v1/commands                     body: one command (contracts/partnership/v1 command)      -> its result
    POST /v1/receipts                     body: one command as the caller would send it            -> recorded/...
    GET  /v1/records/<type>/<id>          a record in the caller's read scope, else 404
    GET  /v1/reconstruct/<task|request>/<id>[?at=...]   Q07 reconstruction, else 404
    POST /v1/tasks/<id>/dispatch-check    body: {"fence_generation", "interpretation_id"}
    GET  /v1/tasks/<id>/release-gate[?artifact_set_digest=...]
    GET  /v1/work[?seat=...]              a seat's own work (the principal may name any seat)
    GET  /v1/open                         open work in the caller's scopes
    GET  /v1/pending                      proposals waiting for the principal's confirmation (principal only)
    GET  /v1/pending/unfinished           confirmed proposals whose task was never made, to finish (principal only)
    GET  /v1/history?words=...            next-session recall over requests
    GET  /healthz                         liveness only; says nothing about the store

Authentication is ``Authorization: Bearer <token>``. A token whose binding is the owner's session (origin
owner_session) also needs ``X-Portal-Owner-Assertion``: the portal's signature over this exact request, made only on
Robert's own signed-in click, so the token alone never acts as Robert (A02; B4 design v0.2 §2). The token resolves to a seat binding; who acts, from which origin
and in which scopes always comes from that binding, never from the body (A02). "Not found" and "not yours" answer
the same 404 by design. A held or damaged store answers 503, never a guess. Tokens are never logged.

One process, served one request at a time (``serve``): the journal is safe across processes, and Dev load is small.
"""
from __future__ import annotations

import json
import logging

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from services.partnership_tasks.reconstruct import Reconstructor, history, open_work, pending_proposals, unfinished_confirmations
from services.partnership_tasks.service import StoreUnavailable, TaskService
from services.partnership_tasks.views import work_for

MAX_BODY = 256 * 1024
_log = logging.getLogger("partnership_tasks.http")


def _strict_json(raw: bytes):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = value
        return out
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        return None


def _json(value, status=200):
    return Response(json.dumps(value, ensure_ascii=False, sort_keys=True), status=status,
                    mimetype="application/json")


OWNER_AUDIENCE = "partnership-owner"
OWNER_HEADER = "X-Portal-Owner-Assertion"


def create_app(svc: TaskService, *, owner_key=None, owner_env: str | None = None, owner_subject: str | None = None,
               spend=None) -> Flask:
    """``owner_key`` (kid, secret), ``owner_env``, ``owner_subject`` ("owner:<portal id>") and ``spend`` (a nonce
    store) configure the owner rule (B4 design v0.2 §2, v0.3 R-N1/R-N3): a request made with an owner_session
    binding is accepted only with a portal-signed assertion for this exact method, route and body, naming the
    configured owner, used once. Unconfigured, every owner_session request is refused."""
    from core import portal_assertion as PA
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_e):
        return jsonify(error="too_large"), 413

    @app.errorhandler(StoreUnavailable)
    def unavailable(_e):
        return jsonify(error="store_unavailable"), 503

    @app.after_request
    def private(response):
        response.headers["Cache-Control"] = "no-store, private"
        return response

    def token():
        header = request.headers.get("Authorization", "")
        presented = header[7:] if header.startswith("Bearer ") else ""
        return presented if 1 <= len(presented) <= 512 else None

    def authenticated():
        t = token()
        binding = svc.bindings.resolve(t, svc._clock()) if t is not None else None
        if binding is None:
            return None
        if binding.is_owner and not owner_proven():
            return None
        return t

    def owner_proven() -> bool:
        header = request.headers.get(OWNER_HEADER)
        if not header or owner_key is None or not owner_env or not owner_subject or spend is None:
            return False
        try:
            subject = PA.verify(header, owner_key, audience=OWNER_AUDIENCE, env=owner_env, method=request.method,
                                target=PA.canonical_target(request.path, request.query_string),
                                body=lambda: request.get_data(cache=True), spend=spend)
        except PA.Refused:
            return False
        return subject == owner_subject

    def body():
        if request.mimetype != "application/json":
            return None
        return _strict_json(request.get_data(cache=True))

    @app.get("/healthz")
    def health():
        return jsonify(status="up", environment=svc.environment)

    @app.post("/v1/commands")
    def commands():
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        cmd = body()
        if not isinstance(cmd, dict):
            return jsonify(error="payload_invalid"), 400
        result = svc.submit(cmd, t)
        _log.info("command %s -> %s/%s", str(cmd.get("kind"))[:48], result.get("outcome"), result.get("code"))
        return _json(result)

    @app.post("/v1/receipts")
    def receipts():
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        cmd = body()
        if not isinstance(cmd, dict):
            return jsonify(error="payload_invalid"), 400
        return _json(svc.receipt(cmd, t))

    @app.get("/v1/records/<target_type>/<target_id>")
    def record(target_type, target_id):
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        found = svc.get(target_type, target_id, t)
        return _json(found) if found is not None else (jsonify(error="not_found"), 404)

    @app.get("/v1/reconstruct/<start_type>/<start_id>")
    def reconstruct(start_type, start_id):
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        try:
            out = Reconstructor(svc).reconstruct(start_type, start_id, t, at=request.args.get("at"))
        except ValueError:
            return jsonify(error="bad_request"), 400
        if out is None:
            return jsonify(error="not_found"), 404
        return _json(out, 503 if out.get("integrity") == "damaged" else 200)

    @app.post("/v1/tasks/<task_id>/dispatch-check")
    def dispatch_check(task_id):
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        b = body()
        if (not isinstance(b, dict) or set(b) != {"fence_generation", "interpretation_id"}
                or type(b["fence_generation"]) is not int or not isinstance(b["interpretation_id"], str)):
            return jsonify(error="payload_invalid"), 400
        return _json(svc.dispatch_check(task_id, b["fence_generation"], b["interpretation_id"], t))

    @app.get("/v1/tasks/<task_id>/release-gate")
    def release_gate(task_id):
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        return _json(svc.release_gate(task_id, t, request.args.get("artifact_set_digest")))

    @app.get("/v1/work")
    def work():
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        out = work_for(svc, t, request.args.get("seat"))
        return _json(out) if out is not None else (jsonify(error="not_found"), 404)

    @app.get("/v1/open")
    def open_list():
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        return _json(open_work(svc, t))

    @app.get("/v1/pending")
    def pending():
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        out = pending_proposals(svc, t)
        return _json(out) if out is not None else (jsonify(error="not_found"), 404)

    @app.get("/v1/pending/unfinished")
    def pending_unfinished():
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        out = unfinished_confirmations(svc, t)
        return _json(out) if out is not None else (jsonify(error="not_found"), 404)

    @app.get("/v1/outbox/pending")
    def outbox_pending():
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        from services.partnership_tasks.reconstruct import outbox_queue
        out = outbox_queue(svc, t)
        return _json(out) if out is not None else (jsonify(error="not_found"), 404)

    @app.get("/v1/history")
    def recall():
        t = authenticated()
        if t is None:
            return jsonify(error="not_authenticated"), 401
        words = (request.args.get("words") or "").strip()
        if not words or len(words) > 200:
            return jsonify(error="bad_request"), 400
        return _json(history(svc, t, words))

    return app


def main(argv=None) -> int:
    """``python -m services.partnership_tasks.http_api`` with PARTNERSHIP_ROOT, PARTNERSHIP_ENVIRONMENT,
    PARTNERSHIP_BINDINGS (seat bindings file) and optional PARTNERSHIP_GRANTS, PARTNERSHIP_HOST, PARTNERSHIP_PORT.
    Refuses to create a store: the root must already hold a journal (provisioned by the B4 package)."""
    import os
    from pathlib import Path
    from werkzeug.serving import make_server
    from services.partnership_tasks.auth import Bindings, Grants
    root = os.environ["PARTNERSHIP_ROOT"]
    environment = os.environ["PARTNERSHIP_ENVIRONMENT"]
    if environment == "prod":
        # Production takes its own explicit admission; it never presents itself as dev.
        if os.environ.get("MINIMOI_ROLE") != "production" or os.environ.get("PARTNERSHIP_PRODUCTION") != "1":
            raise SystemExit("the HTTP task service on production requires its explicit admission")
    elif environment not in ("dev", "staging"):
        raise SystemExit("the HTTP task service runs only on dev, staging or admitted production")
    if not (Path(root) / "journal" / "journal.jsonl").is_file():
        raise SystemExit("no task store at PARTNERSHIP_ROOT; provision it first")
    grants_file = os.environ.get("PARTNERSHIP_GRANTS")
    grants = Grants(json.loads(Path(grants_file).read_text())) if grants_file else None
    from services.partnership_tasks.charters import load_if_approved
    charters = load_if_approved(os.environ)                                       # a mismatch refuses the start
    svc = TaskService(root, environment, Bindings.load(os.environ["PARTNERSHIP_BINDINGS"]), grants=grants, charters=charters)
    from core import portal_assertion as PA
    nonce_file = os.environ.get("PARTNERSHIP_OWNER_NONCE_FILE")
    app = create_app(svc, owner_key=PA.load_key(os.environ.get("PARTNERSHIP_OWNER_KEY_FILE")),
                     owner_env=os.environ.get("PARTNERSHIP_OWNER_ENV"),
                     owner_subject=os.environ.get("PARTNERSHIP_OWNER_SUBJECT"),
                     spend=PA.NonceFile(nonce_file) if nonce_file else None)
    server = make_server(os.environ.get("PARTNERSHIP_HOST", "0.0.0.0"), int(os.environ.get("PARTNERSHIP_PORT", "8772")),
                         app, threaded=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _log.info("partnership task service up (%s)", environment)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

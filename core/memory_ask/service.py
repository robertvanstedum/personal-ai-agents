"""The Ask service: the host that binds an agent's identity (Codex lane, 5 Oct 2026). **Not started by anything**: no launchd, no
compose entry, no wiring into an agent. This is the frozen contract and its implementation, for review.

    POST /v1/ask        Authorization: Bearer <agent token>     Content-Type: application/json
        {"query": str, "include_raw": bool?, "created_after": "YYYY-MM-DD"?, "created_before": "YYYY-MM-DD"?, "k": int?}
        200 -> the Ask result (two lists, adjacent slot, unresolved, status, citation_rule), exactly ``tool.call``'s
    GET  /v1/health     -> {"ok": true}   (no auth, no detail)

The caller is **only** what the token resolves to in the registry (``identity.py``): agent name, entry point, tiers, scopes, k cap and
rate limit. A body key, header or query-string value that names an agent, scope, tier or canary is refused (400, closed schema) or ignored
(headers and the query string never matter). Every failure is a fixed code, never a message that quotes the query, a token or a record.

    400 bad_arguments | empty_query | bad_k | bad_date | invalid_json     (the request is wrong)
    401 unauthorized      missing, malformed, unknown, revoked and wrong tokens are indistinguishable
    403 tier_not_permitted include_raw for an agent whose grant lacks the raw tier
    413 too_large         body over 4 KB          415 unsupported_media_type    429 rate_limited
    503 registry_unavailable | index_missing | index_incompatible | pin_mismatch     (fail closed)

The registry is re-read when its file changes, so a revocation or a rotation applies from the next request. A registry that is missing,
readable by others or malformed denies everyone (503). Each request builds a fresh embedder, runs one search at a time (the server is
single-threaded on purpose: the host is a small Mac) and appends one line to the Ask query log under the agent's own name.
Nothing else is written. The service reads the shelf and the index; it never captures, edits or deletes either.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

from flask import Flask, jsonify, request

from core.memory_ask import identity, tool
from core.memory_index import indexer, search

MAX_BODY = 4096
DEFAULT_PORT = 8771
_STATUS = {"bad_arguments": 400, "empty_query": 400, "bad_k": 400, "bad_date": 400, "bad_caller": 500,
           "index_missing": 503, "index_incompatible": 503, "pin_mismatch": 503}


def _embedder():
    from core.memory_index.cli import _embedder
    return _embedder()


def create_app(*, registry_path, shelf_root, embedder_factory=None, index_path=None, log_path=None, clock=None,
               monotonic=time.monotonic) -> Flask:
    app = Flask(__name__)
    registry = identity.RegistryFile(registry_path)
    shelf_root = Path(shelf_root)
    index_path = Path(index_path) if index_path else indexer.default_index_path(shelf_root)
    recent: dict[str, deque] = defaultdict(deque)

    def refuse(status: int, code: str, agent: str = "-"):
        print(f"[ask_service] who={agent} status={status} code={code}", file=sys.stderr, flush=True)
        response = jsonify({"error": code})
        response.status_code = status
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/v1/health")
    def health():
        return jsonify({"ok": True})

    @app.post("/v1/ask")
    def ask():
        try:
            grants = registry.current()
        except identity.RegistryError as exc:
            return refuse(503, "registry_unavailable", agent=exc.code)
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else None
        grant, why = identity.authenticate(grants, token)
        if grant is None:
            return refuse(401, "unauthorized", agent=why)                   # the log keeps the reason; the caller never sees it
        if request.content_length is not None and request.content_length > MAX_BODY:
            return refuse(413, "too_large", grant.name)
        if not request.is_json:
            return refuse(415, "unsupported_media_type", grant.name)
        raw = request.stream.read(MAX_BODY + 1)           # never more than one byte past the limit is read, whatever the headers claim
        if len(raw) > MAX_BODY:
            return refuse(413, "too_large", grant.name)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return refuse(400, "invalid_json", grant.name)
        if not isinstance(body, dict):
            return refuse(400, "bad_arguments", grant.name)
        now = monotonic()
        window = recent[grant.name]
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= grant.rate_per_min:
            return refuse(429, "rate_limited", grant.name)
        window.append(now)
        k = body.get("k", search.DEFAULT_K)
        if "k" in body and (isinstance(k, bool) or not isinstance(k, int) or k > grant.max_k):
            return refuse(400, "bad_k", grant.name)
        if "k" not in body:                                # an omitted k is the grant's cap at most, never the global default above it
            body = {**body, "k": min(search.DEFAULT_K, grant.max_k)}
        include_raw = body.get("include_raw", False)
        if include_raw is True and "all" not in grant.tiers:
            return refuse(403, "tier_not_permitted", grant.name)
        result = tool.call(body, agent=grant.name, entry_point=grant.entry_point, scopes=grant.scopes, shelf_root=shelf_root,
                           embedder=(embedder_factory or _embedder)(), index_path=index_path, log_path=log_path, clock=clock)
        if "error" in result:
            return refuse(_STATUS.get(result["error"], 400), result["error"], grant.name)
        print(f"[ask_service] who={grant.name} status=200", file=sys.stderr, flush=True)
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.memory_ask.service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--shelf-root")
    ap.add_argument("--index")
    ap.add_argument("--registry")
    ap.add_argument("--log", help="the Ask query log file (default: beside the index); production keeps the index read-only")
    args = ap.parse_args(argv)
    if args.shelf_root:
        root = Path(args.shelf_root).expanduser()
    else:
        from core.memory_shelf import config
        root = config.load().shelf_root
    registry_file = Path(args.registry).expanduser() if args.registry else identity.registry_path(root)
    app = create_app(registry_path=registry_file, shelf_root=root, index_path=Path(args.index).expanduser() if args.index else None,
                     log_path=Path(args.log).expanduser() if args.log else None)
    app.run(host=args.host, port=args.port, threaded=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

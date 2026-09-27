"""Standalone local runner for the Guild interaction prototype.

    .venv/bin/python prototype-lab/projects/guild-interaction-prototype/app.py --port 18895

Loopback only, no URL prefix, PROTOTYPE=True, no-op owner guard. Writes nothing.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from flask import Flask, abort, redirect, request, url_for

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from guild_ui import register_guild_ui  # noqa: E402


def create_app(*, sources="live", testing=False, port=18895, queue_path=None, repo_root=None,
               records_db=None, prototype=None, owner_guard=None, write_services=None, url_prefix=""):
    if prototype is None:
        prototype = os.environ.get("GUILD_PROTOTYPE", "1") != "0"
    app = Flask(__name__, static_folder=None)
    app.config.update(TESTING=testing)
    register_guild_ui(app, prototype=prototype, owner_guard=owner_guard, write_services=write_services,
                      url_prefix=url_prefix, sources=sources, queue_path=queue_path, repo_root=repo_root,
                      records_db=records_db if records_db is not None else os.environ.get("GUILD_RECORDS_DB"))
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if testing:
        allowed.add("localhost")

    @app.before_request
    def loopback_only():  # standalone runner only; a host portal applies its own rules
        if request.host not in allowed:
            abort(403)
        origin = request.headers.get("Origin")
        if request.method != "GET" and origin and origin != f"http://{request.host}":
            abort(403)

    @app.route("/")
    def root():
        return redirect(url_for("guild_ui.landing"))

    return app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=18895)
    ap.add_argument("--sources", choices=["live", "sample"], default="live")
    args = ap.parse_args()
    app = create_app(sources=args.sources, port=args.port)
    print(f"Guild interaction prototype · sources={args.sources} · http://127.0.0.1:{args.port}/guild")
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

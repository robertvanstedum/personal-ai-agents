#!/usr/bin/env python3
"""Existing authenticated Ask API, gated on a fresh verified Dev feed run.

``DEV_VAULT_PROFILE=mc`` (#594, container ``minimoi-dev-vault-mc``, port 8772): Master Craftsman's Ask on the same Dev data, opened
only on ``memory-index/memory-mc.sqlite`` (the feed's career-filtered index) with its own registry ``memory-ask/agents-mc.json`` and
query log, and ``require_profile="mc"``: any index without the MC profile and a career-filter digest is refused."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from flask import jsonify
from core.jobs.status import read_status, parse_time
from core.memory_ask.service import create_app


FRESH_S = 600                 # the feed runs every 5 minutes
RUNNING_GRACE_S = 900         # during a run (at most 240 s): the last success may be one interval plus the run old


class NoModel:
    def release(self):
        pass

    def check_pin(self):
        raise RuntimeError("dev_keyword_only_required")


def app_for(data: Path, profile: str | None = None):
    if profile == "mc":
        app = create_app(registry_path=data / "memory-ask/agents-mc.json", shelf_root=data / "memory",
                         index_path=data / "memory-index/memory-mc.sqlite", log_path=data / "memory-ask/queries-mc.jsonl",
                         embedder_factory=NoModel, require_profile="mc")
    elif profile:
        raise SystemExit("unknown DEV_VAULT_PROFILE")
    else:
        app = create_app(registry_path=data / "memory-ask/agents.json", shelf_root=data / "memory",
                         index_path=data / "memory-index/memory.sqlite", log_path=data / "memory-ask/queries.jsonl",
                         embedder_factory=NoModel)

    @app.before_request
    def require_fresh_feed():
        doc = read_status(data / "jobs", "dev-memory-feed.json", "dev-memory-feed")
        if doc and doc.get("state") == "running":
            # A run in progress publishes its indexes only by atomic replace, so the files on disk are still the last
            # successful run's: keep answering from them while that run is fresh (Dev, 14 Nov: a 503 mid-rebuild).
            finished = parse_time(doc.get("last_success_at"))
            limit = RUNNING_GRACE_S
        else:
            finished = parse_time(doc.get("finished_at")) if doc and doc.get("state") == "ok" else None
            limit = FRESH_S
        age = (datetime.now(timezone.utc) - finished).total_seconds() if finished else -1
        if not doc or not 0 <= age <= limit:
            response = jsonify({"error": "feed_not_ready"})
            response.status_code = 503
            response.headers["Cache-Control"] = "no-store"
            return response

    return app


if __name__ == "__main__":
    os.umask(0o077)
    profile = os.environ.get("DEV_VAULT_PROFILE") or None
    app_for(Path(os.environ.get("DEV_VAULT_DATA", "/vault")), profile).run(
        host="0.0.0.0", port=8772 if profile == "mc" else 8771, threaded=False)

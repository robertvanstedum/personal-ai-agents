#!/usr/bin/env python3
"""Existing authenticated Ask API, gated on a fresh verified Dev feed run."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from flask import jsonify
from core.jobs.status import read_status, parse_time
from core.memory_ask.service import create_app


class NoModel:
    def release(self):
        pass

    def check_pin(self):
        raise RuntimeError("dev_keyword_only_required")


def app_for(data: Path):
    app = create_app(registry_path=data / "memory-ask/agents.json", shelf_root=data / "memory",
                     index_path=data / "memory-index/memory.sqlite", log_path=data / "memory-ask/queries.jsonl",
                     embedder_factory=NoModel)

    @app.before_request
    def require_fresh_feed():
        doc = read_status(data / "jobs", "dev-memory-feed.json", "dev-memory-feed")
        finished = parse_time(doc.get("finished_at")) if doc else None
        age = (datetime.now(timezone.utc) - finished).total_seconds() if finished else -1
        if not doc or doc.get("state") != "ok" or not 0 <= age <= 600:
            response = jsonify({"error": "feed_not_ready"})
            response.status_code = 503
            response.headers["Cache-Control"] = "no-store"
            return response

    return app


if __name__ == "__main__":
    os.umask(0o077)
    app_for(Path(os.environ.get("DEV_VAULT_DATA", "/vault"))).run(host="0.0.0.0", port=8771, threaded=False)

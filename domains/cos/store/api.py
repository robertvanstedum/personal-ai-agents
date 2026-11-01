"""The CoS store's internal command and read API, served by the CoS backend only (spec v0.3 section 4.5, N7).

Every process other than the scheduler (the agent's tools, Telegram's in-process CoS) changes the store through
POST /internal/cos-store/v1/commands and reads through GET /internal/cos-store/v1/records/<type>/<id>. The API is
for the compose network only, and authenticated by a service token held in a file and compared in constant time.
It never trusts an X-Minimoi-* header (Spec 165).

It installs only when the store is configured: COS_STORE_DIR (the folder), COS_STORE_ENVIRONMENT (dev, staging or
prod) and COS_STORE_TOKEN_FILE (a non-empty token file). Without all three, nothing is mounted and nothing changes.
"""
from __future__ import annotations

import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from flask import Blueprint, Flask, jsonify, request

from domains.cos.store.writer import MAX_COMMAND_BYTES, StoreRefused, StoreWriter

from domains.cos.store.protocol import PREFIX  # noqa: E402,F401  (re-exported for callers)
_SERVICE_ACTORS = {"agent": {"cos"}, "cos-bot": {"telegram", "cos"},
                   "scheduler-jobs": {"scheduler"}, "supervisor": {"scheduler"}, "portal": {"mc"}}
_READ_TYPES = {"cos.goal", "cos.plan", "cos.obligation", "cos.meeting", "cos.in_flight"}


def _token(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def create_blueprint(writer: StoreWriter, token_file: str = "", *, token_dir: str = "") -> Blueprint:
    bp = Blueprint("cos_store", __name__)

    def allowed_actors() -> set[str]:
        supplied = request.headers.get("Authorization", "")
        if not supplied.startswith("Bearer "):
            return set()
        supplied = supplied[7:].strip()
        if not supplied:
            return set()
        # Ambiguous/duplicate credentials are refused, never unioned into wider authority.
        matches = []
        if token_dir:
            for service, actors in _SERVICE_ACTORS.items():
                expected = _token(str(Path(token_dir) / service))
                if expected and hmac.compare_digest(expected.encode(), supplied.encode()):
                    matches.append(actors)
        elif token_file:
            # Legacy single-token configuration is only the CoS agent grant.
            expected = _token(token_file)
            if expected and hmac.compare_digest(expected.encode(), supplied.encode()):
                matches.append({"cos"})
        return matches[0] if len(matches) == 1 else set()

    def authorized() -> bool:
        return bool(allowed_actors())

    def reader(state: str, *, value: dict | None = None, reason: str | None = None):
        """A reader_response (contracts/cos/v1/read-surface.schema.json). as_of is when the record was true."""
        # A record read live from the store is true as of this read, unless it carries its own as_of.
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        body = {"schema": "minimoi.cos.reader_response/1", "reader": "store", "state": state,
                "as_of": (value.get("as_of") or now) if value else None,
                "data_scope": "personal", "environment": writer.environment}
        if value is not None:
            body["value"] = value
        if reason:
            body["reason"] = reason
        return body

    @bp.post(f"{PREFIX}/commands")
    def commands():
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        if (request.content_length or 0) > MAX_COMMAND_BYTES:
            return jsonify({"error": "too_large"}), 413
        command = request.get_json(silent=True)
        if not isinstance(command, dict):
            return jsonify({"error": "json_object_required"}), 400
        actor = command.get("actor")
        seats = {"cos": {"cos", None}, "telegram": {"cos", None}, "mc": {"mc", None}, "scheduler": {None}}
        origins = {"cos": {"owner_turn", "consultation"}, "telegram": {"owner_turn"},
                   "mc": {"consultation", "owner_turn"}, "scheduler": {"scheduled", "import"}}
        if (not isinstance(actor, str) or actor not in allowed_actors()
                or not (command.get("seat") is None or isinstance(command.get("seat"), str))
                or command.get("seat") not in seats.get(actor, set())
                or not isinstance(command.get("origin"), str)
                or command.get("origin") not in origins.get(actor, set())):
            return jsonify({"schema": "minimoi.cos.store_result/1",
                            "command_id": command.get("command_id", "unknown"),
                            "outcome": "refused", "code": "not_authorized"}), 403
        # The outcome (accepted, conflict, refused, failed) is in the body; the transport succeeded either way.
        return jsonify(writer.submit(command))

    @bp.get(f"{PREFIX}/records/<target_type>/<target_id>")
    def records(target_type: str, target_id: str):
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        if target_type not in _READ_TYPES:
            return jsonify(reader("unavailable", reason="not_granted")), 404
        try:
            doc = writer.get(target_type, target_id)
        except StoreRefused:
            return jsonify(reader("unavailable", reason="not_granted")), 404
        except OSError:
            return jsonify(reader("unavailable", reason="source_unreadable")), 503
        if doc is None:
            return jsonify(reader("unavailable", reason="not_found")), 404
        return jsonify(reader("ok", value=doc))

    @bp.get(f"{PREFIX}/occurrences/<occurrence_id>")
    def occurrence(occurrence_id: str):
        if not authorized():
            return jsonify({"error": "unauthorized"}), 401
        try:
            occ = writer.occurrence(occurrence_id)
        except OSError:
            return jsonify({"error": "source_unreadable"}), 503
        return jsonify({"occurrence_id": occ.occurrence_id, "state": occ.effective, "events": occ.events,
                        "evidence": occ.evidence, "open_claims": sorted(occ.claims - occ.nudged_claims)})

    return bp


def install(app: Flask, *, environ: Mapping[str, str],
            verify_robert_source: Callable[[dict, str], bool] | None = None) -> StoreWriter | None:
    """Mount the API when the store is configured; return the writer, or None when it is not."""
    root = environ.get("COS_STORE_DIR", "").strip()
    environment = environ.get("COS_STORE_ENVIRONMENT", "").strip()
    token_file = environ.get("COS_STORE_TOKEN_FILE", "").strip()
    token_dir = environ.get("COS_STORE_TOKEN_DIR", "").strip()
    configured = any(_token(str(Path(token_dir) / name)) for name in _SERVICE_ACTORS) if token_dir else bool(token_file and _token(token_file))
    if not root or environment not in ("dev", "staging", "prod") or not configured:
        return None
    writer = StoreWriter(root, environment, verify_robert_source=verify_robert_source)
    app.register_blueprint(create_blueprint(writer, token_file, token_dir=token_dir))
    return writer

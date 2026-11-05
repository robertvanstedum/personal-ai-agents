"""Build as first customer (spec v1.1 build plan "Build as first customer"; batch B3D).

Records a build package as a task in a task-service store, without ever holding the lead's credential:
- ``record_package_words`` (called by the lead or the principal, with their own token): the package's words as a
  source event;
- ``open_package`` (called by the builder): the builder's reading as an interpretation adopted under the build grant
  (a real grant must cite the principal's approval), a request, and a task promoted with the specification /
  acceptance lead (Codex) as lead and the builder as holder.
Artifacts, the non-author review and the lead's acceptance use the ordinary task commands from each seat's own
binding (see tests). Nothing here launches a worker or admits work: the harness stays the admission authority.

Gate: synthetic stores only until an independently attributable acceptance exists (see ``gate``). The checkpoint
(CURRENT_HANDOFF.json) stays the recovery record; a failure of this store never blocks the build.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.canonical_json import digest

RUNTIME = {"kind": "deterministic", "model": None, "provider": None, "build": "partnership_tasks.build_tracker/1",
           "config_version": None}


class GateClosed(Exception):
    pass


def gate(svc, checkpoint_path=None, *, reviewed_diff_sha256: str | None = None, synthetic_root_guard=()) -> dict:
    """Synthetic stores only, for now (Codex finding 1, 4 Nov).

    The earlier gate opened a non-synthetic store on a file that said "accepted_by: codex" and named the diff. The
    builder can write that file, so the gate proved nothing about who accepted, and it named one reviewer brand
    although no package needs a specific reviewer's sign-off. Until an acceptance whose provenance the builder cannot
    produce exists (for example, recorded by the reviewing seat's own binding in a store the builder cannot write),
    every non-synthetic store stays closed. ``checkpoint_path`` and ``reviewed_diff_sha256`` are kept for that
    mechanism and are validated strictly, but never open the gate.

    A store labelled synthetic is refused when its real (symlink-resolved) root is, or is inside, a guarded private
    path. The check compares path components, not string prefixes."""
    if reviewed_diff_sha256 is not None and not (isinstance(reviewed_diff_sha256, str)
                                                 and len(reviewed_diff_sha256) == 64
                                                 and all(c in "0123456789abcdef" for c in reviewed_diff_sha256)):
        raise GateClosed("the reviewed diff digest is not a sha256 hex digest")
    if svc.environment != "synthetic":
        raise GateClosed("non-synthetic stores need an independently attributable acceptance, which does not exist yet")
    root = Path(svc.journal.root).resolve()
    for guarded in synthetic_root_guard:
        g = Path(guarded).resolve()
        if root == g or root.is_relative_to(g):
            raise GateClosed("a store labelled synthetic sits under a private store path")
    return {"open": True, "basis": "synthetic store"}


def _cmd(svc, kind, ttype, tid, payload, cid, expected=None, causal=None):
    return {"schema": "minimoi.partnership.command/1", "command_id": cid, "kind": kind,
            "target": {"type": ttype, "id": tid}, "expected_revision": expected, "payload": payload,
            "payload_digest": digest(payload), "environment": svc.environment, "data_scope": "shared:build",
            "basis_ref": None, "causal_parent": causal, "session_id": None, "runtime": RUNTIME, "event_at": None}


def _ok(res, what):
    if res["outcome"] != "accepted":
        raise RuntimeError(f"{what}: {res.get('code')}/{res.get('detail')}")
    return res


def _key(package_id: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in package_id)[:24]
    return f"{safe}-{digest(package_id)[7:15]}"                 # distinct ids for "B3.D" and "B3-D"


def record_package_words(svc, *, lead_token, package_id, words) -> str:
    """The lead (or principal) records what the package is, from its own binding."""
    key = _key(package_id)
    sid = f"SE-pkg-{key}"
    _ok(svc.submit(_cmd(svc, "source_event.record", "source_event", sid,
                        {"source": {"store": "build_checkpoint", "id": package_id, "position": None},
                         "content": words, "correction_of": None}, f"pkg-{key}-se"), lead_token), "source")
    return sid


def open_package(svc, checkpoint_path, *, builder_token, package_id, source_event_id, lead, holder, grant_ref,
                 reviewed_diff_sha256=None):
    gate(svc, checkpoint_path, reviewed_diff_sha256=reviewed_diff_sha256)
    key = _key(package_id)
    sid, iid, rid, tid = source_event_id, f"IN-pkg-{key}", f"RQ-pkg-{key}", f"TK-pkg-{key}"
    se = svc.get("source_event", sid, builder_token)
    if se is None:
        raise RuntimeError("the lead has not recorded this package")
    words = se["content"]
    _ok(svc.submit(_cmd(svc, "interpretation.record", "interpretation", iid,
                        {"source_event_ids": [sid], "correction_event_ids": [],
                         "proposed": {"outcome": words, "scope": "shared:build", "recipients": [lead],
                                      "continuing": True}, "context_refs": [], "supersedes": None},
                        f"pkg-{key}-in"), builder_token), "interpretation")
    interp = svc.get("interpretation", iid, builder_token)
    if interp["disposition"] == "proposed":
        _ok(svc.submit(_cmd(svc, "interpretation.dispose", "interpretation", iid,
                            {"disposition": "adopted", "authority": {"kind": "existing_grant", "ref": grant_ref}},
                            f"pkg-{key}-adopt", expected=interp["revision"]), builder_token), "adopt")
    _ok(svc.submit(_cmd(svc, "request.record", "request", rid,
                        {"source_event_id": sid, "intent_text": words, "interpretation_ref": iid,
                         "responder": holder}, f"pkg-{key}-rq"), builder_token), "request")
    req = svc.get("request", rid, builder_token)
    if req["state"] == "recorded":
        _ok(svc.submit(_cmd(svc, "request.promote", "request", rid,
                            {"task_id": tid, "lead": lead, "holder": holder, "outcome": words,
                             "done_when": "The lead accepts a non-author-reviewed diff.", "return_window": None,
                             "parent_ref": None}, f"pkg-{key}-pr", expected=req["revision"]), builder_token), "promote")
    return tid

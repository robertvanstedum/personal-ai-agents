"""Migration rehearsal: CoS In-flight rows <-> the task service (spec v1.1 M01-M04; batch B3B; acceptance F09).

REHEARSAL ONLY, revised after REVIEW_B3B_INDEPENDENT_2026-11-04. It never touches a live store and performs no
cutover; a cutover needs its own approval (release stop lines). Out of scope here, and named so: fencing the live
legacy writer before a real cutover (M03), and a consistent backup of the *live* legacy store (this rehearsal takes a
digest-checked snapshot of its input copy instead, ``snapshot_legacy``).

Forward (``migrate``), keyed on the legacy item id only (a row's revision is an edition, never a new identity):
- the **migration seat** (a deterministic job, origin scheduler) records the row's title as a source event and its
  reading as an interpretation, and adopts it under the cutover grant that cites the principal's approval
  (``existing_grant``). The principal's own confirmation is not claimed for agent-drafted words;
- each comment and the close note become their own source events, with their provenance kept in the mapping;
- the migration seat records the request; the owner's session promotes open/doing/waiting rows to a task whose lead is the
  reserved marker ``unresolved-lead`` (no binding can hold it): ``with``/``opened_by`` never establish the
  accountable lead (M03). ``with`` becomes the holder; the task waits for the principal;
- done rows become answered requests, dropped rows withdrawn requests (history, not open work, D03);
- every row field is kept in a persisted mapping file next to the store (``migration/mapping.json``), with the
  row's digest; a row whose digest changed since it was migrated is refused as ``row_changed_since_migration``,
  never minted again. Per-row failures are recorded in ``problems`` and do not lose the mapping built so far.

Reverse (``reverse``) writes a rolled-back copy, never into its source: a row whose task has not changed since
migration is written back byte-identical; changes after cutover are replayed (status, outcome, holder) with a
comment saying why; everything the legacy table cannot hold (child tasks, requests never promoted, a lead resolved
after cutover, holders without a legacy seat) is listed in ``unmappable``. Rows written are valid CoS in_flight rows.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from core import durable_files as fsio
from core.canonical_json import digest

RUNTIME = {"kind": "deterministic", "model": None, "provider": None, "build": "partnership_tasks.migration/2",
           "config_version": None}
UNRESOLVED_LEAD = "unresolved-lead"
SEAT = {"robert": "principal", "cos": "cos", "mc": "mc"}
LEGACY = {v: k for k, v in SEAT.items()}


def _cmd(svc, kind, ttype, tid, payload, cid, causal, expected=None):
    return {"schema": "minimoi.partnership.command/1", "command_id": cid, "kind": kind,
            "target": {"type": ttype, "id": tid}, "expected_revision": expected, "payload": payload,
            "payload_digest": digest(payload), "environment": svc.environment, "data_scope": "personal",
            "basis_ref": None, "causal_parent": causal, "session_id": None, "runtime": RUNTIME, "event_at": None}


def snapshot_legacy(source_folder, snapshot_folder) -> dict:
    """Copy the legacy rows once and record each file's digest; ``read_rows`` then reads only the snapshot."""
    src, dst = Path(source_folder), Path(snapshot_folder)
    if dst.exists():
        raise ValueError("snapshot folder must be new")
    shutil.copytree(src, dst)
    manifest = {p.name: digest(json.loads(p.read_text("utf-8"))) for p in sorted(dst.glob("IF-*.json"))}
    (dst / "MANIFEST.json").write_text(json.dumps(manifest, sort_keys=True, indent=1))
    return manifest


def read_rows(snapshot_folder) -> list[dict]:
    folder = Path(snapshot_folder)
    manifest = json.loads((folder / "MANIFEST.json").read_text("utf-8"))
    rows = []
    for name, want in sorted(manifest.items()):
        row = json.loads((folder / name).read_text("utf-8"))
        if digest(row) != want:
            raise ValueError(f"{name} changed after the snapshot")
        rows.append(row)
    return rows


def _ok(res, what):
    if res["outcome"] != "accepted":
        raise RuntimeError(f"{what}: {res.get('code')}/{res.get('detail')}")
    return res


def migrate(svc, *, migration_token: str, owner_token: str, rows: list[dict], grant_ref: dict) -> dict:
    mapping_path = svc.journal.root.parent / f"{svc.journal.root.name}-migration" / "mapping.json"
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    saved = fsio.read_doc(mapping_path)
    if saved is not None and digest(saved["mapping"]) != saved["digest"]:
        raise ValueError("mapping file does not match its digest")
    mapping = {} if saved is None else saved["mapping"]
    problems = []
    for row in rows:
        rid = row.get("item_id", "?")
        try:
            prior = mapping.get(rid)
            if prior is not None and prior["row_digest"] != digest(row):
                problems.append({"item_id": rid, "code": "row_changed_since_migration",
                                 "detail": f"migrated at revision {prior['revision']}, now {row['revision']}"})
                continue
            if prior is not None and prior.get("complete"):
                continue                          # already migrated: never re-pin its baseline (B3B S2-2)
            if row["with"] not in SEAT:
                problems.append({"item_id": rid, "code": "unknown_legacy_seat", "detail": row["with"]})
                continue
            mapping[rid] = dict(_migrate_row(svc, migration_token, owner_token, row, grant_ref), complete=True)
        except (RuntimeError, KeyError) as exc:
            problems.append({"item_id": rid, "code": "row_failed", "detail": str(exc)[:200]})
        finally:
            fsio.write_doc(mapping_path, {"mapping": mapping, "digest": digest(mapping)})   # atomic, after every row
    return {"schema": "minimoi.partnership.migration_rehearsal/2", "rows": len(rows),
            "mapping": [mapping[k] for k in sorted(mapping)], "mapping_file": str(mapping_path),
            "problems": problems, "rehearsal": True}


def _migrate_row(svc, mtok, otok, row, grant_ref) -> dict:
    rid = row["item_id"]
    key = rid[3:]
    sid, iid, qid, tid = f"SE-mig-{key}", f"IN-mig-{key}", f"RQ-mig-{key}", f"TK-mig-{key}"
    alias = {"store": "cos_in_flight", "id": rid, "edition": str(row["revision"]), "digest": digest(row)}
    _ok(svc.submit(_cmd(svc, "source_event.record", "source_event", sid,
                        {"source": {"store": "cos_in_flight", "id": rid, "position": None},
                         "content": row["title"], "correction_of": None}, f"mig-{key}-se", alias), mtok), "source")
    extra = []
    for n, c in enumerate(row.get("comments", []), start=1):
        cid = f"SE-mig-{key}-c{n}"
        _ok(svc.submit(_cmd(svc, "source_event.record", "source_event", cid,
                            {"source": {"store": "cos_in_flight", "id": f"{rid}#comment-{n}", "position": n},
                             "content": c["text"], "correction_of": None}, f"mig-{key}-c{n}", alias), mtok), "comment")
        extra.append({"source_event_id": cid, "author": c["author"], "provenance": c["provenance"], "at": c["at"]})
    if row.get("close_note"):
        cid = f"SE-mig-{key}-close"
        _ok(svc.submit(_cmd(svc, "source_event.record", "source_event", cid,
                            {"source": {"store": "cos_in_flight", "id": f"{rid}#close-note", "position": None},
                             "content": row["close_note"], "correction_of": None}, f"mig-{key}-cn", alias), mtok),
            "close note")
        extra.append({"source_event_id": cid, "kind": "close_note"})
    _ok(svc.submit(_cmd(svc, "interpretation.record", "interpretation", iid,
                        {"source_event_ids": [sid], "correction_event_ids": [],
                         "proposed": {"outcome": row["title"], "scope": "personal",
                                      "recipients": [SEAT[row["with"]]], "continuing": row["status"] != "done"},
                         "context_refs": [alias], "supersedes": None}, f"mig-{key}-in",
                        {"store": "task_service", "id": sid, "edition": None, "digest": None}), mtok), "interpretation")
    interp = svc.get("interpretation", iid, mtok)
    if interp["disposition"] == "proposed":
        _ok(svc.submit(_cmd(svc, "interpretation.dispose", "interpretation", iid,
                            {"disposition": "adopted", "authority": {"kind": "existing_grant", "ref": grant_ref}},
                            f"mig-{key}-adopt", grant_ref, expected=interp["revision"]), mtok), "adopt")
    responder = SEAT[row["with"]]
    _ok(svc.submit(_cmd(svc, "request.record", "request", qid,
                        {"source_event_id": sid, "intent_text": row["title"], "interpretation_ref": iid,
                         "responder": responder}, f"mig-{key}-rq",
                        {"store": "task_service", "id": iid, "edition": None, "digest": None}), mtok), "request")
    created = {"source_event_id": sid, "interpretation_id": iid, "request_id": qid, "task_id": None,
               "extra_source_events": extra}
    req = svc.get("request", qid, otok)
    if row["status"] == "done":
        if req["state"] == "recorded" and row["outcome"] == "dropped":
            _ok(svc.submit(_cmd(svc, "request.set_state", "request", qid,
                                {"state": "withdrawn", "reason": "Dropped in CoS In flight."},
                                f"mig-{key}-drop", alias, expected=req["revision"]), otok), "withdraw")
        elif req["state"] == "recorded":
            _ok(svc.submit(_cmd(svc, "request.set_state", "request", qid, {"state": "active", "reason": None},
                                f"mig-{key}-act", alias, expected=req["revision"]), otok), "activate")
            req = svc.get("request", qid, otok)
        if req["state"] == "active":
            _ok(svc.submit(_cmd(svc, "request.answer", "request", qid,
                                {"result_refs": [alias], "coverage": {"sources": [], "gaps": [
                                    "Migrated history: closed as done in CoS In flight."]},
                                 "fence_generation": req["fence_generation"]},
                                f"mig-{key}-ans", alias, expected=req["revision"]), otok), "answer")
    else:
        if req["state"] == "recorded":
            _ok(svc.submit(_cmd(svc, "request.promote", "request", qid,
                                {"task_id": tid, "lead": UNRESOLVED_LEAD, "holder": responder,
                                 "outcome": row["title"], "done_when": "The principal confirms the outcome.",
                                 "return_window": None, "parent_ref": None},
                                f"mig-{key}-pr", alias, expected=req["revision"]), otok), "promote")
        created["task_id"] = tid
        task = svc.get("task", tid, otok)
        if task["state"] == "submitted":
            principal = svc.bindings.resolve(otok, svc._clock()).principal
            _ok(svc.submit(_cmd(svc, "task.transition", "task", tid,
                                {"to": "waiting", "reason": f"lead unresolved at migration (with={row['with']}, "
                                                            f"opened_by={row['opened_by']}, status={row['status']})",
                                 "waiting": {"reason": "input", "who_can_unblock": principal},
                                 "evidence_refs": [alias], "fence_generation": task["fence_generation"]},
                                f"mig-{key}-wait", alias, expected=task["revision"]), otok), "wait")
        created["migrated_task_revision"] = svc.get("task", tid, otok)["revision"]
    return {"item_id": rid, "revision": row["revision"], "row_digest": digest(row), "row": row, **created}


_STATUS = {"submitted": "open", "working": "doing", "waiting": "waiting", "completed": "done", "failed": "done",
           "canceled": "done", "rejected": "done"}


def reverse(svc, owner_token: str, rows: list[dict], mapping: list[dict], out_folder, *, source_folder, at: str) -> dict:
    """Write a rolled-back copy of the rows into ``out_folder`` (refused if it is the source)."""
    out = Path(out_folder).resolve()
    if out == Path(source_folder).resolve() or out.exists() and any(out.iterdir()):
        raise ValueError("reverse writes only into a new, empty folder that is not its source")
    out.mkdir(parents=True, exist_ok=True)
    by_row = {m["item_id"]: m for m in mapping}
    written, unmappable, known_tasks, known_requests = [], [], set(), set()
    for row in rows:
        m = by_row.get(row["item_id"])
        new = dict(row)
        if m:
            known_requests.add(m["request_id"])
        if m and m.get("task_id"):
            known_tasks.add(m["task_id"])
            t = svc.get("task", m["task_id"], owner_token)
            if t["revision"] != m["migrated_task_revision"]:
                status = _STATUS[t["state"]]
                note = []
                if status != row["status"]:
                    new.update(status=status, last_moved_at=at)
                    note.append(f"status {row['status']} -> {status} after cutover ({t['state']}: {t['reason']})"
                                + ("; the legacy table has no 'failed', recorded as dropped" if t["state"] == "failed"
                                   else ""))
                    if status == "done":
                        new.update(outcome="done" if t["state"] == "completed" else "dropped", closed_at=at)
                if t["holder"] in LEGACY and LEGACY[t["holder"]] != row["with"]:
                    new["with"] = LEGACY[t["holder"]]
                    note.append(f"holder -> {t['holder']}")
                elif t["holder"] not in LEGACY:
                    unmappable.append({"item_id": row["item_id"], "why": f"holder {t['holder']} has no legacy seat"})
                if t["lead"] != UNRESOLVED_LEAD:
                    unmappable.append({"item_id": row["item_id"], "why": f"lead resolved after cutover: {t['lead']} "
                                                                         "(the legacy table has no lead)"})
                if note:
                    new["revision"] = row["revision"] + 1
                    new["comments"] = row["comments"] + [{"at": at, "author": "cos", "provenance": "agent_draft",
                                                          "text": "Rolled back from the task service: " + "; ".join(note)}]
            for child in _children(svc, owner_token, m["task_id"]):
                unmappable.append({"task_id": child, "why": f"delegated step of {m['task_id']}; "
                                                            "the legacy table has no children"})
        original = Path(source_folder) / f"{row['item_id']}.json"
        if new == row and original.exists():
            shutil.copyfile(original, out / f"{row['item_id']}.json")          # untouched: the exact original bytes
        else:
            (out / f"{row['item_id']}.json").write_text(json.dumps(new, ensure_ascii=False, sort_keys=True))
        written.append(row["item_id"])
    env = rows[0]["environment"] if rows else "dev"
    for p in sorted((svc.journal.root / "tasks").glob("TK-*.json")):
        t = svc.get("task", p.stem, owner_token)
        if t is None or t["task_id"] in known_tasks or t["parent_ref"] in known_tasks:
            continue
        if t["parent_ref"] is not None:
            unmappable.append({"task_id": t["task_id"], "why": "delegated step; the legacy table has no children"})
            continue
        known_requests.add(t["request_ref"])
        if t["holder"] not in LEGACY:
            unmappable.append({"task_id": t["task_id"], "why": f"created after cutover with holder {t['holder']}, "
                                                                "which the legacy table cannot hold"})
            continue
        req = svc.get("request", t["request_ref"], owner_token)
        status = _STATUS[t["state"]]
        title = t["outcome"] if len(t["outcome"]) <= 200 else t["outcome"][:197] + "..."
        row = {"schema": "minimoi.cos.in_flight/1", "data_scope": "personal", "environment": env,
               "item_id": "IF-post-" + digest(t["task_id"])[7:23], "revision": 1, "kind": "action",
               "title": title, "title_provenance": "agent_draft", "priority": 2, "status": status,
               "with": LEGACY[t["holder"]], "opened_at": t["created_at"], "last_moved_at": at,
               "closed_at": at if status == "done" else None,
               "outcome": None if status != "done" else ("done" if t["state"] == "completed" else "dropped"),
               "source": None, "opened_by": LEGACY.get(req["provenance"]["actor_seat"], "cos") if req else "cos",
               "comments": [{"at": at, "author": "cos", "provenance": "agent_draft",
                             "text": f"Created after cutover as {t['task_id']}"
                                     + ("; title shortened" if title != t["outcome"] else "")}]}
        (out / f"{row['item_id']}.json").write_text(json.dumps(row, ensure_ascii=False, sort_keys=True))
        written.append(row["item_id"])
    for p in sorted((svc.journal.root / "requests").glob("RQ-*.json")):
        r = svc.get("request", p.stem, owner_token)
        if r and r["request_id"] not in known_requests and r["task_ref"] is None:
            unmappable.append({"request_id": r["request_id"], "why": f"request created after cutover, "
                                                                     f"never promoted (state {r['state']})"})
    return {"written": written, "unmappable": unmappable, "rehearsal": True}


def _children(svc, token, task_id) -> list[str]:
    out = []
    for p in sorted((svc.journal.root / "tasks").glob("TK-*.json")):
        t = svc.get("task", p.stem, token)
        if t and t["parent_ref"] == task_id:
            out.append(t["task_id"])
    return out

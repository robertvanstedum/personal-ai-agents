"""Verified copies of the owner's chosen files for Master Craftsman to OPEN (P1-B, 12 Oct).

The relay to Master Craftsman carries text only, so a picture (or the original of a long document) can reach him only as a file his own read tools open. For a turn
that carries files, the PORTAL copies them with the same ``stage_files`` the agents' evidence uses (hash checked against the hash stored at upload, MANIFEST, quota,
private modes, no symlinks) into ``<ledger>/evidence/<id>/`` and tells him the absolute path on the host. That is all this module ever claims: ``staged``. It is never
a receipt that he opened or saw anything; only his own tool trace could show that.

Off unless ``MINIMOI_MC_FILES_HOST_ROOT`` names, as an absolute path on the HOST, the same ledger folder the portal writes (the portal sees its container path, not the
host's). Unset or malformed: nothing is staged and the turn is exactly what it was. The root is server configuration only; nothing from a request, a file name or a
model reply can change it."""
from __future__ import annotations

import hashlib
import json
import os
import shutil

from core.agent_requests.evidence_files import EvidenceRefused, FOLDER
from .agent_evidence import ALL_MAX_BYTES, FILES_MAX, FILE_MAX_BYTES, MANIFEST_RESERVE, _read
from core.agent_requests import evidence_files as _core

ENV = "MINIMOI_MC_FILES_HOST_ROOT"
LEDGER_NAME = "agent_requests"          # the folder the portal's ledger lives in, on the host as in the container
DOMAIN = "mc-evidence-v1"


def host_root(environ=None) -> str | None:
    """A host path from the mount's admitted configuration. No mapping means off."""
    value = ({} if environ is None else environ).get(ENV)
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    if not os.path.isabs(value) or os.path.normpath(value) != value or os.path.basename(value) != LEDGER_NAME:
        return None
    return value


def request_id(channel: str, scope: str, principal: str, turn_id: str, wanted) -> str:
    """``r-<24 hex>``: domain-separated, bound to the channel, the conversation or room, the owner, the SERVER's turn id and the exact files chosen."""
    blob = json.dumps([DOMAIN, channel, scope, principal, turn_id, [list(w) for w in wanted]], separators=(",", ":"), ensure_ascii=True)
    return "r-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def wants_copy(kind: str, item: dict) -> bool:
    """A picture always (nothing else can show it); a document only when its saved text is not the whole file."""
    return kind == "image" or bool(item.get("truncated"))


class Staged(dict):
    """{file id: {name, path, sha256, bytes}} plus ``root`` and ``rid`` so a refused dispatch can take its own copy away again."""
    root: str | None = None
    rid: str | None = None


def discard(staged) -> None:
    """Remove the folder this turn's staging created (a turn that was staged and then not sent). Never touches anything but ``evidence/<its own id>``."""
    root, rid = getattr(staged, "root", None), getattr(staged, "rid", None)
    if root and rid and _core.RID_RE.fullmatch(rid):
        target = os.path.join(root, FOLDER, rid)
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target, ignore_errors=True)


def picks(conv: dict, ids: list[str]) -> list[tuple[str, str]]:
    """[(kind, id)] among the chosen ids that need a copy, in the owner's order. Ids not in this conversation are left to ``turn_files.build`` to refuse.
    Never cuts the list: stage() refuses one that is over the limit, so no file is silently left out."""
    docs = {d.get("id"): d for d in conv.get("documents") or []}
    pics = {a.get("asset_id"): a for a in conv.get("attachments") or []}
    out = []
    for fid in ids:
        if fid in docs and wants_copy("doc", docs[fid]):
            out.append(("doc", fid))
        elif fid in pics:
            out.append(("image", fid))
    return out


def stage(ledger_root: str, host: str, *, channel: str, scope: str, principal: str, owner: str, turn_id: str, chosen, store, media) -> Staged:
    """Copy ``chosen`` for this turn. Returns {file id: {"name", "path", "sha256", "bytes"}} with ABSOLUTE host paths built from the verified manifest's own leaf names.
    Every call first reads the CURRENT originals (access, still present, still the uploaded hash), even when a finished copy already exists, and a reused copy must match
    those bytes: a source deleted, trashed or changed after the first staging is never served from the old copy. Raises EvidenceRefused (nothing is claimed staged then)."""
    out = Staged()
    if not chosen:
        return out
    if len(chosen) > FILES_MAX:
        raise EvidenceRefused("too_many", f"At most {FILES_MAX} files can be copied for Master Craftsman in one message, so none was.")
    rid = request_id(channel, scope, principal, turn_id, chosen)
    gathered = []
    for kind, fid in chosen:
        name, data, _sha = _read(kind, fid, store=store, media=media, cid=scope, principal=principal, owner=owner)
        if len(data) > FILE_MAX_BYTES:
            raise EvidenceRefused("too_big", "One of those files is over 8 MB, so none was copied.")
        gathered.append((kind, fid, name, data))
    current = {fid: hashlib.sha256(data).hexdigest() for _k, fid, _n, data in gathered}

    manifest = _core.stage_files(ledger_root, rid, [[k, f] for k, f in chosen], lambda: gathered, scope, file_max=FILE_MAX_BYTES, all_max=ALL_MAX_BYTES,
                                 reserve=MANIFEST_RESERVE)
    out.root, out.rid = ledger_root, rid
    for row in manifest["files"]:
        leaf = os.path.basename(row["path"])
        if os.path.dirname(row["path"]) != f"{FOLDER}/{rid}" or leaf != os.path.basename(leaf):
            raise EvidenceRefused("unavailable", "A copy was not where it should be, so it was not offered.")
        if row["sha256"] != current.get(row["id"]):
            raise EvidenceRefused("changed", "A file changed after it was first copied, so the old copy was not offered.")
        out[row["id"]] = {"name": row["name"], "path": os.path.join(host, FOLDER, rid, leaf), "sha256": row["sha256"], "bytes": row["bytes"]}
    return out

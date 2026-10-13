"""#297: files the owner attached in a conversation, handed to Claude Code or Codex as a verified copy.

``ASK codex +files: <title>`` (this message's files) or ``ASK codex +files:latest: <title>`` (the newest file in the conversation). The PORTAL, not MC, resolves the ids against the
owner's own conversation, copies each original into ``<ledger root>/evidence/<request id>/`` (a folder both agents already use for requests; no new mount), verifies the copy
against the hash stored at upload, and writes a MANIFEST. The request text then names the path and the hash. If anything fails, the ask is not sent and the owner is told why.
States stay honest: this module only ever reports ``staged``; delivery and opening are the agent's to confirm."""
from __future__ import annotations

import hashlib
import os
import re

from .conversations import ConversationNotFound, ConversationStoreUnavailable, DocumentGone, ORIGINAL_READ_MAX
from .media import _safe_path

FILES_MAX = 5
FILE_MAX_BYTES = 8 * 1024 * 1024
ALL_MAX_BYTES = 200 * 1024 * 1024
MANIFEST_RESERVE = 4096          # each request also writes a small manifest; its space is reserved with the files


from core.agent_requests import evidence_files as _core
from core.agent_requests.evidence_files import EvidenceRefused, FOLDER, MANIFEST, RID_RE   # noqa: F401  (one shared definition)

_used = _core.used                                  # the portal's tests and callers use these names


def choose(conv: dict, mode: str, turn_ids: list | None) -> list[tuple[str, str]]:
    """[(kind, id)] for ``mode`` ("turn" or "latest") from THIS conversation only; EvidenceRefused when there is nothing to send."""
    docs = {d.get("id"): d for d in conv.get("documents") or []}
    pics = {a.get("asset_id"): a for a in conv.get("attachments") or []}
    if mode == "turn":
        picked = list(turn_ids or [])
        if not picked:
            raise EvidenceRefused("none_attached", "No file was attached to that message, so nothing was put to the agent.")
    else:
        newest = sorted([(d.get("added_at") or "", "doc", i) for i, d in docs.items()] + [(a.get("added_at") or "", "image", i) for i, a in pics.items()])
        if not newest:
            raise EvidenceRefused("none_in_conversation", "There is no file in this conversation yet, so nothing was put to the agent.")
        return [(newest[-1][1], newest[-1][2])]
    out = []
    for fid in dict.fromkeys(picked):
        if fid in docs:
            out.append(("doc", fid))
        elif fid in pics:
            out.append(("image", fid))
        else:
            raise EvidenceRefused("not_found", "One of those files is not in this conversation, so nothing was put to the agent.")
    if len(out) > FILES_MAX:
        raise EvidenceRefused("too_many", f"At most {FILES_MAX} files can go to an agent in one request.")
    return out


def _read(kind, fid, *, store, media, cid, principal, owner):
    if kind == "doc":
        try:
            entry, data = store.read_original(cid, principal, fid)
        except ConversationNotFound:
            raise EvidenceRefused("not_found", "That conversation was not found.") from None
        except DocumentGone:
            raise EvidenceRefused("no_original", "No original was kept for one of those files, so it cannot be sent.") from None
        except ConversationStoreUnavailable:
            raise EvidenceRefused("unavailable", "The files could not be read right now.") from None
        return entry.get("name"), data, entry.get("original_sha256") or hashlib.sha256(data).hexdigest()
    if media is None:
        raise EvidenceRefused("unavailable", "The picture store is not available, so a picture cannot be sent.")
    asset = media.get(owner, fid)
    if not asset or asset.get("state") != "active" or not asset.get("storage_key"):
        raise EvidenceRefused("gone", "A picture is no longer available (removed or in the Trash), so it cannot be sent.")
    try:
        with open(_safe_path(media.root, asset["storage_key"]), "rb") as f:
            data = f.read(FILE_MAX_BYTES + 1)
    except (OSError, ValueError):
        raise EvidenceRefused("gone", "A picture file could not be read, so it cannot be sent.") from None
    if hashlib.sha256(data).hexdigest() != asset.get("sha256"):
        raise EvidenceRefused("changed", "A picture no longer matches what was uploaded, so it was not sent.")
    return asset.get("title") or fid, data, asset["sha256"]


def stage(root: str, rid: str, chosen, *, store, media, cid, principal, owner) -> dict:
    """Copy the owner's chosen conversation files for request ``rid`` (see core.agent_requests.evidence_files.stage_files for the guarantees)."""
    def gather():
        out = []
        for kind, fid in chosen:
            name, data, _sha = _read(kind, fid, store=store, media=media, cid=cid, principal=principal, owner=owner)
            if kind == "doc" and len(data) > ORIGINAL_READ_MAX:
                raise EvidenceRefused("too_big", "One of those files is over 8 MB, so it was not sent.")
            out.append((kind, fid, name, data))
        return out
    return _core.stage_files(root, rid, [[k, f] for k, f in chosen], gather, cid, file_max=FILE_MAX_BYTES, all_max=ALL_MAX_BYTES, reserve=MANIFEST_RESERVE)


evidence_lines = _core.evidence_lines

"""Canonical Board request digest shared by trusted clients and the application.

This performs text canonicalization only, never ownership or link admission.
"""
import hashlib
import json
from domains.guild.board_text import clean


def payload(capability, body, target=None):
    if capability == "guild.board.operation.undo":
        return {"undo_of": target, "expected_revision": body["expected_revision"]}
    changes = dict(body if capability == "guild.board.note.create" else body["changes"])
    scrubbed = False
    if "text" in changes:
        text = clean(changes["text"])
        changes["text"], scrubbed = text.text, text.scrubbed
    result = {"changes": changes, "text_scrubbed": scrubbed}
    if capability == "guild.board.note.update":
        result.update(target=int(target), expected_revision=body["expected_revision"])
    return result


def digest(capability, canonical):
    raw = json.dumps({"capability": capability, "payload": canonical}, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()

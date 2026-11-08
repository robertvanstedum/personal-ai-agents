"""The Build Log item a task was approved for (MC front door spec A9). Robert's word is for one exact revision: the task's
interpretation names it, and every later step checks it is still the current one. It fails closed: a bound item that can't be
read is a refusal, never a pass."""
from __future__ import annotations

STORE = "build_log"
CHANGED_WORDS = ("Build Log #{id} has changed since Robert confirmed it, so this work can't go ahead on his earlier word. "
                 "Nothing was started. He needs to look at it again and say build it.")
UNREADABLE_WORDS = "The Build Log can't be read to check #{id} is unchanged, so nothing was started."
GONE_WORDS = "Build Log #{id} is no longer there, so nothing was started."


def reference(item: dict) -> dict:
    from domains.guild.queue_store import content_digest
    revision = content_digest(item)
    return {"store": STORE, "id": str(item["id"]), "edition": revision, "digest": "sha256:" + revision}


def bound(client, task) -> dict | None:
    """The Build Log reference on this task's interpretation, or None when the task isn't tied to an item."""
    if not task.get("interpretation_ref"):
        return None
    interpretation = client.get("interpretation", task["interpretation_ref"])
    if interpretation is None:                         # it names one we can't find: that isn't the same as naming no item
        raise LookupError("interpretation not found")
    return next((r for r in interpretation.get("context_refs") or () if r.get("store") == STORE), None)


def problem(ref, read_items) -> tuple[str, str] | None:
    """(code, words) when the bound item is not exactly the revision approved, else None."""
    if ref is None:
        return None
    if read_items is None:
        return "item_unreadable", UNREADABLE_WORDS.format(id=ref["id"])
    from domains.guild.queue_store import content_digest
    try:
        items = read_items()
    except Exception:
        return "item_unreadable", UNREADABLE_WORDS.format(id=ref["id"])
    item = next((i for i in items if isinstance(i, dict) and str(i.get("id")) == ref["id"]), None)
    if item is None:
        return "item_gone", GONE_WORDS.format(id=ref["id"])
    if content_digest(item) != ref["edition"]:
        return "item_changed", CHANGED_WORDS.format(id=ref["id"])
    return None

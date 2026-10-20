"""Board operations for MC's action adapter: list, add, move to the bin, restore.

A thin wrapper over the existing ``FloorStores`` / ``BoardMixin`` rules (unchanged): the same
idempotent transactions, the same 280-character limit and payment-detail scrub as the website,
and the same recoverable bin (nothing here purges). It maps the store's outcomes onto the action
result vocabulary and reads the note back so a caller reports what is actually on the Board.

    done             the change happened now and the read-back confirms it
    already_applied  the same operation id was seen before, or the note was already in that state
    conflict         the operation id was reused for a different change; nothing changed
    not_found        no such note on this floor
    refused          invalid input (empty, too long, unknown label); nothing changed
    unknown          the Board store could not be reached; the effect is unverified, so read back
                     before any retry with the same operation id

The operation id must be assigned by the application per intended effect (harness v0.3.1 §5),
never by the model; it becomes the store's idempotency key, so a retry cannot add a second note.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .stores import MASTER_CRAFTSMAN, POSTIT_MAX, Author, FloorStoreUnavailable  # before .board (cycle)
from .board import LABELS
from .payment_scrub import scrub

_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,99}$")
_KEY_PREFIX = "mc-op:"


@dataclass(frozen=True)
class BoardOutcome:
    status: str
    code: str = ""
    postit: dict | None = None
    verified_state: dict = field(default_factory=dict)


def _key(operation_id: str) -> str:
    if not isinstance(operation_id, str) or not _OPERATION_ID.match(operation_id):
        raise ValueError("operation_id_invalid")
    return _KEY_PREFIX + operation_id


MAX_POSTIT_ID = 2**63 - 1


def _postit_id(value) -> int | None:
    """An actual positive integer, or a canonical decimal string of one. Floats (1.9 must never
    become note 1), booleans, signs, spaces and out-of-range values are refused before the store
    is touched."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value):
        number = int(value)
    else:
        return None
    return number if 1 <= number <= MAX_POSTIT_ID else None


def _state(postit: dict | None) -> dict:
    if not postit:
        return {}
    return {"postit_id": postit.get("id"), "binned": postit.get("binned_at") is not None,
            "label": postit.get("label"), "version": postit.get("version")}


def list_board(stores, *, include_bin: bool = False, bin_limit: int = 50) -> dict:
    """Active post-its (and optionally the bin). An unavailable store is reported, never empty."""
    active = stores.list_postits()
    result = {"available": bool(active.ok),
              "postits": (active.data or {}).get("postits", []) if active.ok else [],
              "bin_total": (active.data or {}).get("bin_total") if active.ok else None}
    if include_bin:
        binned = stores.list_bin(limit=bin_limit)
        result["bin"] = (binned.data or {}).get("bin", []) if binned.ok else None
        result["available"] = result["available"] and bool(binned.ok)
    return result


def add(stores, text: str, *, operation_id: str, label: str | None = None,
        author: Author = MASTER_CRAFTSMAN) -> BoardOutcome:
    try:
        key = _key(operation_id)
    except ValueError:
        return BoardOutcome("refused", "operation_id_invalid")
    if not isinstance(text, str) or not text.strip():
        return BoardOutcome("refused", "text_empty")
    if len(text) > POSTIT_MAX:
        return BoardOutcome("refused", "text_too_long")
    clean = scrub(text.strip())
    if len(clean) > POSTIT_MAX:
        return BoardOutcome("refused", "text_too_long_after_scrub")
    if label is not None and label not in LABELS:
        return BoardOutcome("refused", "label_invalid")
    try:
        result = stores.add_postit(clean, author, idempotency_key=key, label=label)
    except FloorStoreUnavailable:
        return BoardOutcome("unknown", "board_unavailable")
    if result.outcome == "idempotency_mismatch":
        return BoardOutcome("conflict", "operation_id_reused")
    if result.outcome != "added" or not result.value:
        return BoardOutcome("unknown", f"unexpected_{result.outcome}")
    return BoardOutcome("already_applied" if result.repeated else "done", "added", result.value,
                        _state(result.value))


def _move(stores, postit_id, operation_id, *, to_bin: bool, author: Author) -> BoardOutcome:
    try:
        key = _key(operation_id)
    except ValueError:
        return BoardOutcome("refused", "operation_id_invalid")
    pid = _postit_id(postit_id)
    if pid is None:
        return BoardOutcome("refused", "postit_id_invalid")
    try:
        method = stores.bin_postit if to_bin else stores.restore_postit
        result = method(pid, author, idempotency_key=key)
    except FloorStoreUnavailable:
        return BoardOutcome("unknown", "board_unavailable")
    done, already = ("binned", "already_binned") if to_bin else ("restored", "already_active")
    if result.outcome == "idempotency_mismatch":
        return BoardOutcome("conflict", "operation_id_reused")
    if result.outcome == "not_found":
        return BoardOutcome("not_found", "postit_not_found")
    if result.outcome == done:
        return BoardOutcome("already_applied" if result.repeated else "done", done, result.value,
                            _state(result.value))
    if result.outcome == already:
        return BoardOutcome("already_applied", already, result.value, _state(result.value))
    return BoardOutcome("unknown", f"unexpected_{result.outcome}")


def bin_postit(stores, postit_id, *, operation_id: str, author: Author = MASTER_CRAFTSMAN) -> BoardOutcome:
    """Move to the recoverable bin. Never a purge."""
    return _move(stores, postit_id, operation_id, to_bin=True, author=author)


def restore_postit(stores, postit_id, *, operation_id: str, author: Author = MASTER_CRAFTSMAN) -> BoardOutcome:
    return _move(stores, postit_id, operation_id, to_bin=False, author=author)

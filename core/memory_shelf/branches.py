"""The message tree of a claude.ai conversation (format-only; amendment R2/R3).

claude.ai stores a conversation as a **tree**: every message names its parent
(``parent_message_uuid``), an edit or a retry adds a sibling, and the export
list interleaves branches, so adjacent list items are often not parent-linked.
This module turns that tree into a deterministic, lossless sequence:

* **Main line** = the path from the root to the *most recently created leaf*
  (ties: the later one in list order). It is the conversation as it ended.
* Every message not on the main line is **kept** in a *branch segment*: a linear
  path that starts at a child of an earlier message and runs to the most recently
  created leaf below it. Segments are listed after the main line, in tree order:
  forks are visited along the main line from the root, and a segment's own forks
  are visited right after it (depth first); siblings by list order.
* A message whose parent is not in the export is an **orphan**: it starts its own
  segment with reason ``orphan_parent`` (so does anything unreachable, e.g. a
  parent cycle). A second root starts a segment with reason ``extra_root``.

Nothing is dropped: ``plan`` asserts that every message lands in exactly one path.
An export whose messages carry no ``parent_message_uuid`` at all (the older
layout) is read as one linear chain in list order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

NO_PARENT = {None, "", "00000000-0000-4000-8000-000000000000"}     # the root message's parent
ORPHAN_PARENT, EXTRA_ROOT = "orphan_parent", "extra_root"


@dataclass(frozen=True)
class Segment:
    indexes: tuple[int, ...]          # message indexes (positions in the export list), root to leaf
    fork: int | None                  # the message this segment hangs off; None for orphans and extra roots
    reason: str | None = None


def _ts(msg: dict) -> float:
    text = msg.get("created_at")
    if isinstance(text, dict):                                   # {"$date": ...}
        text = text.get("$date")
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text) / 1000 if text > 1e11 else float(text)          # epoch seconds or milliseconds
    if isinstance(text, str):
        try:
            parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass
    return float("-inf")


def plan(messages: list[dict], prefer_leaf: int | None = None) -> tuple[list[int], list[Segment]]:
    """``(main line indexes, branch segments)`` covering every message exactly once.

    Messages are dicts with ``uuid``, ``parent_message_uuid`` and ``created_at`` (a format that names them
    differently maps its fields first). ``prefer_leaf`` is the index of a message the source itself names as
    the end of the conversation (Grok's ``leaf_response_id``): when its parent chain reaches a root, the main
    line is that path; otherwise the newest-leaf rule applies.
    """
    n = len(messages)
    if not any(isinstance(m, dict) and "parent_message_uuid" in m for m in messages):
        return list(range(n)), []
    by_uuid: dict[str, int] = {}
    for i, msg in enumerate(messages):
        uid = msg.get("uuid")
        if isinstance(uid, str) and uid and uid not in by_uuid:
            by_uuid[uid] = i
    parent: list[int | None] = []                  # index, None for a root, -1 for an orphan
    for i, msg in enumerate(messages):
        pid = msg.get("parent_message_uuid")
        if not isinstance(pid, str) or pid in NO_PARENT:
            parent.append(None)
        elif pid in by_uuid and by_uuid[pid] != i:
            parent.append(by_uuid[pid])
        else:
            parent.append(-1)
    children: list[list[int]] = [[] for _ in range(n)]
    for i, p in enumerate(parent):
        if p is not None and p >= 0:
            children[p].append(i)
    key = [(_ts(m), i) for i, m in enumerate(messages)]
    visited: set[int] = set()

    def path_from(start: int) -> list[int]:
        """``start`` down to the most recently created leaf below it (never re-entering a visited message)."""
        seen, stack, best = {start}, [start], start
        leaves = []
        while stack:
            node = stack.pop()
            kids = [c for c in children[node] if c not in visited and c not in seen]
            if not kids:
                leaves.append(node)
            for c in kids:
                seen.add(c)
                stack.append(c)
        best = max(leaves or [start], key=lambda i: key[i])
        out = [best]
        while out[-1] != start:
            out.append(parent[out[-1]])
        return out[::-1]

    roots = [i for i, p in enumerate(parent) if p is None]
    main: list[int] = []
    best_key = None
    if prefer_leaf is not None:
        chain, node = [prefer_leaf], prefer_leaf
        while parent[node] is not None and parent[node] >= 0 and parent[node] not in chain:
            node = parent[node]
            chain.append(node)
        if parent[node] is None:
            main = chain[::-1]
            roots = []                                       # decided; the loop below only picks when undecided
    for root in roots:                              # the root whose subtree holds the newest leaf owns the main line
        candidate = path_from(root)
        if best_key is None or key[candidate[-1]] > best_key:
            main, best_key = candidate, key[candidate[-1]]
    visited.update(main)
    segments: list[Segment] = []

    def forks(path: tuple[int, ...]):
        for node in path:
            for child in children[node]:
                yield node, child

    def drain(first_path: tuple[int, ...]) -> None:
        stack = [forks(first_path)]
        while stack:
            try:
                node, child = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            if child in visited:
                continue
            seg = tuple(path_from(child))
            visited.update(seg)
            segments.append(Segment(seg, node))
            stack.append(forks(seg))
    drain(tuple(main))
    for i in range(n):                              # extra roots, orphans, and anything unreachable, in list order
        if i in visited:
            continue
        if parent[i] is None:
            reason = EXTRA_ROOT
        else:
            reason = ORPHAN_PARENT
        seg = tuple(path_from(i))
        visited.update(seg)
        segments.append(Segment(seg, None, reason))
        drain(seg)
    covered = list(main) + [i for s in segments for i in s.indexes]
    assert sorted(covered) == list(range(n)), "branch plan lost or duplicated a message"
    return main, segments


__all__ = ["plan", "Segment", "ORPHAN_PARENT", "EXTRA_ROOT"]

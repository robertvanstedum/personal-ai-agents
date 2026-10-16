"""Fidelity F2, sampled (amendment R4): did the shelf keep exactly what the source said?

For a record, the retained source is re-read **fresh** and compared, as an ordered
sequence ``(ordinal, speaker, sha256(text))``, against the turns stored on the shelf
(the record body) and against its edition file. Both sides are scrubbed the same way
first, so an intentional scrub is never reported as damage. Containment checks are
not enough: this finds **dropped**, **duplicated**, **reordered**, **misattributed**
and **unexpected** (altered or inserted) turns, plus **edition_mismatch** (record
body and edition file disagree).

Diffs name positions and 12-character hash prefixes only: never turn text, so a diff
can leak neither excluded nor sanitized content. A source that no longer matches the
record's ``source_hash`` (the session kept growing, a newer edition is pending) or is
gone is **skipped with a reason**, not failed: it cannot be compared fairly.

The re-parse reuses the same format parsers as the writer, on freshly read bytes; it
checks the render, store and edition layers, not the parsers themselves (the parsers
have their own fixtures).
"""
from __future__ import annotations

import json
import hashlib
import os
import stat
import zipfile
import random
from collections import Counter
from pathlib import Path

from core.memory_shelf import editions, inbox, record, render, rooms, sessions, watchers
from core.memory_shelf.config import Config

SOURCE_CHANGED, SOURCE_MISSING, NOT_RETAINED, UNREADABLE = "source_changed", "source_missing", "not_retained", "unreadable"
NORMALIZER_CHANGED = "normalizer_changed"          # the record was read by an older parser; re-mediation will bring it up to date


def diff(expected: list[tuple], actual: list[tuple], label: str = "") -> list[dict]:
    """Findings between two ordered ``(ordinal, speaker, sha)`` lists. Empty when identical."""
    if expected == actual:
        return []
    prefix = f"{label}:" if label else ""
    out: list[dict] = []

    def add(kind, position, e=None, a=None):
        out.append({"kind": prefix + kind, "position": position, "expected": e and e[:12], "actual": a and a[:12]})
    if [e[1:] for e in expected] == [a[1:] for a in actual]:       # same turns in the same order, numbered differently
        add("ordinals", 0)
        return out
    if len(expected) == len(actual):
        pairs = list(zip(expected, actual))
        mis = [(e, a) for e, a in pairs if e[2] == a[2] and e[1] != a[1]]
        rest = [(e, a) for e, a in pairs if e != a and not (e[2] == a[2] and e[1] != a[1])]
        if mis and not rest:
            for e, a in mis:
                add("misattributed", e[0], e[2], a[2])
            return out
        if rest and not mis and Counter((e[1], e[2]) for e, _ in rest) == Counter((a[1], a[2]) for _, a in rest):
            for e, a in rest:
                add("reordered", e[0], e[2], a[2])
            return out
    exp_c, act_c = Counter((e[1], e[2]) for e in expected), Counter((a[1], a[2]) for a in actual)
    seen: Counter = Counter()
    for e in expected:
        seen[(e[1], e[2])] += 1
        if seen[(e[1], e[2])] > act_c[(e[1], e[2])]:
            add("dropped", e[0], e[2])
    seen, exp_hashes = Counter(), {e[2] for e in expected}
    for a in actual:
        seen[(a[1], a[2])] += 1
        if seen[(a[1], a[2])] > exp_c[(a[1], a[2])]:
            add("duplicated" if a[2] in exp_hashes else "unexpected", a[0], None, a[2])
    return out


def shelf_turns(main: Path) -> list[tuple[int, str, str]]:
    _, body = record.load(record.read(main))
    return render.ordered_turns(render.parse_body(body))


def edition_turns(folder: Path, number: int) -> list[tuple[int, str, str]] | None:
    for ed in editions.list_editions(folder):
        if ed.number == number:
            rows = []
            with open(ed.path, "rb") as handle:
                handle.readline()                                  # header
                for line in handle:
                    row = json.loads(line)
                    if row.get("type") in ("note", "ref"):               # kept apart from the turns; hashed like them
                        if render.sha256_text(str(row.get("text", ""))) != row["sha256"]:
                            return rows + [(-1, "?", "0" * 64)]
                        continue
                    if render.sha256_text(row["text"]) != row["sha256"]:
                        return rows + [(-1, "?", "0" * 64)]        # the edition's own hash line disagrees with its text
                    who = (row.get("attrs") or {}).get("who")
                    rows.append((row["ordinal"], f"{row['speaker']}:{who}" if who else row["speaker"], row["sha256"]))
            return rows
    return None


SOURCE_TOO_LARGE = "source_too_large"
REPLAY_LIMIT_INVALID = "source_replay_limit_invalid"


class ReplayHeld(RuntimeError):
    pass


def _limit():
    try:
        value = int(os.environ.get("MOI_CAPTURE_MAX_SOURCE_BYTES", str(watchers.DEFAULT_MAX_SOURCE_BYTES)))
    except (ValueError, TypeError):
        raise ReplayHeld(REPLAY_LIMIT_INVALID) from None
    # Replay does not implement the explicit selected-source isolated-worker override.
    if value <= 0 or value > watchers.DEFAULT_MAX_SOURCE_BYTES:
        raise ReplayHeld(REPLAY_LIMIT_INVALID)
    return value


def _bounded_hash(path, limit):
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ReplayHeld(SOURCE_TOO_LARGE)
    digest, total = hashlib.sha256(), 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(min(1 << 20, limit - total + 1))
            if not block:
                break
            total += len(block)
            if total > limit:
                raise ReplayHeld(SOURCE_TOO_LARGE)
            digest.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ReplayHeld(SOURCE_CHANGED)
    return digest.hexdigest(), after


class _ReplayCapped(inbox._Capped):
    def __call__(self, size):
        try:
            return super().__call__(min(size, self.limit - self.total + 1))
        except inbox.Refusal:
            if self.total > self.limit:
                raise ReplayHeld(SOURCE_TOO_LARGE) from None
            raise


def _inbox_selected(path, fallback, members, limit):
    """One existing streaming parser pass per export, retaining only sampled members."""
    kind, _ = inbox.classify(path.name, path)
    if kind in ("canary", "paste"):
        # read_bytes in these existing parsers is avoided so growth stays capped.
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise ReplayHeld(SOURCE_TOO_LARGE)
        if kind == "paste" and len(raw) > inbox.MAX_PASTE_BYTES:
            raise inbox.Refusal(inbox.codes.TOO_LARGE)
        item = inbox.parse_canary(raw) if kind == "canary" else inbox.parse_paste(raw, name=path.name, fallback=fallback)
        return {None: item}
    if kind not in ("claude-ai-export", "grok-export"):
        raise ValueError("unsupported_source")
    result = {}
    def collect(read):
        events = inbox._grok_stream(read) if kind == "grok-export" else inbox._stream(read)
        seen = False
        for event in events:
            if kind == "grok-export":
                if event[0] == "start":
                    seen = True
                if event[0] != "item":
                    if event[0] != "start" and event[1] == "conversations":
                        raise inbox.Refusal(inbox.codes.UNKNOWN_SCHEMA)
                    continue
                conv = event[1]
                head = conv.get("conversation") if isinstance(conv, dict) else None
                member = head.get("id") if isinstance(head, dict) else None
                if isinstance(member, str) and member in members and member not in result:
                    result[member] = inbox.parse_grok_conversation(inbox.check_grok_conversation(conv))
            else:
                conv = event
                member = conv.get("uuid") if isinstance(conv, dict) else None
                if isinstance(member, str) and member in members and member not in result:
                    result[member] = inbox.parse_conversation(inbox.check_conversation(conv))
        if kind == "grok-export" and not seen:
            raise inbox.Refusal(inbox.codes.UNKNOWN_SCHEMA)
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            if kind == "grok-export":
                infos = [i for i in archive.infolist() if inbox._safe_member(i) and inbox._basename(i.filename) == inbox.GROK_BACKEND]
                if not infos:
                    raise ValueError("source_member_missing")
                info = infos[0]
            else:
                try:
                    info = inbox._export_member(archive)
                except inbox.Refusal as exc:
                    if exc.code == inbox.codes.TOO_LARGE:
                        raise ReplayHeld(SOURCE_TOO_LARGE) from None
                    raise
            if info.file_size > limit:
                raise ReplayHeld(SOURCE_TOO_LARGE)
            with archive.open(info) as handle:
                collect(_ReplayCapped(handle, limit))
    else:
        with path.open("rb") as handle:
            collect(_ReplayCapped(handle, limit))
    return result


def load_source(cfg: Config, meta: dict, *, expected_hash=None, cache=None, members=None):
    """Bounded current-parser source replay. No historical bytes are changed.
    A per-sample cache shares only sampled conversations from a single export pass.
    """
    ret = meta.get("retained") or {}
    try:
        limit = _limit()
        if ret.get("kind") == "source-file":
            source = cfg.sources.get(ret.get("source"))
            path = source and Path(source.root) / ret["rel"]
            if not path or not path.is_file():
                return None, SOURCE_MISSING
            sha, before = _bounded_hash(path, limit)
            if expected_hash is not None and sha != expected_hash:
                return None, SOURCE_CHANGED
            with path.open("rb") as handle:
                parsed = watchers.PARSERS[source.kind](watchers._bounded_lines(handle, limit))
        elif ret.get("kind") == "rooms-bundle":
            source = cfg.sources.get(ret.get("source"))
            path = source and Path(source.root) / ret["rel"]
            if not path or not path.is_dir():
                return None, SOURCE_MISSING
            if sum((path / name).stat().st_size for name in ("manifest.json", *rooms.PAYLOAD)) > limit:
                raise ReplayHeld(SOURCE_TOO_LARGE)
            try:
                read = rooms.read_bundle(path, source, max_total_bytes=limit)
            except rooms.Refusal as exc:
                if exc.code == inbox.codes.TOO_LARGE:
                    raise ReplayHeld(SOURCE_TOO_LARGE) from None
                raise
            parsed, sha = read.parsed, read.sha256
            return (parsed, sha), None
        elif ret.get("kind") == "inbox-file":
            path = Path(cfg.inbox_root) / ret["rel"]
            if not path.is_file():
                return None, SOURCE_MISSING
            from datetime import datetime, timezone
            key = str(path)
            entry = cache.get(key) if cache is not None else None
            if entry is None:
                sha, before = _bounded_hash(path, limit)
                if expected_hash is not None and sha != expected_hash:
                    return None, SOURCE_CHANGED
                fallback = datetime.fromtimestamp(before.st_mtime, tz=timezone.utc)
                selected = members.get(key, {ret.get("member")}) if members is not None else {ret.get("member")}
                selected = _inbox_selected(path, fallback, selected, limit)
                after = path.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    return None, SOURCE_CHANGED
                entry = selected, before
                if cache is not None:
                    cache[key] = entry
            items, before = entry
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                return None, SOURCE_CHANGED
            item = items.get(ret.get("member"))
            if item is None:
                return None, SOURCE_MISSING
            # Export source hashes identify the selected conversation, not the outer zip.
            return (item.parsed, item.source_sha256), None
        else:
            return None, NOT_RETAINED
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            return None, SOURCE_CHANGED
    except ReplayHeld as exc:
        return None, str(exc)
    except watchers.SourceTooLarge:
        return None, SOURCE_TOO_LARGE
    except (OSError, inbox.Refusal, rooms.Refusal, ValueError, KeyError, zipfile.BadZipFile):
        return None, UNREADABLE
    return (parsed, sha), None


def _source_turns(cfg: Config, meta: dict, *, cache=None, members=None):
    provider = str(meta.get("source", "")).split(":", 1)[0]
    if (meta.get("normalized") or {}).get("normalizer", sessions.DEFAULT_NORMALIZER) != sessions.NORMALIZER_VERSION.get(provider, sessions.DEFAULT_NORMALIZER):
        return None, NORMALIZER_CHANGED
    got, reason = load_source(cfg, meta, expected_hash=meta.get("source_hash") if (meta.get("retained") or {}).get("kind") == "source-file" else None, cache=cache, members=members)
    if got is None:
        return None, reason
    parsed, sha = got
    if (meta.get("normalized") or {}).get("normalizer", sessions.DEFAULT_NORMALIZER) != parsed.normalizer:
        return None, NORMALIZER_CHANGED
    clean, _ = render.scrub_parsed(parsed)
    return (render.ordered_turns(clean.turns), sha), None


def check_record(cfg: Config, main: Path, *, cache=None, members=None) -> dict:
    meta, _ = record.load(record.read(main))
    base = {"record": meta["id"]}
    stored = shelf_turns(main)
    edition = edition_turns(main.parent, int(meta.get("edition", 1)))
    findings = diff(stored, edition or [], "edition_mismatch") if edition != stored else []
    if edition is None and not findings:
        findings = [{"kind": "edition_mismatch:missing", "position": 0, "expected": None, "actual": None}]
    storage = {"status": "failed" if findings else "ok"}
    got, reason = _source_turns(cfg, meta, cache=cache, members=members)
    if got is None:
        if findings:
            return {**base, "status": "failed", "findings": findings, "storage": storage, "source_replay": {"status": "skipped", "reason": reason}}
        return {**base, "status": "skipped", "reason": reason, "storage": storage, "source_replay": {"status": "skipped", "reason": reason}}
    expected, sha = got
    if sha != meta.get("source_hash"):
        return {**base, "status": "failed" if findings else "skipped", "reason": SOURCE_CHANGED, "findings": findings, "storage": storage, "source_replay": {"status": "skipped", "reason": SOURCE_CHANGED}}
    findings += diff(expected, stored)
    return {**base, "status": "failed" if findings else "ok", "findings": findings, "storage": storage, "source_replay": {"status": "checked"}}


def sample(shelf, cfg: Config, n: int = 5, *, canary: bool = False, seed: int | None = None) -> dict:
    """One global sample, with independently attributable source counts. Read-only."""
    entries = shelf.list_records(canary=canary)
    chosen = random.Random(seed).sample(entries, min(n, len(entries)))
    from core.memory_shelf.shelf import read_front_matter
    members = {}
    for entry in chosen:
        meta = read_front_matter(shelf.main_path(entry));ret = meta.get("retained") or {}
        if ret.get("kind") == "inbox-file":
            members.setdefault(str(Path(cfg.inbox_root) / ret["rel"]), set()).add(ret.get("member"))
    cache = {};results = [];by_source = {}
    for entry in chosen:
        result = check_record(cfg, shelf.main_path(entry), cache=cache, members=members)
        results.append(result)
        row = by_source.setdefault(entry["provider"], {"sample_size": 0, "checked": 0, "ok": 0, "failed": 0, "skipped": {}, "storage": {"checked": 0, "ok": 0, "failed": 0}})
        row["sample_size"] += 1
        if result["status"] == "skipped":
            reason = result["reason"];row["skipped"][reason] = row["skipped"].get(reason, 0) + 1
        else:
            row["checked"] += 1;row[result["status"]] += 1
        row["storage"]["checked"] += 1;row["storage"][result["storage"]["status"]] += 1
    skipped = Counter(r["reason"] for r in results if r["status"] == "skipped")
    return {"canary": canary, "checked": sum(r["status"] != "skipped" for r in results),
            "ok": sum(r["status"] == "ok" for r in results), "failed": sum(r["status"] == "failed" for r in results),
            "skipped": dict(skipped), "by_source": by_source, "results": [r for r in results if r["status"] != "ok"]}

"""Read-only resolver for the ingress service (Codex): payload bytes by ``payload_ref``.

The ingress verifies sha256(bytes) against the event's payload_digest. Only names the standing
worker itself writes are accepted: ``<cycle key>.brief`` (the brief's text) and
``learning.<24 hex>.<n>`` (a learning event's canonical payload). No paths, URLs or other spool
areas.

Strictly read-only (it runs on a read-only mount): no directory creation, chmod or write. Every
component is opened with O_NOFOLLOW relative to the previous one (no symlinks anywhere), the
record must be a regular file within the size bound, and its JSON must have the expected shape.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

_BRIEF = re.compile(r"^(?:dev|prod)\.\d{4}-\d{2}-\d{2}\.[A-Za-z0-9_-]{1,40}\.morning\.brief$")
_LEARNING = re.compile(r"^learning\.[0-9a-f]{24}\.\d{1,3}$")
MAX_BYTES = 256 * 1024
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _read_record(state_root: str | Path, name: str) -> dict:
    root_fd = os.open(state_root, _DIR_FLAGS)
    try:
        dir_fd = os.open("delivery", _DIR_FLAGS, dir_fd=root_fd)
        try:
            try:
                fd = os.open(f"{name}.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
            except FileNotFoundError:
                raise LookupError("payload_not_found") from None
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                    raise LookupError("payload_invalid")
                raw = os.read(fd, MAX_BYTES + 1)
            finally:
                os.close(fd)
        finally:
            os.close(dir_fd)
    finally:
        os.close(root_fd)
    try:
        doc = json.loads(raw)
    except ValueError:
        raise LookupError("payload_invalid") from None
    if not isinstance(doc, dict):
        raise LookupError("payload_invalid")
    return doc


def load_payload(state_root: str | Path, payload_ref: str) -> bytes:
    if not isinstance(payload_ref, str):
        raise ValueError("payload_ref_invalid")
    if _BRIEF.match(payload_ref):
        record = _read_record(state_root, payload_ref)
        if not isinstance(record.get("text"), str):
            raise LookupError("payload_invalid")
        return record["text"].encode("utf-8")
    if _LEARNING.match(payload_ref):
        record = _read_record(state_root, payload_ref)
        if not isinstance(record.get("payload"), str):
            raise LookupError("payload_invalid")
        return record["payload"].encode("utf-8")
    raise ValueError("payload_ref_invalid")


def load_brief_text(state_root: str | Path, payload_ref: str) -> bytes:
    """Kept for the existing caller; briefs only."""
    if not isinstance(payload_ref, str) or not _BRIEF.match(payload_ref):
        raise ValueError("payload_ref_invalid")
    return load_payload(state_root, payload_ref)

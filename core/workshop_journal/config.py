"""The workshop's ``config.json``, read as carefully as the journal (v0.6 section 13).

Opened no-follow from the workshop folder, a regular single-link private file owned by this user, bounded in size, strict JSON,
version 1, known sections only. It holds names and references, never credentials. An absent file is an empty configuration; a
damaged or oversized one is refused, never half-read.
"""
from __future__ import annotations

import os

from core.workshop_journal import fsutil, strictjson
from core.workshop_journal.errors import Missing, UnsafeRoot

MAX_BYTES = 64 * 1024
SECTIONS = ("v", "routes", "model_profiles")


class BadConfig(ValueError):
    pass


def read(workshop_dir: str) -> dict:
    try:
        dir_fd = os.open(workshop_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    except OSError:
        raise BadConfig("workshop_folder_unreadable") from None
    try:
        try:
            fd = fsutil.open_file(dir_fd, "config.json", os.O_RDONLY)
        except Missing:
            return {}
        except UnsafeRoot:
            raise BadConfig("config_not_a_private_regular_file") from None
        try:
            if os.fstat(fd).st_size > MAX_BYTES:
                raise BadConfig("config_too_large")
            raw = fsutil.read_all(fd)
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)
    try:
        doc = strictjson.loads(raw)
    except strictjson.StrictJSONError:
        raise BadConfig("config_unreadable") from None
    if not isinstance(doc, dict) or set(doc) - set(SECTIONS):
        raise BadConfig("unknown_config_field")
    if doc.get("v", 1) != 1:
        raise BadConfig("unknown_config_version")
    return doc

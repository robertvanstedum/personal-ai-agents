"""Turning a domain's activity writer on (spec v0.3 section 4.3).

Off unless the service is given its own activity folder and its environment:
- ``MINIMOI_ACTIVITY_DIR``: this domain's folder, an absolute path (production: ``data/activity/<domain>/`` mounted at
  ``/app/data/activity/<domain>``, contracts/cos/v1/access-matrix.json);
- ``MINIMOI_ACTIVITY_ENVIRONMENT``: ``dev``, ``staging`` or ``prod``.

The language domains also need ``MINIMOI_ACTIVITY_OWNER_ID``, the owner's auth id. They serve several learners, and
this record is Robert's personal domain only, so without the owner named they publish nothing rather than guess.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

from core.activity.writer import ActivityWriter

_ID = re.compile(r"[^A-Za-z0-9._:@-]+")


def writer_from_environ(*, domain: str, metric: str, service: str,
                        environ: Mapping[str, str] = os.environ) -> ActivityWriter | None:
    folder = (environ.get("MINIMOI_ACTIVITY_DIR") or "").strip()
    environment = (environ.get("MINIMOI_ACTIVITY_ENVIRONMENT") or "").strip()
    if not folder or not os.path.isabs(folder) or environment not in ("dev", "staging", "prod"):
        return None
    return ActivityWriter(folder, domain=domain, metric=metric, environment=environment, service=service)


def owner_id(environ: Mapping[str, str] = os.environ) -> int | None:
    value = (environ.get("MINIMOI_ACTIVITY_OWNER_ID") or "").strip()
    return int(value) if value.isdigit() else None


def safe_id(*parts: object) -> str:
    """An event or ref id from source ids: the contract's id characters only, at most 128."""
    text = _ID.sub("-", "-".join(str(p) for p in parts)).strip("-._:@")
    return (text or "x")[:128]


def one_line(text: str) -> str:
    return " ".join(str(text).split())[:160] or "activity"

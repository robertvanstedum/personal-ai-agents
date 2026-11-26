"""The owner's Voice choice for one partner, kept by MiniMoi, not by the browser (#659, #661).

One value per owner per partner: ``{"voice": "openai"|"xai", "revision": n, "updated_at": iso}``. The model is never
stored: it is derived from the voice (core.model_routes.picker), so a cross-provider pair cannot exist. Phone, desktop
and car read the same file, and a new conversation starts on it.

File-first: ``<root>/partner-settings/<sha256(owner)>/<partner>.json``, replaced atomically (core.durable_files) under an
exclusive lock on a sibling file, so a read-check-write never loses another device's change. Each container keeps only
its own partner's file (MC in the portal, CoS in the CoS service).
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core import durable_files as fsio

ROOT_ENV = "MINIMOI_PARTNER_SETTINGS_DIR"
PARTNERS = ("mc", "cos")
VOICES = ("openai", "xai")


class SettingsConflict(Exception):
    """The page saw an older revision: another device changed the choice first."""

    def __init__(self, revision: int):
        super().__init__("partner settings revision conflict")
        self.revision = revision


def root_for(default: Path | str | None) -> Path | None:
    """The host's root: the env var when set, else the host's own durable data folder; None where it has neither."""
    value = os.environ.get(ROOT_ENV) or default
    return Path(value) if value else None


class PartnerSettings:
    def __init__(self, root: Path | str, partner: str):
        if partner not in PARTNERS:
            raise ValueError(f"unknown partner {partner!r}")
        self.root, self.partner = Path(root) / "partner-settings", partner

    def _path(self, owner: str) -> Path:
        return self.root / hashlib.sha256(str(owner).encode("utf-8")).hexdigest() / f"{self.partner}.json"

    @contextmanager
    def _locked(self, owner: str):
        path = self._path(owner)
        fsio.ensure_dir(path.parent)
        fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield path
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def get(self, owner: str) -> dict:
        """The saved choice, or ``{"voice": None, "revision": 0}`` when the owner never chose here."""
        doc = fsio.read_doc(self._path(owner)) or {}
        voice = doc.get("voice") if doc.get("voice") in VOICES else None
        return {"voice": voice, "revision": int(doc.get("revision") or 0), "updated_at": doc.get("updated_at")}

    def set_voice(self, owner: str, voice: str, revision: int) -> dict:
        if voice not in VOICES:
            raise ValueError("unknown voice")
        with self._locked(owner) as path:
            current = int((fsio.read_doc(path) or {}).get("revision") or 0)
            if revision != current:
                raise SettingsConflict(current)
            doc = {"voice": voice, "revision": current + 1,
                   "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            fsio.write_doc(path, doc)
        return doc

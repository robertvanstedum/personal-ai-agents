"""Scrub every copied file (agent-memory v0.4 §3.2, §3.4).

The credential guard and the payment scrub both run on each file. What is
stored and hashed is the SCRUBBED bytes; when they differ from the source, the
file is a "sanitized derivative" and the manifest keeps ``source_sha256`` as
provenance. Nothing here logs or returns a matched value.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from utils import credential_scrub, payment_scrub

SANITIZERS = ("payment_scrub_v1", "credential_guard_v1")


@dataclass(frozen=True)
class StoredFile:
    """One file as it will be stored, with its manifest facts (§3.2)."""
    path: str
    data: bytes
    sha256: str
    size: int
    sanitized: bool
    source_sha256: str | None
    sanitizers: tuple[str, ...]
    redactions: int

    def manifest_entry(self) -> dict:
        return {"path": self.path, "sha256": self.sha256, "size": self.size,
                "sanitized": self.sanitized, "source_sha256": self.source_sha256,
                "sanitizers": list(self.sanitizers), "redactions": self.redactions}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scrub_text(text: str) -> tuple[str, int]:
    """(scrubbed text, number of replacements made). Credentials first, then payment details."""
    cred_before = text.count(credential_scrub.REMOVED)
    pay_before = text.count(payment_scrub.REMOVED)
    out, _ = credential_scrub.scrub(text)
    out = payment_scrub.scrub(out)
    added = (max(0, out.count(credential_scrub.REMOVED) - cred_before)
             + max(0, out.count(payment_scrub.REMOVED) - pay_before))
    if out != text and added == 0:
        added = 1
    return out, added


def scrub_file(path: str, data: bytes) -> StoredFile:
    """Scrub one UTF-8 file's bytes. ``data`` must already have passed ``selection``."""
    text = data.decode("utf-8")
    out, redactions = scrub_text(text)
    stored = out.encode("utf-8")
    changed = stored != data
    return StoredFile(path=path, data=stored, sha256=sha256_hex(stored), size=len(stored),
                      sanitized=changed, source_sha256=sha256_hex(data) if changed else None,
                      sanitizers=SANITIZERS, redactions=redactions if changed else 0)

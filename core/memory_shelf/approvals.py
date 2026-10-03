"""The only code that builds approvals (amendment R2): called from the owner's entry points.

* ``approve_record``: appends ``approved-direct`` (or ``approved-under-mandate``) to a record.
* ``approve_source``: writes the approval record that switches a source on after its dry run.
* ``designate_record``: Robert confirms a designation candidate; adds ``designated-curated`` only.

Every function here needs an ``OwnerAuthority`` token, which only ``moi``
(``core/memory_shelf/cli.py``, after an interactive confirmation) creates. Nothing
that reads conversation text imports this module or ``OwnerAuthority``; a test
walks the package to prove it.

**Source approval** binds to the source's *fingerprint* (kind, root, ``never_copy``),
not to a listing: new sessions appear every hour, but changing the root or the
``never_copy`` list invalidates the approval (``stale``) until a new dry run is
approved. A source with no approval record never captures (``not_approved``);
its first run only writes the dry-run listing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import codes, fsio, review, ulid
from core.memory_shelf import events as ev
from core.memory_shelf import record
from core.memory_shelf.events import OwnerAuthority

NO_DRY_RUN, STALE, APPROVED = "no_dry_run", "stale", "approved"


class ApprovalRefused(ValueError):
    """Fixed-text refusal; never quotes anything from a record."""


def _need(authority: object, *entry_points: str) -> OwnerAuthority:
    if not isinstance(authority, OwnerAuthority) or authority.entry_point not in entry_points:
        raise ApprovalRefused("an owner action is required")
    return authority


def dry_run_path(shelf, name: str) -> Path:
    return Path(shelf.status_dir) / "dry-runs" / f"{name}.json"


def approval_path(shelf, name: str) -> Path:
    return Path(shelf.status_dir) / "approvals" / f"{name}.json"


def source_status(shelf, name: str, fingerprint: str) -> str:
    """``approved`` | ``not_approved`` | ``no_dry_run`` | ``stale``. Fail closed on anything unreadable."""
    if not dry_run_path(shelf, name).is_file():
        return NO_DRY_RUN
    doc = fsio.read_json(approval_path(shelf, name))
    if not isinstance(doc, dict) or doc.get("approved") is not True:
        return codes.NOT_APPROVED
    return APPROVED if doc.get("fingerprint") == fingerprint else STALE


def approve_source(shelf, name: str, fingerprint: str, authority: OwnerAuthority, *,
                   now: datetime | None = None) -> dict:
    """Approve a source's dry run. Refused unless a dry-run listing for this exact config exists."""
    _need(authority, "moi-approve")
    listing = fsio.read_json(dry_run_path(shelf, name))
    if not isinstance(listing, dict):
        raise ApprovalRefused("no dry-run listing to approve")
    if listing.get("fingerprint") != fingerprint:
        raise ApprovalRefused("the dry-run listing is for a different configuration")
    doc = {"approved": True, "source": name, "fingerprint": fingerprint, "listing_sha256": listing.get("listing_sha256"),
           "approved_at": ev.stamp(now), "by": authority.owner, "via": authority.entry_point}
    fsio.write_json(approval_path(shelf, name), doc)
    return doc


def approve_record(shelf, record_id: str, authority: OwnerAuthority, *, under: str | None = None,
                   now: datetime | None = None) -> dict:
    """Append the owner's approval event to one record; every earlier event stays as it was."""
    _need(authority, "moi-approve")
    path = shelf.find_record(record_id) if ulid.is_ulid(record_id) else None
    if path is None or not path.is_file():
        raise ApprovalRefused("no such record")
    try:
        if under:
            event = ev.make_event("approved-under-mandate", authority.owner, now=now, authority=authority, mandate=under)
        else:
            event = ev.make_event("approved-direct", authority.owner, now=now, authority=authority)
    except ev.EventRefused as exc:
        raise ApprovalRefused(str(exc)) from exc
    record.append_event(path, event)
    return event


def designate_record(shelf, record_id: str, authority: OwnerAuthority, *, now: datetime | None = None) -> dict:
    """Robert confirms a pasted marker. Adds ``designated-curated``; never an approval."""
    _need(authority, "moi-approve")
    path = shelf.find_record(record_id) if ulid.is_ulid(record_id) else None
    if path is None or not path.is_file():
        raise ApprovalRefused("no such record")
    event = ev.make_event("designated-curated", authority.owner, now=now, via="moi-designate")
    record.append_event(path, event)
    review.resolve(shelf, "designation-candidate", ulid.short(record_id), "confirmed")
    return event

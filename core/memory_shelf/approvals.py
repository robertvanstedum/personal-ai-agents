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
    with shelf.lock():
        record.append_event(path, event)
    return event


def designate_record(shelf, record_id: str, authority: OwnerAuthority, *, now: datetime | None = None) -> dict:
    """Robert confirms a pasted marker. Adds ``designated-curated``; never an approval."""
    _need(authority, "moi-approve")
    path = shelf.find_record(record_id) if ulid.is_ulid(record_id) else None
    if path is None or not path.is_file():
        raise ApprovalRefused("no such record")
    event = ev.make_event("designated-curated", authority.owner, now=now, via="moi-designate")
    with shelf.lock():
        record.append_event(path, event)
    review.resolve(shelf, "designation-candidate", ulid.short(record_id), "confirmed")
    return event

# R1a uses the existing event list and owner authority, not a second journal.
_REF_KEYS = {'record_id','edition','edition_sha256','record_sha256','lines','range_sha256'}
_MAX_REFERENCE_BYTES = 4 << 20

def _annotation_record(shelf, rid):
    from core.vault_t1 import reader
    if not ulid.is_ulid(rid): raise ApprovalRefused('annotation_record_invalid')
    path=shelf.find_record(rid)
    if path is None: raise ApprovalRefused('annotation_record_missing')
    path=Path(path).absolute()
    if not path.is_relative_to(Path(shelf.root).absolute()): raise ApprovalRefused('annotation_path_refused')
    try:
        raw=reader.read_path('/',list(path.parts[1:]),_MAX_REFERENCE_BYTES)
        meta,body=record.load(raw.decode('utf-8'))
    except (reader.VaultError,UnicodeError,record.InvalidRecord):
        raise ApprovalRefused('annotation_source_unreadable') from None
    if meta['id']!=rid: raise ApprovalRefused('annotation_identity_mismatch')
    return path,meta,raw


def _annotation_ref_shape(ref):
    import re
    if not isinstance(ref,dict) or set(ref)!=_REF_KEYS: raise ApprovalRefused('annotation_reference_invalid')
    if type(ref['edition']) is not int or ref['edition']<1: raise ApprovalRefused('annotation_reference_invalid')
    for name in ('edition_sha256','record_sha256','range_sha256'):
        if not isinstance(ref[name],str) or not re.fullmatch('[0-9a-f]{64}',ref[name]): raise ApprovalRefused('annotation_reference_invalid')
    if not isinstance(ref['record_id'],str) or not ulid.is_ulid(ref['record_id']):raise ApprovalRefused('annotation_reference_invalid')
    lines=ref['lines']
    if not isinstance(lines,list) or len(lines)!=2 or any(type(n) is not int for n in lines) or not 1<=lines[0]<=lines[1] or lines[1]-lines[0]>=100:
        raise ApprovalRefused('annotation_range_invalid')


def _annotation_ref(shelf, ref):
    import hashlib,re
    from core.vault_t1 import reader
    from core.memory_shelf import editions
    _annotation_ref_shape(ref)
    lines=ref['lines']
    path,meta,raw=_annotation_record(shelf,ref['record_id'])
    if hashlib.sha256(raw).hexdigest()!=ref['record_sha256'] or meta.get('edition')!=ref['edition'] or meta.get('edition_hash')!=ref['edition_sha256']:
        raise ApprovalRefused('annotation_stale_source')
    candidates=[e for e in editions.list_editions(path.parent) if e.number==ref['edition']]
    if len(candidates)!=1: raise ApprovalRefused('annotation_edition_missing')
    edpath=candidates[0].path.absolute()
    try:edraw=reader.read_path('/',list(edpath.parts[1:]),_MAX_REFERENCE_BYTES)
    except reader.VaultError:raise ApprovalRefused('annotation_edition_unreadable') from None
    if hashlib.sha256(edraw).hexdigest()!=ref['edition_sha256']: raise ApprovalRefused('annotation_edition_changed')
    try:split=edraw.decode('utf-8').splitlines(keepends=True)
    except UnicodeError:raise ApprovalRefused('annotation_binary_range_unsupported') from None
    if lines[1]>len(split):raise ApprovalRefused('annotation_range_invalid')
    piece=''.join(split[lines[0]-1:lines[1]]).encode('utf-8')
    if hashlib.sha256(piece).hexdigest()!=ref['range_sha256']:raise ApprovalRefused('annotation_range_changed')
    return path,meta


def supersede_record(shelf, source_ref, successor_ref, authority, *, request_id, now=None):
    """Owner-confirmed link; source editions unchanged, no successor approval.

    References name current immutable UTF-8 original edition lines (max100),
    not mutable projection line positions. Duplicate request returns its exact
    committed event even though that event changed the main projection hash.
    All read/validate/append operations use the existing shelf writer lock.
    """
    import re
    _need(authority,'moi-approve','guild-owner-control')
    if authority.owner!='robert' or not isinstance(request_id,str) or not re.fullmatch('[0-9a-f]{32}',request_id):
        raise ApprovalRefused('annotation_owner_or_request_refused')
    if not isinstance(source_ref,dict) or not isinstance(successor_ref,dict):raise ApprovalRefused('annotation_reference_invalid')
    source=source_ref.get('record_id');successor=successor_ref.get('record_id')
    if source==successor:raise ApprovalRefused('annotation_self_link')
    with shelf.lock(wait=5):
        path,meta,_=_annotation_record(shelf,source)
        previous=[e for e in meta.get('events',[]) if e.get('request_id')==request_id]
        if previous:
            if len(previous)!=1 or previous[0].get('kind')!='superseded' or previous[0].get('source_ref')!=source_ref or previous[0].get('successor_ref')!=successor_ref:
                raise ApprovalRefused('annotation_idempotency_mismatch')
            try:fsio.fsync_dir(path.parent)
            except OSError:raise fsio.PublicationUncertain(5,'publication_uncertain') from None
            return {'event':previous[0],'repeated':True,'index_refresh_required':True}
        if shelf.headroom():raise ApprovalRefused('annotation_disk_low')
        path,meta=_annotation_ref(shelf,source_ref)
        _,new=_annotation_ref(shelf,successor_ref)
        if (meta.get('scope'),meta.get('tier'),meta.get('kind')=='canary')!=(new.get('scope'),new.get('tier'),new.get('kind')=='canary'):
            raise ApprovalRefused('annotation_access_boundary')
        visited={source};cursor=successor
        for _ in range(64):
            if not isinstance(cursor,str) or not ulid.is_ulid(cursor):raise ApprovalRefused('annotation_chain_unreadable')
            if cursor in visited:raise ApprovalRefused('annotation_cycle')
            visited.add(cursor);_,candidate,_=_annotation_record(shelf,cursor)
            try:derived=__import__('core.memory_shelf.weight',fromlist=['derive']).derive(candidate)
            except (KeyError,TypeError):raise ApprovalRefused('annotation_chain_unreadable') from None
            if derived['weight']!='superseded':break
            cursor=derived['successor']
        else:raise ApprovalRefused('annotation_chain_limit')
        event=ev.make_event('superseded',authority.owner,now=now,authority=authority,successor=successor,
                            source_ref=source_ref,successor_ref=successor_ref,request_id=request_id)
        try:ev.check_events([*meta.get('events',[]),event])
        except ev.EventRefused:raise ApprovalRefused('annotation_timestamp_invalid') from None
        # Existing publication error is uncertain, not a rollback; repeat with
        # same request checks the committed event and never appends a second one.
        record.append_event(path,event)
        return {'event':event,'repeated':False,'index_refresh_required':True}


def annotation_reference_evidence(shelf, ref):
    """Read-only original edition pointer; caller checks scope/tier first.

    A historical original edition can survive a newer current edition. Its
    range must still hash exactly; this does not make that record approved.
    """
    import hashlib
    from core.memory_shelf import editions
    from core.vault_t1 import reader
    _annotation_ref_shape(ref)
    path,meta,_=_annotation_record(shelf,ref['record_id'])
    found=[e for e in editions.list_editions(path.parent) if e.number==ref['edition']]
    if len(found)!=1:raise ApprovalRefused('annotation_edition_missing')
    target=found[0].path.absolute()
    try:data=reader.read_path('/',list(target.parts[1:]),_MAX_REFERENCE_BYTES)
    except reader.VaultError:raise ApprovalRefused('annotation_edition_unreadable') from None
    if hashlib.sha256(data).hexdigest()!=ref['edition_sha256']:raise ApprovalRefused('annotation_edition_changed')
    lines=ref['lines']
    if not isinstance(lines,list) or len(lines)!=2 or any(type(n) is not int for n in lines) or not 1<=lines[0]<=lines[1] or lines[1]-lines[0]>=100:raise ApprovalRefused('annotation_range_invalid')
    try:text=data.decode('utf-8').splitlines(keepends=True)
    except UnicodeError:raise ApprovalRefused('annotation_binary_range_unsupported') from None
    if lines[1]>len(text) or hashlib.sha256(''.join(text[lines[0]-1:lines[1]]).encode()).hexdigest()!=ref['range_sha256']:
        raise ApprovalRefused('annotation_range_changed')
    return {'record_id':ref['record_id'],'edition':ref['edition'],'edition_sha256':ref['edition_sha256'],
            'file':target.relative_to(Path(shelf.root).absolute()).as_posix(),'lines':lines,'range_sha256':ref['range_sha256']}

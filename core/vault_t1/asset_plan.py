"""Read-only, explicitly selected native-custody planning; never captures files.

Logical source IDs are the original inventory's _working-relative paths. A
relocation changes only the explicit current path, never those IDs. This plan is
an observation for review, not capture authority, a capacity reservation, backup
or publication. No original directories are enumerated.
"""
from __future__ import annotations
import json
import fcntl
import os
import stat
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from core.vault_t1 import assets, reader
from core.vault_t1.reader import VaultError

FORMAT = 'vault-private-asset-plan/1'
MAX_FILES = 20
MAX_BATCH_BYTES = 50 << 20
MAX_INVENTORY_BYTES = 16 << 20
MAX_ENTRIES = 20000


def _refuse(code):
    raise VaultError(code)


def _absolute(path):
    if not isinstance(path, (str, Path)):
        _refuse('asset_plan_path_invalid')
    p = Path(path)
    if not p.is_absolute() or '..' in p.parts:
        _refuse('asset_plan_path_invalid')
    return p


def _read_json(path, limit):
    p = _absolute(path)
    fd = reader._open_chain('/', list(p.parts[1:]), final_file=True)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            _refuse('asset_plan_input_limit')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if len(data) > limit or len(data) != before.st_size or any(
            getattr(before, key) != getattr(after, key)
            for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        ):
            _refuse('asset_plan_input_changed')
        try:
            return json.loads(data), assets.sha256(data)
        except (ValueError, UnicodeDecodeError):
            _refuse('asset_plan_input_json')
    finally:
        os.close(fd)


def load_inventory(path, expected_sha256):
    if not isinstance(expected_sha256, str) or not assets.HEX.fullmatch(expected_sha256):
        _refuse('asset_plan_inventory_hash_invalid')
    doc, digest = _read_json(path, MAX_INVENTORY_BYTES)
    if digest != expected_sha256:
        _refuse('asset_plan_inventory_hash_changed')
    validate_inventory(doc)
    return doc


def _source_id(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value:
        _refuse('asset_plan_id_invalid')
    p = PurePosixPath(value)
    if p.is_absolute() or str(p) != value or any(x in ('', '.', '..') for x in value.split('/')) or not value.startswith('_working/'):
        _refuse('asset_plan_id_invalid')
    return value


def validate_inventory(doc):
    if not isinstance(doc, dict) or doc.get('format') != 'working-migration-inventory/1':
        _refuse('asset_plan_inventory_schema')
    entries = doc.get('entries')
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        _refuse('asset_plan_inventory_limit')
    seen = set()
    for row in entries:
        if not isinstance(row, dict):
            _refuse('asset_plan_inventory_schema')
        sid = _source_id(row.get('path'))
        if sid in seen:
            _refuse('asset_plan_inventory_duplicate')
        seen.add(sid)
        size = row.get('bytes')
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            _refuse('asset_plan_inventory_schema')
        digest = row.get('sha256')
        if digest not in (None, '') and (not isinstance(digest, str) or not assets.HEX.fullmatch(digest)):
            _refuse('asset_plan_inventory_schema')
    return {row['path']: row for row in entries}


def load_selection(path):
    doc, _ = _read_json(path, 65536)
    if not isinstance(doc, dict) or set(doc) - {'format', 'ids', 'source_paths'} or doc.get('format') != 'vault-private-asset-selection/1':
        _refuse('asset_plan_selection_schema')
    return doc.get('ids'), doc.get('source_paths', {})


def custody_metadata(root):
    """Read existing bounded authoritative metadata; no native blob scan.

    Every immutable metadata edition is graph-checked. Only selected latest
    blobs are read by plan(). Unreferenced object names still consume capacity.
    Missing/unreadable/layout-limited metadata is never guessed to be empty.
    """
    p = _absolute(root)
    fd = reader._open_chain('/', list(p.parts[1:]), final_file=False)
    try:
        before = os.fstat(fd)
        if before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o700:
            _refuse('asset_plan_custody_insecure')
        captures = {}
        total = 0
        top = assets.bounded_list(p, ['assets'], 3)
        if any(name not in ('captures', 'objects') or not plain for name, plain in top):
            _refuse('asset_plan_custody_layout')
        for aid, plain in assets.bounded_list(p, ['assets', 'captures']):
            if not plain or not assets.ID.fullmatch(aid):
                _refuse('asset_plan_custody_layout')
            for name, regular in assets.bounded_list(p, ['assets', 'captures', aid]):
                if not regular or not assets.CAP.fullmatch(name):
                    _refuse('asset_plan_custody_layout')
                if len(captures) >= assets.MAX_CAPTURES:
                    _refuse('asset_plan_capture_limit')
                rel = 'assets/captures/' + aid + '/' + name
                raw = reader.read_path(p, rel.split('/'), 65536)
                total += len(raw)
                if total > assets.MAX_METADATA_BYTES:
                    _refuse('asset_plan_metadata_limit')
                captures[rel] = raw
        rows = assets.graph_rows(captures)
        expected_sizes = {}
        for doc, _, _ in rows:
            expected_sizes.setdefault(doc['sha256'], set()).add(doc['bytes'])
        objects = set()
        for name, regular in assets.bounded_list(p, ['assets', 'objects']):
            digest = name[:-4] if name.endswith('.bin') else ''
            if not regular or not assets.HEX.fullmatch(digest):
                _refuse('asset_plan_custody_layout')
            # Descriptor metadata only; never read unselected native bytes.
            size = _source_size(p / assets.object_path(digest))
            if size > assets.MAX_BYTES or (digest in expected_sizes and expected_sizes[digest] != {size}):
                _refuse('asset_plan_custody_object_size')
            objects.add(digest)
        if not {doc['sha256'] for doc, _, _ in rows}.issubset(objects):
            _refuse('asset_plan_custody_missing_object')
        after = os.lstat(p)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or not stat.S_ISDIR(after.st_mode):
            _refuse('asset_plan_custody_changed')
        return rows, objects, total
    finally:
        os.close(fd)


def projected_export(rows, selected_asset_ids):
    """Match assets.export's ALL-edition selected graph using metadata only.

    Historical blob bytes are deliberately not read here. This computes export
    limits/allocation, not historical blob integrity or successful recovery.
    """
    selected = [r for r in rows if r[0]['asset_id'] in selected_asset_ids and r[0]['scope'] == 'robert']
    sizes = {}
    for doc, _, _ in selected:
        if doc['sha256'] in sizes and sizes[doc['sha256']] != doc['bytes']:
            _refuse('asset_plan_export_size_mismatch')
        sizes[doc['sha256']] = doc['bytes']
    unique_bytes = sum(sizes.values())
    if unique_bytes > assets.MAX_BYTES:
        _refuse('asset_plan_export_batch_too_large')
    files = [{'path': rel, 'bytes': len(raw), 'sha256': assets.sha256(raw)} for _, rel, raw in selected]
    for digest in sorted(sizes):
        files.append({'path': assets.object_path(digest), 'bytes': sizes[digest], 'sha256': digest})
    files.append({'path': 'SCHEMA.md', 'bytes': len(assets.SCHEMA), 'sha256': assets.sha256(assets.SCHEMA)})
    if len(files) > assets.MAX_EXPORT_FILES or sum(f['bytes'] for f in files) > assets.MAX_BYTES + assets.MAX_METADATA_BYTES:
        _refuse('asset_plan_export_batch_too_large')
    present = {d['asset_id'] for d, _, _ in selected}
    manifest = {'format': assets.FORMAT, 'complete': True, 'files': files,
                'counts': {'assets': len(present), 'asset_editions': len(selected), 'asset_bytes': unique_bytes},
                'limitations': ['native custody only; no text extraction or semantic retrieval'],
                'selection': {'asset_ids': selected_asset_ids, 'scopes': ['robert']}}
    raw_manifest = assets.canonical(manifest)
    if len(raw_manifest) > assets.MAX_METADATA_BYTES:
        _refuse('asset_plan_export_manifest_limit')
    return {'assets': len(present), 'asset_editions': len(selected), 'unique_blob_bytes': unique_bytes,
            'files': len(files), 'file_bytes': sum(f['bytes'] for f in files),
            'manifest_bytes': len(raw_manifest), 'all_selected_ids_present': present == set(selected_asset_ids),
            'allocation_bytes': assets.allocation_estimate(files, manifest_bytes=len(raw_manifest)),
            'historical_integrity_verified': False}


def _source_size(path):
    fd = reader._open_chain('/', list(path.parts[1:]), final_file=True)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            _refuse('asset_plan_source_not_regular')
        return st.st_size
    finally:
        os.close(fd)


def _plan(inventory, selected_ids, *, source_root, source_instance, custody_root,
         source_paths=None, inventory_sha256=None, now=None):
    entries = validate_inventory(inventory)
    if (not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= MAX_FILES
            or any(not isinstance(sid, str) for sid in selected_ids)
            or len(set(selected_ids)) != len(selected_ids)):
        _refuse('asset_plan_selection_limit')
    for sid in selected_ids:
        _source_id(sid)
        if sid not in entries:
            _refuse('asset_plan_selection_missing')
    if not isinstance(source_instance, str) or not 1 <= len(source_instance) <= 128 or '\x00' in source_instance:
        _refuse('asset_plan_source_instance_invalid')
    source_root = _absolute(source_root)
    custody_root = _absolute(custody_root)
    assets.ensure_headroom(custody_root, assets.MAX_METADATA_BYTES)
    overrides = {} if source_paths is None else source_paths
    if not isinstance(overrides, dict) or set(overrides) - set(selected_ids):
        _refuse('asset_plan_override_invalid')
    current_paths = {sid: _absolute(overrides[sid]) if sid in overrides else source_root / sid for sid in selected_ids}
    rows, objects, metadata_bytes = custody_metadata(custody_root)
    latest = {}
    for doc, rel, raw in rows:
        if doc['asset_id'] not in latest or doc['edition'] > latest[doc['asset_id']][0]['edition']:
            latest[doc['asset_id']] = (doc, rel, raw)
    # Bound selected-old-blob verification as well as current-source hashing.
    selected_latest = {}
    for sid in selected_ids:
        aid = assets.sha256(assets.canonical([source_instance, sid]))[:32]
        if aid in latest:
            old = latest[aid][0]
            if old['scope'] != 'robert':
                _refuse('asset_plan_scope_mismatch')
            if old['sha256'] in selected_latest and selected_latest[old['sha256']] != old['bytes']:
                _refuse('asset_plan_selected_blob_metadata_mismatch')
            selected_latest[old['sha256']] = old['bytes']
    if sum(selected_latest.values()) > MAX_BATCH_BYTES:
        _refuse('asset_plan_selected_blob_limit')
    verified = set()
    for digest, size in selected_latest.items():
        data = assets.read_stable(custody_root / assets.object_path(digest))
        if len(data) != size or assets.sha256(data) != digest:
            _refuse('asset_plan_selected_blob_damaged')
        verified.add(digest)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    result = []
    added = []
    proposed_objects = set()
    proposed_rows = []
    current_bytes = 0
    for sid in selected_ids:
        row = entries[sid]
        path = current_paths[sid]
        aid = assets.sha256(assets.canonical([source_instance, sid]))[:32]
        item = {'source_id': sid, 'source_instance': source_instance, 'asset_id': aid,
                'source': str(path), 'scope': 'robert', 'inventory_sha256': row.get('sha256'),
                'inventory_bytes': row['bytes'], 'proposed_capture': None}
        result.append(item)
        if path.suffix.lower() not in assets.NATIVE_EXTENSIONS:
            item.update(status='unsupported', reason='native_extension_unsupported')
            continue
        try:
            size = _source_size(path)
            if size > assets.MAX_BYTES:
                item.update(status='oversize', reason='per_file_limit')
                continue
            if current_bytes + size > MAX_BATCH_BYTES:
                item.update(status='oversize', reason='selected_batch_limit')
                continue
            data = assets.read_stable(path)
        except reader.NotFound:
            item.update(status='missing', reason='source_missing')
            continue
        except (VaultError, OSError):
            item.update(status='stale', reason='source_unsafe_or_changed')
            continue
        current_bytes += len(data)
        if current_bytes > MAX_BATCH_BYTES:
            _refuse('asset_plan_source_batch_changed')
        digest = assets.sha256(data)
        item.update(current_sha256=digest, current_bytes=len(data),
                    inventory_matches=(row.get('sha256') == digest and row['bytes'] == len(data)))
        old = latest.get(aid)
        if old and old[0]['sha256'] == digest and old[0]['source_path'] == str(path) and digest in verified:
            item.update(status='current_matching_covered', reason='selected_latest_blob_verified')
            continue
        if old:
            status, reason = 'stale', ('source_path_changed' if old[0]['sha256'] == digest else 'captured_bytes_changed')
        elif not row.get('sha256'):
            status, reason = 'unhashed', 'inventory_unhashed_refreshed_for_review'
        elif not item['inventory_matches']:
            status, reason = 'stale', 'inventory_bytes_changed'
        else:
            status, reason = 'missing', 'custody_missing'
        item.update(status=status, reason=reason)
        previous = assets.sha256(old[2]) if old else None
        doc = {'format': assets.CAPTURE_FORMAT, 'asset_id': aid, 'edition': old[0]['edition'] + 1 if old else 1,
               'source_instance': source_instance, 'source_id': sid, 'source_path': str(path),
               'original_name': path.name, 'scope': 'robert', 'captured_at': timestamp,
               'bytes': len(data), 'sha256': digest, 'previous_capture_sha256': previous}
        raw = assets.canonical(doc)
        rel = assets.capture_path(doc, raw)
        assets.check_capture(doc, rel, raw)
        if len(raw) > 65536:
            _refuse('asset_plan_metadata_record_limit')
        added.append({'path': rel, 'bytes': len(raw)})
        proposed_rows.append((doc, rel, raw))
        # Existing same-digest blobs not selected above must also be verified
        # before a future reuse; do not call an unselected blob covered here.
        if digest not in objects and digest not in proposed_objects:
            added.append({'path': assets.object_path(digest), 'bytes': len(data)})
            proposed_objects.add(digest)
        item['proposed_capture'] = {'source': str(path), 'source_instance': source_instance,
                                    'source_id': sid, 'scope': 'robert',
                                    'expected_sha256': digest, 'expected_bytes': len(data)}
    captures = [r for r in result if r['proposed_capture'] is not None]
    extra_metadata = sum(r['bytes'] for r in added if r['path'].endswith('.json'))
    if len(rows) + len(captures) > assets.MAX_CAPTURES:
        _refuse('asset_plan_capture_limit')
    if metadata_bytes + extra_metadata > assets.MAX_METADATA_BYTES:
        _refuse('asset_plan_metadata_limit')
    if len(objects) + len(proposed_objects) > assets.MAX_CAPTURES:
        _refuse('asset_plan_object_limit')
    selected_asset_ids = [assets.sha256(assets.canonical([source_instance, sid]))[:32] for sid in selected_ids]
    recovery = projected_export(rows + proposed_rows, selected_asset_ids)
    # Recovery selects ALL history, not merely the latest/current source bytes.
    # Count export + restore + one extra private recovery copy on this filesystem,
    # rounded by the exact same file graph/manifest used for export admission.
    need = assets.allocation_estimate(added + [{'path': '.assets.lock', 'bytes': 0}])
    need += 3 * recovery['allocation_bytes'] + assets.MAX_METADATA_BYTES
    assets.ensure_headroom(custody_root, need)
    blocked = any(r['proposed_capture'] is None and r['status'] != 'current_matching_covered' for r in result)
    return {'format': FORMAT, 'prepared_at': timestamp, 'inventory_sha256': inventory_sha256,
            'authority': 'explicit_ID_selection_only; capture_and_publication_not_authorized_by_plan',
            'captured': False, 'admitted_for_review': not blocked, 'items': result,
            'counts': dict(Counter(r['status'] for r in result)),
            'selected_files': len(result), 'current_selected_bytes': current_bytes,
            'proposed_captures': len(captures), 'projected_selected_export': recovery,
            'capacity': {'current_editions': len(rows), 'current_metadata_bytes': metadata_bytes,
                         'remaining_editions_after_plan': assets.MAX_CAPTURES - len(rows) - len(captures),
                         'remaining_metadata_after_plan': assets.MAX_METADATA_BYTES - metadata_bytes - extra_metadata,
                         'reserve_bytes': assets.RESERVE_BYTES, 'required_extra_bytes': need,
                         'observation_not_reservation': True},
            'limits': {'selected_files': MAX_FILES, 'selected_bytes': MAX_BATCH_BYTES},
            'limitations': ['Only selected current originals and selected latest custody blobs were read.',
                            'Historical/unselected blob bytes are not integrity-verified by this planner.',
                            'Capacity may change; existing capture/export/restore must revalidate.',
                            'No capture, move, backup, extraction, publication or content approval occurred.']}


def plan(inventory, selected_ids, *, custody_root, **kwargs):
    """Hold the EXISTING capture lock shared, never create or mutate the store.

    Missing lock/index authority is refused rather than assumed empty. Existing
    capture's exclusive lock protects the metadata observation while planning;
    after return this remains an observation, not a capacity reservation.
    """
    root = _absolute(custody_root)
    root_fd = reader._open_chain('/', list(root.parts[1:]), final_file=False)
    lock = None
    try:
        identity = os.fstat(root_fd)
        lock = reader._open_chain('/', list((root / '.assets.lock').parts[1:]), final_file=True)
        st = os.fstat(lock)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600 or st.st_nlink != 1):
            _refuse('asset_plan_lock_insecure')
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            _refuse('asset_plan_custody_busy')
        result = _plan(inventory, selected_ids, custody_root=root, **kwargs)
        current = os.lstat(root)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            _refuse('asset_plan_custody_changed')
        return result
    finally:
        if lock is not None:
            os.close(lock)
        os.close(root_fd)


def write_receipt(path, document):
    """Optional private exclusive receipt only; never create parent directories."""
    p = _absolute(path)
    fd = reader._open_chain('/', list(p.parent.parts[1:]), final_file=False)
    opened = None
    try:
        st = os.fstat(fd)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
            _refuse('asset_plan_receipt_parent_insecure')
        data = assets.canonical(document)
        assets.ensure_headroom(p.parent, assets.allocation_estimate([{'path': p.name, 'bytes': len(data)}]))
        opened = os.open(p.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        os.fchmod(opened, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(opened, view)
            if written <= 0:
                _refuse('asset_plan_receipt_write_failed')
            view = view[written:]
        os.fsync(opened)
        os.fsync(fd)
    finally:
        if opened is not None:
            os.close(opened)
        os.close(fd)

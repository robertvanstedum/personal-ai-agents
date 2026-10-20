"""Bounded text-file bundles for MC delivery. Model output is data, never a path or command.

This module only prepares bytes. The authenticated host must bind them to an explicit owner
request and conversation, retain them, and verify retrieval before claiming delivery.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import zipfile

MAX_FILES = 12
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 128 * 1024
MAX_PACKET_BYTES = 256 * 1024
_ALLOWED_SUFFIXES = {'.txt', '.md', '.json'}
_SEGMENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_. -]{0,79}')
_RESERVED = re.compile(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', re.I)

class BundleRefused(ValueError):
    """Stable reason only; never includes file contents or an untrusted filename."""

@dataclass(frozen=True)
class PreparedBundle:
    data: bytes
    sha256: str
    files: tuple[dict, ...]


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise BundleRefused('duplicate_json_key')
        out[key] = value
    return out


def _name(name):
    if not isinstance(name, str) or len(name) > 180:
        raise BundleRefused('invalid_filename')
    parts = name.split('/')
    if not 1 <= len(parts) <= 4 or any(
        not _SEGMENT.fullmatch(p) or p.endswith((' ', '.')) or _RESERVED.fullmatch(p)
        for p in parts
    ):
        raise BundleRefused('invalid_filename')
    if PurePosixPath(name).suffix.lower() not in _ALLOWED_SUFFIXES:
        raise BundleRefused('unsupported_file_type')
    return name


def prepare(packet: str, *, scrub) -> PreparedBundle:
    """Accept exactly {files:[{name,content}]} and return a deterministic ZIP plus verified manifest.

    The caller supplies the existing credential/payment scrubber. Every file must survive it
    unchanged: a changed file is refused rather than silently delivering a different document.
    Empty file content is valid. No input file is opened, code evaluated, archive extracted, or supplied path followed.
    """
    if not isinstance(packet, str):
        raise BundleRefused('invalid_packet')
    try:
        raw = packet.encode('utf-8')
    except UnicodeError:
        raise BundleRefused('invalid_unicode') from None
    if len(raw) > MAX_PACKET_BYTES:
        raise BundleRefused('packet_too_large')
    try:
        value = json.loads(packet, object_pairs_hook=_pairs)
    except (json.JSONDecodeError, RecursionError):
        raise BundleRefused('invalid_json') from None
    if not isinstance(value, dict) or set(value) != {'files'} or not isinstance(value['files'], list):
        raise BundleRefused('invalid_packet')
    if not 1 <= len(value['files']) <= MAX_FILES:
        raise BundleRefused('file_count')
    files, seen, total = [], set(), 0
    for entry in value['files']:
        if not isinstance(entry, dict) or set(entry) != {'name', 'content'}:
            raise BundleRefused('invalid_file')
        name = _name(entry['name'])
        folded = name.casefold()
        if folded in seen or any(folded.startswith(n+'/') or n.startswith(folded+'/') for n in seen):
            raise BundleRefused('filename_collision')
        seen.add(folded)
        content = entry['content']
        if not isinstance(content, str) or '\x00' in content:
            raise BundleRefused('invalid_content')
        try:
            data = content.encode('utf-8')
        except UnicodeError:
            raise BundleRefused('invalid_unicode') from None
        if len(data) > MAX_FILE_BYTES:
            raise BundleRefused('file_too_large')
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise BundleRefused('bundle_too_large')
        if scrub(content) != content:
            raise BundleRefused('sensitive_content')
        files.append((name, data))
    output = io.BytesIO()
    manifest = []
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, data in files:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            archive.writestr(info, data)
            manifest.append({'name':name, 'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()})
    result = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        if archive.namelist() != [f['name'] for f in manifest]:
            raise BundleRefused('verification_failed')
        for item in manifest:
            if hashlib.sha256(archive.read(item['name'])).hexdigest() != item['sha256']:
                raise BundleRefused('verification_failed')
    return PreparedBundle(result, hashlib.sha256(result).hexdigest(), tuple(manifest))

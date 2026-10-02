"""Explicit offline adapters. No directory crawling, application DB or network."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import importlib.util

from .contracts import ContractError, Unavailable, strict_json, validate, digest


@dataclass(frozen=True)
class Event:
    value: dict
    raw: bytes


@dataclass(frozen=True)
class Export:
    family: str
    events: tuple[Event, ...]
    original_hash: str
    gaps: tuple[str, ...]
    completeness: str


class Adapter(Protocol):
    def read(self, path: Path) -> Export: ...


def read_file(path):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ContractError('Explicit regular source file required; symlinks refused')
    return path.read_bytes()


class FixtureSessionAdapter:
    """Our fixture interchange is not a guessed Codex/Claude/CoS export format."""
    def read(self, path):
        raw = read_file(path)
        events = []
        for line in raw.splitlines(keepends=True):
            value = strict_json(line)
            validate('event', value)
            events.append(Event(value, line))
        if not events:
            raise ContractError('Empty fixture export')
        identities = [(e.value['provider'], e.value['thread_id'], e.value['event_id']) for e in events]
        positions = [(e.value['provider'], e.value['thread_id'], e.value['index']) for e in events]
        if len(set(identities)) != len(identities) or len(set(positions)) != len(positions):
            raise ContractError('Duplicate source event identity or position')
        return Export('synthetic_session', tuple(events), digest(raw), (), 'partial')


def inspect_rooms_bundle(directory):
    """Read-only real contract inspection using the existing pure validator.

    Does not capture: event-range export, private exclusion and attachments need
    an owner-verified sample. Does not import portal, store, publisher or app.
    """
    directory = Path(directory)
    manifest = strict_json(read_file(directory / 'manifest.json'))
    if manifest.get('schema_version') != 'minimoi.transcript/1.1':
        raise ContractError('Rooms transcript 1.1 required')
    files = manifest.get('files', {})
    if set(files) != {'transcript.json', 'transcript.md'}:
        raise ContractError('Unexpected Rooms bundle files')
    for name, expected in files.items():
        raw = read_file(directory / name)
        if len(raw) != expected.get('bytes') or digest(raw) != expected.get('sha256'):
            raise ContractError('Rooms bundle hash mismatch')
    if importlib.util.find_spec('jsonschema') is None:
        raise Unavailable('Rooms validator requires jsonschema; install only after owner setup approval')
    path = Path(__file__).parent.parent / 'prototype-lab/projects/project-records-room-poc/transcript_format.py'
    spec = importlib.util.spec_from_file_location('_intelligence_rooms_format', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = strict_json(read_file(directory / 'transcript.json'))
    try:
        module.validate(data)
    except Exception:
        raise ContractError('Rooms transcript validation failed') from None
    for key, actual in [('source_instance_id', data['source_instance_id']),
                        ('session_id', data['session']['session_id']), ('source_revision', data['source_revision']),
                        ('through_seq', data['through_seq']), ('schema_version', data['schema_version'])]:
        if manifest.get(key) != actual:
            raise ContractError('Rooms bundle identity mismatch')
    return {'status': 'validated-export-only', 'schema_version': data['schema_version'],
            'events': len(data['raw_transcript']), 'capture': 'unavailable: activation and range contract pending'}


UNAVAILABLE = {
    'rooms': '1.1 validator and bundle contract inspected; capture needs designated-range/private/attachment sample',
    'cos_turns': 'Spec 160 dropped turn_index; cos_turn writer absent at pinned base; verified exporter needed',
    'agent_memory': 'Spec 160 copier and manifest writer absent at pinned base; verified exporter needed',
    'claude_code_sessions': 'Owner-verified exporter, compaction and private-event boundaries required; no session scanning',
    'codex_sessions': 'Owner-verified exporter, compaction and private-event boundaries required; no session scanning',
    'relay': 'Original versus relayed attribution and screenshot/OCR contract require verified sample',
}

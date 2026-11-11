"""Durable shared allowance for bounded model calls.

Unknown spend remains reserved. Stable call identities and task totals survive
midnight and restarts. The ledger lock covers both checking and reserving, so
concurrent workers cannot each spend the last allowance.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import time


class CapReached(Exception):
    pass


class AlreadyCalled(Exception):
    """This call ran or may have run. Never call again."""


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid allowance')
    return float(value)


def _durable_directory(path):
    missing, existing = [], path
    while not existing.exists():
        missing.append(existing)
        existing = existing.parent
    # Also settle a previous failed mkdir/fsync attempt before extending it.
    def sync(folder):
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    sync(existing.parent)
    for folder in reversed(missing):
        folder.mkdir(exist_ok=True, mode=0o700)
        sync(folder.parent)


class Ledger:
    def __init__(self, root, *, daily_cap: float, clock=time.time):
        self.root, self.daily_cap, self.clock = Path(root), _amount(daily_cap), clock
        _durable_directory(self.root)

    @contextmanager
    def _locked(self):
        with (self.root / '.lock').open('a') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _day(self) -> Path:
        d = self.root / time.strftime('%Y-%m-%d', time.gmtime(self.clock()))
        d.mkdir(exist_ok=True, mode=0o700)
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return d

    def _rows(self):
        for p in self.root.glob('????-??-??/*.json'):
            row = json.loads(p.read_text())  # A damaged reservation is unavailable, never zero spend.
            charge = _amount(row['actual_usd'] if row.get('actual_usd') is not None else row['bound_usd'])
            yield p, row, charge

    def spent_today(self) -> float:
        with self._locked():
            day = self._day()
            return sum(charge for p, _, charge in self._rows() if p.parent == day)

    def reserve(self, key: str, *, bound_usd: float, task_spent: float, task_cap: float, purpose: str, task_id: str) -> Path:
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', key) or key in ('.', '..'):
            raise ValueError('invalid call identity')
        bound, prior, cap = map(_amount, (bound_usd, task_spent, task_cap))
        if not isinstance(task_id, str) or not task_id or not isinstance(purpose, str):
            raise ValueError('invalid reservation provenance')
        with self._locked():
            rows = list(self._rows())
            if any(p.name == key + '.json' for p, _, _ in rows):
                raise AlreadyCalled(key)
            # Host-provided external spend can only tighten the persistent task total.
            total = max(prior, sum(charge for _, row, charge in rows if row['task_id'] == task_id))
            if total + bound > cap + 1e-12:
                raise CapReached('task')
            day = self._day()
            if sum(charge for p, _, charge in rows if p.parent == day) + bound > self.daily_cap + 1e-12:
                raise CapReached('day')
            path = day / (key + '.json')
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as fh:
                json.dump({'task_id': task_id, 'purpose': purpose, 'bound_usd': bound,
                           'actual_usd': None, 'estimate': True}, fh)
                fh.flush()
                os.fsync(fh.fileno())
            fd = os.open(day, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            return path

    @staticmethod
    def settle(path: Path, actual_usd: float) -> None:
        amount = _amount(actual_usd)
        ledger = Ledger(path.parent.parent, daily_cap=0)
        with ledger._locked():
            row = json.loads(path.read_text())
            # Never release an unknown reservation using an invalid or competing settlement.
            if row.get('actual_usd') is not None and row['actual_usd'] != amount:
                raise ValueError('settlement differs')
            row['actual_usd'] = amount
            tmp = path.with_suffix('.tmp')
            with tmp.open('w') as fh:
                os.chmod(tmp, 0o600)
                json.dump(row, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

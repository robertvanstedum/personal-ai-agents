"""SYNTHETIC execution authority for B2A. Its own store and its own transactions, apart from the task service. It
keeps its own fence per task (raised by set_fence) and refuses an admission below it inside the same locked commit
that writes the admission and its dedup receipt: amendment fencing and effect admission serialize here (C06). A lookup
that answers "absent" for attempt n makes attempt n dead, so a late send can never contradict it (E03). Faults are
injected per call. It runs nothing and spends nothing."""
from __future__ import annotations

import threading
from pathlib import Path

from core import durable_files as fsio
from services.partnership_tasks.outbox import DestinationUnreachable, HarnessRefused


class FakeHarness:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.faults: list[str] = []      # "lose_answer_after_commit", "fail_before_commit", "lookup_down"
        self.admits_seen = 0
        self.before_commit = None        # test hook: runs inside the lock, before the fence check

    def _doc(self, kind, key):
        return self.root / kind / f"{key}.json"

    def admissions(self):
        folder = self.root / "admissions"
        docs = [(p.stem, fsio.read_doc(p)) for p in folder.glob("OB-*.json")] if folder.is_dir() else []
        return sorted(k for k, d in docs if d["receipt"]["edition"] == "admitted")

    def fence(self, task_id):
        return (fsio.read_doc(self._doc("fences", task_id)) or {"fence": 0})["fence"]

    def _dead(self, op):
        return (fsio.read_doc(self._doc("dead", op)) or {"below_or_at": 0})["below_or_at"]

    def admit(self, operation_id, bundle_digest, *, task_id, fence_generation, attempt):
        fault = self.faults.pop(0) if self.faults else None
        with self.lock:
            self.admits_seen += 1
            if fault == "fail_before_commit":
                raise DestinationUnreachable("dropped before commit")
            if self.before_commit:
                self.before_commit()
            found = fsio.read_doc(self._doc("admissions", operation_id))
            if found is not None:
                if found["bundle_digest"] != bundle_digest:
                    raise HarnessRefused("operation_id_reused")
                return {"state": "duplicate", "receipt": found["receipt"]}
            if attempt <= self._dead(operation_id):
                raise HarnessRefused("attempt_declared_absent")
            if fence_generation < self.fence(task_id):
                raise HarnessRefused("fenced")
            receipt = {"store": "harness", "id": operation_id, "edition": "admitted", "digest": bundle_digest}
            fsio.write_doc(self._doc("admissions", operation_id),
                           {"bundle_digest": bundle_digest, "task_id": task_id, "fence": fence_generation,
                            "receipt": receipt})
        if fault == "lose_answer_after_commit":
            raise DestinationUnreachable("committed, answer lost")
        return {"state": "admitted", "receipt": receipt}

    def set_fence(self, operation_id, *, task_id, fence_generation, attempt):
        from core.canonical_json import digest
        with self.lock:
            if fence_generation > self.fence(task_id):
                fsio.write_doc(self._doc("fences", task_id), {"fence": fence_generation})
            body = {"task_id": task_id, "fence_generation": fence_generation}
            receipt = {"store": "harness", "id": operation_id, "edition": "fence", "digest": digest(body)}
            fsio.write_doc(self._doc("admissions", operation_id), {"bundle_digest": digest(body), "task_id": task_id,
                                                                   "fence": fence_generation, "receipt": receipt})
        return {"state": "applied", "receipt": receipt}

    def peek(self, operation_id):
        """Read-only status for audits: applied or absent, with no side effects (unlike lookup)."""
        with self.lock:
            return "applied" if fsio.read_doc(self._doc("admissions", operation_id)) is not None else "absent"

    def lookup(self, operation_id, *, attempt):
        if "lookup_down" in self.faults:
            return {"state": "unknown"}
        with self.lock:
            found = fsio.read_doc(self._doc("admissions", operation_id))
            if found is not None:
                return {"state": "applied", "receipt": found["receipt"]}
            if attempt > self._dead(operation_id):
                fsio.write_doc(self._doc("dead", operation_id), {"below_or_at": attempt})
            return {"state": "absent"}


class FakeNotices:
    """SYNTHETIC record-only notice channel: writes the notice, delivers nothing to any person."""

    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.faults: list[str] = []      # "down" (unreachable), "lookup_down"

    def delivered(self):
        folder = self.root / "notices"
        return sorted(p.stem for p in folder.glob("OB-*.json")) if folder.is_dir() else []

    def deliver(self, operation_id, payload_digest, *, task_id, kind, attempt):
        if self.faults and self.faults[0] == "down":
            self.faults.pop(0)
            raise DestinationUnreachable("notice channel down")
        with self.lock:
            dead = fsio.read_doc(self.root / "dead" / f"{operation_id}.json") or {"below_or_at": 0}
            if attempt <= dead["below_or_at"]:
                raise HarnessRefused("attempt_declared_absent")
            receipt = {"store": "notices", "id": operation_id, "edition": "delivered", "digest": payload_digest}
            if fsio.read_doc(self.root / "notices" / f"{operation_id}.json") is None:
                fsio.write_doc(self.root / "notices" / f"{operation_id}.json",
                               {"task_id": task_id, "kind": kind, "receipt": receipt})
        return {"state": "delivered", "receipt": receipt}

    def lookup(self, operation_id, *, attempt):
        if "lookup_down" in self.faults:
            return {"state": "unknown"}
        with self.lock:
            found = fsio.read_doc(self.root / "notices" / f"{operation_id}.json")
            if found is not None:
                return {"state": "applied", "receipt": found["receipt"]}
            fsio.write_doc(self.root / "dead" / f"{operation_id}.json", {"below_or_at": attempt})
            return {"state": "absent"}

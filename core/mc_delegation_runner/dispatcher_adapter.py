"""Runner-side adapter to the dispatcher store: the runner's ``dispatcher`` protocol, plus the
bounded output text returned with each result so the owner sees what the delegate produced."""
from __future__ import annotations

from pathlib import Path

OUTPUT_MAX = 64 * 1024


class StoreDispatcher:
    def __init__(self, store, artifact_root: Path):
        self.store, self.artifact_root = store, Path(artifact_root)

    def heartbeat(self, runner_id, state, capabilities):
        return self.store.heartbeat(runner_id, state, capabilities)

    def claim(self, runner_id, capabilities):
        return self.store.claim(runner_id, capabilities)

    def progress(self, request_id, generation, phase):
        return self.store.progress(request_id, generation, phase)

    def cancel_requested(self, request_id, generation):
        return self.store.cancel_requested(request_id, generation)

    def renew(self, request_id, generation):
        return self.store.renew(request_id, generation)

    def result(self, request_id, generation, *, event_id, status, artifacts, detail):
        text = None
        for a in artifacts or []:
            if isinstance(a, dict) and a.get("kind") == "output" and "/" not in str(a.get("ref", "")):
                path = self.artifact_root / a["ref"]
                try:
                    if path.is_file() and not path.is_symlink():
                        text = path.read_bytes()[:OUTPUT_MAX].decode("utf-8", errors="replace")
                except OSError:
                    text = None
        return self.store.result(request_id, generation, event_id=event_id, status=status, artifacts=artifacts,
                                 detail=detail, output_text=text)

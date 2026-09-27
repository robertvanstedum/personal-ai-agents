"""Recent activity: local git only (no fetch, no gh, no network)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .contract import READ_FAILED, SourceResult, live_ok, live_unknown, sample

_SEP = "\x1f"


class LiveActivity:
    def __init__(self, repo: Path):
        self.repo = Path(repo)

    def _git(self, *args) -> str:
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
        out = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True,
                             text=True, timeout=2, env=env, check=True)
        return out.stdout

    def recent_commits(self, limit: int = 5) -> SourceResult:
        evidence = f"git log -n {limit} (local worktree)"
        try:
            raw = self._git("log", f"-n{limit}", f"--format=%h{_SEP}%s{_SEP}%cI")
        except (OSError, subprocess.SubprocessError) as exc:
            return live_unknown(READ_FAILED, f"git log failed: {type(exc).__name__}", evidence)
        commits = []
        for line in raw.splitlines():
            parts = line.split(_SEP)
            if len(parts) == 3:
                commits.append({"ref": parts[0], "title": parts[1], "at": parts[2]})
        return live_ok(commits, evidence, fresh_for_s=300)

    def branch(self) -> SourceResult:
        evidence = "git branch --show-current (local)"
        try:
            name = self._git("branch", "--show-current").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return live_unknown(READ_FAILED, f"git branch failed: {type(exc).__name__}", evidence)
        return live_ok(name, evidence)


class SampleActivity:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.doc = json.loads(self.path.read_text(encoding="utf-8"))

    def recent_commits(self, limit: int = 5) -> SourceResult:
        return sample(self.doc["commits"][:limit], f"fixtures/{self.path.name}", self.doc.get("observed_at"))

    def branch(self) -> SourceResult:
        return sample(self.doc["branch"], f"fixtures/{self.path.name}")

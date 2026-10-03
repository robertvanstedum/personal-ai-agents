"""Terminal-only dry run (agent-memory v0.4 §3.5).

``python -m core.agent_memory.dry_run --source <name> [--config path]``

Prints one row per candidate to STDOUT and nothing anywhere else: no log, no
status file, no data folder, no network. Robert reviews it, marks personal
files ``never_copy``, and approves each source before its first real copy.
The last line carries a hash of the listing (decision, reason and name, not
the redaction counts) that ``run --approved-by-dry-run`` must match.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from typing import Sequence

from .config import ConfigError, SourceConfig, build_source, load_config
from .scrub import scrub_file
from .selection import classify_path, content_reason, path_reason
from .sources import Source, SourceScan

WOULD_COPY = "would-copy"
SKIP = "skip"


@dataclass(frozen=True)
class Row:
    decision: str
    path: str
    reason: str = ""
    redactions: int = 0

    def line(self) -> str:
        detail = f"redactions={self.redactions}" if self.decision == WOULD_COPY else self.reason
        return f"{self.decision}\t{self.path}\t{detail}"


def scan_for_listing(cfg: SourceConfig, source: Source) -> SourceScan:
    rules = cfg.rules()
    return source.read(lambda rel: path_reason(rel, rules))


def build_rows(cfg: SourceConfig, scan: SourceScan) -> list[Row]:
    """One row per candidate: would-copy with its redaction count, or skip with a reason code."""
    rules = cfg.rules()
    rows = [Row(SKIP, path, reason) for path, reason in scan.rejected.items()]
    seen: set[str] = set()
    for raw, data in scan.files.items():
        path, reason = classify_path(raw, rules)
        reason = reason or content_reason(data) or ("duplicate_path" if path in seen else None)
        if reason:
            rows.append(Row(SKIP, raw, reason))
            continue
        seen.add(path)
        rows.append(Row(WOULD_COPY, path, redactions=scrub_file(path, data).redactions))
    return sorted(rows, key=lambda r: (r.path, r.decision))


def listing_hash(rows: Sequence[Row]) -> str:
    """SHA-256 of the sorted (decision, reason, name) lines; ignores redaction counts and file bytes."""
    text = "\n".join(f"{r.decision}\t{r.reason}\t{r.path}" for r in rows)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render(rows: Sequence[Row]) -> list[str]:
    return [r.line() for r in rows] + [f"# listing_sha256={listing_hash(rows)}"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.agent_memory.dry_run")
    parser.add_argument("--source", required=True)
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        cfg = config.sources[args.source]
        scan = scan_for_listing(cfg, build_source(cfg))
    except (ConfigError, KeyError):
        print("dry run: configuration problem")
        return 2
    except Exception:  # noqa: BLE001 - fixed text only
        print("dry run: internal")
        return 2
    if not scan.complete:
        print(f"dry run: scan incomplete ({scan.code or 'copy_incomplete'}); nothing would be copied")
        return 2
    print("\n".join(render(build_rows(cfg, scan))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

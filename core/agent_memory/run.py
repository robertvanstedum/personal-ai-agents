"""One source's daily copy run (agent-memory v0.4 §3.6, §6).

scan -> select -> scrub -> diff against ``current`` -> atomic publish -> status.

``run_source`` NEVER raises. Whatever goes wrong, the caller gets a
``RunResult`` carrying one fixed code from ``errors.py``; exception text, file
names and matched content are never logged, stored or returned (T9).

CLI: ``python -m core.agent_memory.run --source <name> --approved-by-dry-run <sha256>``
refuses a real copy unless the hash equals a fresh dry-run of the same source,
which encodes "Robert approves each source's dry run before its first copy".
"""
from __future__ import annotations

import argparse
import logging
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from . import publish as publish_mod
from .config import ConfigError, Headroom, SourceConfig, build_source, load_config
from .dry_run import build_rows, listing_hash, scan_for_listing
from .errors import COPY_INCOMPLETE, DISK_LOW, INTERNAL, CopierError, classify
from .fsio import ensure_dir
from .headroom import check_headroom
from .scrub import scrub_file
from .selection import path_reason, select
from .snapshot import diff, utc_stamp
from .sources import Source
from .status import update_status

log = logging.getLogger("minimoi.agent_memory")


@dataclass(frozen=True)
class RunResult:
    """Codes, names of snapshots and counts only: never file names or content."""
    source: str
    ok: bool
    code: str | None = None
    snapshot: str | None = None
    counts: dict = field(default_factory=dict)


def _guard(stage: str, fn: Callable[[], object]):
    """Run one stage; any failure becomes a CopierError with a fixed code and no text."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - mapped to a fixed code, text discarded
        raise CopierError(classify(exc, stage)) from None


def _run(source: Source, cfg: SourceConfig, source_dir: Path, now: datetime,
         headroom: Headroom) -> tuple[RunResult, datetime | None]:
    if check_headroom(source_dir, headroom.min_free_bytes, headroom.min_free_fraction):
        raise CopierError(DISK_LOW)
    _guard("write", lambda: (ensure_dir(source_dir), publish_mod.recover(source_dir)))
    rules = cfg.rules()
    scan = _guard("scan", lambda: source.read(lambda rel: path_reason(rel, rules)))
    if not scan.complete:
        raise CopierError(scan.code or COPY_INCOMPLETE)
    chosen = select(scan.files, rules)
    skipped = dict(scan.reason_counts() + chosen.skipped)
    stored = _guard("scrub", lambda: {p: scrub_file(p, d) for p, d in chosen.files.items()})
    previous = publish_mod.current_files(source_dir) or {}
    changed, deleted = diff(previous, stored)
    counts = {"files": len(stored), "changed": len(changed), "deleted": len(deleted),
              "sanitized_files": sum(f.sanitized for f in stored.values()),
              "redactions": sum(f.redactions for f in stored.values()), "skipped": skipped}
    snapshot = None
    if changed or deleted:                      # unchanged files make no snapshot (T1)
        run_id = f"{utc_stamp(now)}-{secrets.token_hex(4)}"
        snapshot = _guard("write", lambda: publish_mod.publish(
            source_dir, run_id=run_id, now=now, source_meta=cfg.meta(), stored=stored,
            changed=changed, deleted=deleted, skipped=skipped, headroom=headroom))
    return RunResult(cfg.name, True, None, snapshot, counts), scan.data_time


def run_source(source: Source, config: SourceConfig, root: str | Path, now: datetime,
               *, headroom: Headroom | None = None) -> RunResult:
    """Copy one source once. Never raises; failures are one fixed code (§6)."""
    headroom = headroom or Headroom()
    source_dir = Path(root) / config.name
    try:
        result, data_time = _run(source, config, source_dir, now, headroom)
        update_status(source_dir, config.name, now, ok=True, code=None, complete=True,
                      data_time=data_time, counts=result.counts, snapshot=result.snapshot)
        log.info("agent_memory source=%s ok changed=%d deleted=%d", config.name,
                 result.counts["changed"], result.counts["deleted"])
        return result
    except Exception as exc:  # noqa: BLE001 - only a fixed code leaves this function
        code = exc.code if isinstance(exc, CopierError) else INTERNAL
        if code != DISK_LOW:                    # never delete anything to make room
            try:
                publish_mod.recover(source_dir)
            except Exception:  # noqa: BLE001 - the next run recovers
                pass
        update_status(source_dir, config.name, now, ok=False, code=code, complete=False)
        log.warning("agent_memory source=%s failed code=%s", config.name, code)
        return RunResult(config.name, False, code)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.agent_memory.run")
    parser.add_argument("--source", required=True)
    parser.add_argument("--config")
    parser.add_argument("--root", help="data root (default: data_root from the config)")
    parser.add_argument("--approved-by-dry-run", metavar="SHA256")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        cfg = config.sources[args.source]
        source = build_source(cfg)
        scan = scan_for_listing(cfg, source)
    except (ConfigError, KeyError):
        print("refused: configuration problem")
        return 2
    except Exception:  # noqa: BLE001
        print("refused: internal")
        return 2
    approved = scan.complete and args.approved_by_dry_run == listing_hash(build_rows(cfg, scan))
    if not approved:
        print("refused: a real copy needs --approved-by-dry-run matching a fresh dry run of this source")
        return 3
    result = run_source(source, cfg, args.root or config.data_root, datetime.now(timezone.utc),
                        headroom=config.headroom)
    print("ok" + (f" snapshot={result.snapshot}" if result.snapshot else " no-changes") if result.ok
          else f"failed code={result.code}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())

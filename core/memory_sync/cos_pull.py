"""``vault-cos-pull``: production's CoS conversations into the Mac's ``minimoi-cos`` watch folder, then the approval-gated capture
(11 Nov 2026, #509).

The reverse leg of the production CoS capture path; EC2's ``vault-cos-export`` (``core/memory_sync/cos_export.py``) writes the batches.
**The same run as ``vault-mc-pull``** (``core/memory_sync/mc_pull.py``, every step and failure rule there applies), with this family:

  job ``vault-cos-pull`` · batches ``sync/cos-export/`` · acknowledgements ``sync/cos-export-acks/`` · file instance = the environment
  (``prod``) · watch folder ``~/minimoi-staging/cos-export`` (0700, outside ``data/``) · state ``~/minimoi-staging/.vault-cos-pull/`` ·
  capture source ``minimoi-cos`` of kind ``cos`` (``core/memory_shelf/cos.py``).

It shares the one ``<jobs_root>/vault-batch.lock`` with ``vault-mc-pull`` and ``vault-sync``: a run that finds it held writes nothing
and exits 0 (``pull=busy``). It reads the same private identity as ``vault-mc-pull`` (a spec, never the key text).

Before a file is placed, its packed metadata must say ``"private": false`` for this environment (``cos_batch.doc_ok``); anything else is
acknowledged ``rejected:not_an_export`` and never reaches the watch folder. The watcher's parser checks it once more. Every capture waits
for the owner's approval of the source (``moi dry-run minimoi-cos``, then ``moi approve-source minimoi-cos``); until then the pull warns
``capture=not_approved`` and the files wait in the folder.
"""
from __future__ import annotations

import shutil

from core.memory_backup import backup as B
from core.memory_sync import cos_batch as CB
from core.memory_sync import mc_pull as P

JOB_ID = "vault-cos-pull"
SOURCE = "minimoi-cos"
SOURCE_KIND = "cos"
DEFAULT_WATCH = "~/minimoi-staging/cos-export"
DEFAULT_STATE = "~/minimoi-staging/.vault-cos-pull"
EXIT_OK, EXIT_FAIL, EXIT_REFUSED = P.EXIT_OK, P.EXIT_FAIL, P.EXIT_REFUSED
identity_spec_ok = P.identity_spec_ok


def family(environment: str = "prod") -> P.PullFamily:
    if environment not in CB.ENVIRONMENTS:
        raise ValueError("environment must be dev, staging or prod")
    return P.PullFamily(job_id=JOB_ID, job_name="Vault CoS pull from production (Mac)", runbook="core/memory_sync/cos_pull.py",
                        batch=CB.COS, instance=environment, source=SOURCE, source_kind=SOURCE_KIND, default_watch=DEFAULT_WATCH,
                        default_state=DEFAULT_STATE)


def run_pull(*, dest, identity_spec, age_bin, expect_age, jobs_root, environment="prod", watch=DEFAULT_WATCH, state_dir=DEFAULT_STATE,
             config_path=None, registry_path=None, take_lock: bool = True, now=P.utcnow, usage=shutil.disk_usage,
             fetch_identity=B.fetch_identity, alert_send=None) -> dict:
    """One ``vault-cos-pull`` run (``mc_pull.run_pull`` with the CoS family). Returns ``{"exit", "state", "results", "summary"}``."""
    return P.run_pull(dest=dest, identity_spec=identity_spec, age_bin=age_bin, expect_age=expect_age, jobs_root=jobs_root, watch=watch,
                      state_dir=state_dir, config_path=config_path, registry_path=registry_path, take_lock=take_lock, now=now, usage=usage,
                      fetch_identity=fetch_identity, alert_send=alert_send, fam=family(environment))


def plan_pull(*, dest, environment="prod", watch=DEFAULT_WATCH, state_dir=DEFAULT_STATE, config_path=None, now=P.utcnow) -> dict:
    return P.plan_pull(dest=dest, watch=watch, state_dir=state_dir, config_path=config_path, now=now, fam=family(environment))

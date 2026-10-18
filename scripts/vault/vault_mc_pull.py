#!/usr/bin/env python3
"""vault_mc_pull.py: production's MC conversations into the Mac's minimoi-mc watch folder, then the approval-gated capture (job id
vault-mc-pull). Plan-only unless told to run.

  vault_mc_pull.py plan                                   unpulled COMPLETE batches (ids only), the watch folder, the minimoi-mc source's
                                                          approval state. Writes nothing, decrypts nothing.
  vault_mc_pull.py run --execute --identity SPEC          one run: verify, decrypt, place, acknowledge, then the minimoi-mc watcher pass.

The launchd agent comes from the pinned Vault runtime: ``runtime_install.py prepare`` writes the candidate
``com.vanstedum.minimoi-vault-mc-pull.plist`` (every 2 h, ``StartInterval`` 7200, and ``RunAtLoad``) only when the runtime profile names
``mc_pull_identity``. Never run it from a working checkout.

Settings (flag, else environment, else default):
  --identity       VAULT_BACKUP_IDENTITY          REQUIRED for run: file:/path | keychain:service/account | ssm:/name | cmd:program args.
                                                  A spec, never the key text; the key is never logged.
  --dest           MOI_VAULT_PUBLISH_DEST         s3://minimoi-backups/vault   (file:///dir for dev and tests)
  --watch          MOI_MC_WATCH                   ~/minimoi-staging/mc-export   (the minimoi-mc source root, 0700, outside data/)
  --state-dir      MOI_VAULT_MC_PULL_STATE        ~/minimoi-staging/.vault-mc-pull   (pulled.json, placed.json, work/ wiped)
  --config         MOI_CONFIG                     the shelf config holding the minimoi-mc source (config.load's default when unset)
  --age-bin        MOI_AGE_BIN                    ~/minimoi-staging/tools/age-v1.3.2/age
  --expect-age     MOI_AGE_VERSION                v1.3.2
  --jobs-root      MINIMOI_JOBS_ROOT              ~/minimoi-staging/data/jobs

It takes the shared <jobs-root>/vault-batch.lock first and without waiting, unless --no-batch-lock (busy: prints pull=busy to stderr, writes
nothing, not even a status, and exits 0). Exit codes:
0 ok, busy or an expected warn (a rejection, a capture waiting for approval, short of space), 1 failed, 2 refused (fixed reason on stderr).
Runbook: core/memory_sync/mc_pull.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.memory_sync import mc_pull                                       # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_REFUSED = mc_pull.EXIT_OK, mc_pull.EXIT_FAIL, mc_pull.EXIT_REFUSED


def env(flag, name, default=None):
    return flag if flag not in (None, "") else os.environ.get(name, default)


def settings(a) -> dict:
    return {
        "identity_spec": (env(a.identity, "VAULT_BACKUP_IDENTITY") or "").strip(),
        "dest": env(a.dest, "MOI_VAULT_PUBLISH_DEST", "s3://minimoi-backups/vault"),
        "watch": os.path.expanduser(env(a.watch, "MOI_MC_WATCH", mc_pull.DEFAULT_WATCH)),
        "state_dir": os.path.expanduser(env(a.state_dir, "MOI_VAULT_MC_PULL_STATE", mc_pull.DEFAULT_STATE)),
        "config_path": env(a.config, "MOI_CONFIG") or None,
        "age_bin": os.path.expanduser(env(a.age_bin, "MOI_AGE_BIN", "~/minimoi-staging/tools/age-v1.3.2/age")),
        "expect_age": env(a.expect_age, "MOI_AGE_VERSION", "v1.3.2"),
        "jobs_root": os.path.expanduser(env(a.jobs_root, "MINIMOI_JOBS_ROOT", "~/minimoi-staging/data/jobs")),
    }


def refuse(code: str) -> int:
    print(f"refused: {code}", file=sys.stderr)
    return EXIT_REFUSED


def cmd_plan(a) -> int:
    s = settings(a)
    plan = mc_pull.plan_pull(dest=s["dest"], watch=s["watch"], state_dir=s["state_dir"], config_path=s["config_path"])
    plan.update({"destination": s["dest"], "watch": s["watch"], "state_dir": s["state_dir"],
                 "identity_spec_valid": mc_pull.identity_spec_ok(s["identity_spec"]), "age_bin": s["age_bin"], "expect_age": s["expect_age"]})
    print(json.dumps(plan, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_run(a) -> int:
    if not a.execute:
        return refuse("plan_only_use_execute")
    s = settings(a)
    if not s["dest"]:
        return refuse("destination_required")
    if not s["identity_spec"]:
        return refuse("identity_required")
    if not mc_pull.identity_spec_ok(s["identity_spec"]):
        return refuse("identity_spec_invalid")
    out = mc_pull.run_pull(dest=s["dest"], identity_spec=s["identity_spec"], age_bin=s["age_bin"], expect_age=s["expect_age"],
                           jobs_root=s["jobs_root"], watch=s["watch"], state_dir=s["state_dir"], config_path=s["config_path"],
                           registry_path=a.registry, take_lock=not a.no_batch_lock)
    print(out["summary"], file=sys.stderr if out["state"] is None else sys.stdout)
    return out["exit"]


FLAGS = ("--identity", "--dest", "--watch", "--state-dir", "--config", "--age-bin", "--expect-age", "--jobs-root", "--registry")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for flag in FLAGS:
        ap.add_argument(flag)
    # The same flags after the subcommand (the plist's form: `run --execute --identity SPEC`); SUPPRESS keeps a flag given before it.
    common = argparse.ArgumentParser(add_help=False)
    for flag in FLAGS:
        common.add_argument(flag, default=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("plan", parents=[common])
    r = sub.add_parser("run", parents=[common])
    r.add_argument("--execute", action="store_true")
    r.add_argument("--no-batch-lock", action="store_true", help="the caller already holds the shared vault-batch lock")
    a = ap.parse_args(argv)
    return {"plan": cmd_plan, "run": cmd_run, None: cmd_plan}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

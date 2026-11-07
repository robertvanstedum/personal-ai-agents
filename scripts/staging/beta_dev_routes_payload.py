#!/usr/bin/env python3
"""Run on the Mac. Packages the routes operator from an exact commit, sends it to Dev through ssm_run.sh, starts it detached, and
reads its state once. ``build`` touches nothing on any host. Reuses beta_dev_payload's verified transfer.

  beta_dev_routes_payload.py build --operator-commit HEAD --out DIR
  beta_dev_routes_payload.py send --out DIR
  beta_dev_routes_payload.py start dry-run|apply|rollback [--failover openai]
  beta_dev_routes_payload.py read                     one bounded read of status, journal and log; never loops
  beta_dev_routes_payload.py check --operator-commit HEAD   one read: may a release update leave routes as they are?

Routes stay on through a release update (beta_dev_deploy.py update) when Dev's routes operator is this commit's: same
beta_dev_routes.py and mc_keys.sh, the files that decide what it switched on. The update itself refuses a change to anything
routes verified (ROUTES_SENSITIVE), and moves the routes operator's saved images on after a verified result.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import beta_dev_deploy as D                         # noqa: E402
import beta_dev_payload as P                        # noqa: E402
import beta_dev_routes as R                         # noqa: E402

HOST_DIR = f"{D.STAGING_ROOT}/operators/{R.OPERATOR_NAME}"
OPERATOR_FILES = ("scripts/staging/beta_dev_routes.py", "scripts/staging/beta_dev_deploy.py", "scripts/staging/guarded_swap.py", "scripts/staging/mc_keys.sh")


def operator_blobs(git, commit: str) -> dict:
    """The operator from a commit, and refused unless the code doing this build is that commit's."""
    out = {}
    for path in OPERATOR_FILES:
        data = git.blob(commit, path)
        assert data is not None, f"{path} is not in {commit}; commit it first"
        name = pathlib.Path(path).name
        assert (pathlib.Path(__file__).resolve().parent / name).read_bytes() == data, f"{path} differs from {commit}"
        out[name] = data
    return out


ROUTES_LOGIC = ("beta_dev_routes.py", "mc_keys.sh")    # what decides the override, the catalogue and the keys it made


def check_script() -> str:
    """One bounded read: is a routes override applied, and which routes operator made it."""
    P.check_destination(HOST_DIR)
    lines = [f"D={HOST_DIR}", '[ -e "$D/compose.routes.yml" ] || [ -e "$D/mc.routes.yml" ] && echo "APPLIED yes" || echo "APPLIED no"',
             '[ ! -e "$D/recovery-required.json" ] || echo "RECOVERY yes"']
    lines += [f'printf "SHA %s " {name}; sha256sum < "$D/release/operator/{name}" 2>/dev/null | cut -d" " -f1 || echo none' for name in ROUTES_LOGIC]
    return "\n".join(lines) + "\nexit 0\n"


def parse_check(out: str) -> dict:
    found = {"applied": False, "recovery": False, "sha": {}}
    for line in out.splitlines():
        words = line.split()
        if words[:2] == ["APPLIED", "yes"]:
            found["applied"] = True
        elif words[:2] == ["RECOVERY", "yes"]:
            found["recovery"] = True
        elif len(words) == 3 and words[0] == "SHA":
            found["sha"][words[1]] = words[2]
    return found


def routes_decision(local: dict, host: dict) -> tuple:
    """(verdict, words). local: {file name: sha256} at the operator commit. host: parse_check's result."""
    if host["recovery"]:
        return "stop", "the routes operator needs recovery: nothing else runs until someone has looked"
    if not host["applied"]:
        return "off", "routes are off on Dev: the update runs without them; run routes-on afterwards only if you want them on"
    differs = sorted(n for n in ROUTES_LOGIC if host["sha"].get(n) != local[n])
    if differs:
        return "cycle", "Dev's routes operator differs (" + ", ".join(differs) + "): routes-off, the update, routes-on"
    return "skip", "routes stay on: Dev's routes operator is this commit's, and the update refuses anything they verified"


def local_logic(git, commit: str) -> dict:
    out = {}
    for path in OPERATOR_FILES:
        name = pathlib.Path(path).name
        if name in ROUTES_LOGIC:
            data = git.blob(commit, path)
            assert data is not None, f"{path} is not in {commit}"
            out[name] = P.sha(data)
    return out


def scripts_for(tar: bytes, digest: str) -> list:
    scripts = P.chunk_scripts(tar, digest, HOST_DIR)
    guard = '[ ! -e "$D/compose.beta.yml" ]'
    assert scripts[-1].count(guard) == 1, "the transfer's last step changed"
    scripts[-1] = scripts[-1].replace(guard, '[ ! -e "$D/compose.routes.yml" ]')       # this operator's own applied mark
    return scripts


def start_script(action: str, failover: str) -> str:
    """Detached, because SSM kills long scripts. The operator holds the deploy lock itself, and writes status.json."""
    assert action in ("dry-run", "apply", "rollback"), "dry-run, apply or rollback"
    providers = ",".join(R.parse_failover(failover))
    P.check_destination(HOST_DIR)
    return f"""set -eu
D={HOST_DIR}
cd "$D"
[ -f release.sha256 ] || {{ echo 'no release on the host'; exit 1; }}
log="$D/run-{action}-$(date -u +%Y%m%dT%H%M%SZ).log"
umask 077
nohup setsid python3 "$D/release/operator/beta_dev_routes.py" {action} --failover {providers} > "$log" 2>&1 < /dev/null &
echo "started {action}; failover {providers}; log $log"
"""


def main(argv=None):                                # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--operator-commit", required=True)
    build.add_argument("--out", required=True)
    send = sub.add_parser("send")
    send.add_argument("--out", required=True)
    start = sub.add_parser("start")
    start.add_argument("action", choices=("dry-run", "apply", "rollback"))
    start.add_argument("--failover", default=R.DEFAULT_FAILOVER)
    sub.add_parser("read")
    check = sub.add_parser("check")
    check.add_argument("--operator-commit", required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        git, out = P.Git(), pathlib.Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        commit = git.rev(args.operator_commit)
        tar = P.make_tar({"release": R.OPERATOR_NAME, "commit": commit, "targets": {}}, {}, operator_blobs(git, commit))
        (out / P.TAR_NAME).write_bytes(tar)
        digest = P.sha(tar)
        (out / (P.TAR_NAME + ".sha256")).write_text(f"{digest}  {P.TAR_NAME}\n")
        print(f"{P.TAR_NAME}: {len(tar)} bytes, sha256 {digest}, operator from {commit}")
    elif args.command == "send":
        out = pathlib.Path(args.out)
        tar = (out / P.TAR_NAME).read_bytes()
        digest = (out / (P.TAR_NAME + ".sha256")).read_text().split()[0]
        D.check_release(tar, digest)
        for script in scripts_for(tar, digest):
            print(P.run_on_host(script).strip())
    elif args.command == "start":
        print(P.run_on_host(start_script(args.action, args.failover), 60))
    elif args.command == "check":
        git = P.Git()
        verdict, words = routes_decision(local_logic(git, git.rev(args.operator_commit)), parse_check(P.run_on_host(check_script(), 60)))
        print(f"ROUTES {verdict}: {words}")
    else:
        print(P.run_on_host(P.read_script(HOST_DIR), 60))


if __name__ == "__main__":                          # pragma: no cover
    main()

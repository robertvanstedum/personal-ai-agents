#!/usr/bin/env python3
"""Run on the Mac. Packages the combined 1.2 candidate from an exact commit, sends it to Dev through ssm_run.sh, starts the host
operator detached, and reads its state once. ``build`` and the tarball touch nothing on any host.

  beta_dev_payload.py build --commit e7017247 --operator-commit HEAD --out DIR
  beta_dev_payload.py send --out DIR [--start-part N]
  beta_dev_payload.py start dry-run|apply|rollback
  beta_dev_payload.py read                            one bounded read of status, journal and log; never loops
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import beta_dev_deploy as D                         # noqa: E402

SSM_RUN = pathlib.Path.home() / "minimoi-runtime/operators/dev-to-aws-staging-20261030/ssm_run.sh"
STAGING_INSTANCE = "i-0ad80f79eff0a7c4f"
REPO = pathlib.Path(__file__).resolve().parents[2]
HOST_DIR = f"{D.STAGING_ROOT}/operators/{D.OPERATOR_NAME}"
TAR_NAME = "release.tar.gz"
SSM_LIMIT = 97_000                                  # ssm_run.sh's base64 of the gzipped script must stay under this
CHUNK_BYTES = 60_000
OPERATOR_FILES = ("scripts/staging/beta_dev_deploy.py", "scripts/staging/guarded_swap.py")


def check_destination(path: str) -> None:
    """Dev's operator folder and nothing else."""
    D.refuse_production(path)
    assert path.startswith(D.STAGING_ROOT + "/operators/"), "only Dev's operator folder: " + path


class Git:
    def _out(self, *args) -> bytes:
        return subprocess.check_output(["git", *args], cwd=REPO)

    def rev(self, ref: str) -> str:
        return self._out("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def status(self, base: str, commit: str) -> list:
        """[(status letter, path)] for commit against base. A rename is a delete and an add."""
        out = []
        for line in self._out("diff", "--name-status", "--no-renames", base, commit).decode().splitlines():
            state, path = line.split("\t", 1)
            out.append((state[0], path))
        return out

    def blob(self, commit: str, path: str):
        try:
            return subprocess.check_output(["git", "cat-file", "blob", f"{commit}:{path}"], stderr=subprocess.DEVNULL, cwd=REPO)
        except subprocess.CalledProcessError:
            return None

    def mode(self, commit: str, path: str) -> str:
        row = self._out("ls-tree", commit, "--", path).decode().split()
        return "755" if row and row[0] == "100755" else "644"

    def versions(self, since: str, commit: str, path: str) -> list:
        """Every commit in since..commit that touched this path."""
        return self._out("log", "--format=%H", f"{since}..{commit}", "--", path).decode().split()

    def tree_hashes(self, commit: str, prefixes) -> dict:
        """{repo path: sha256} for files under these prefixes, read from the commit, written nowhere."""
        if not prefixes:
            return {}
        proc = subprocess.Popen(["git", "archive", "--format=tar", commit, *sorted({p.rstrip("/") for p in prefixes})], stdout=subprocess.PIPE, cwd=REPO)
        out = {}
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if member.isfile():
                    out[member.name] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
        assert proc.wait() == 0, "git archive failed"
        return out


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def accepted_hashes(git, commit: str, path: str) -> list:
    """Every committed version of this file since OLDEST_ACCEPTED. A file on the image that is none of them is a hand edit, and is refused."""
    found = set()
    for ref in [D.OLDEST_ACCEPTED, *git.versions(D.OLDEST_ACCEPTED, commit, path)]:
        data = git.blob(ref, path)
        if data is not None:
            found.add(sha(data))
    return sorted(found)


def build_meta(git, commit_ref: str, bases=None) -> tuple:
    """(meta, {repo path: bytes}). One entry per image: which changed files it holds, where, and every hash the host proves."""
    commit = git.rev(commit_ref)
    resolved = {k: git.rev(v) for k, v in (bases or D.BASES).items()}
    statuses = {k: git.status(v, commit) for k, v in resolved.items()}
    targets, skipped, blobs = {}, {}, {}
    tree_cache = git.tree_hashes(commit, sorted({p for s in D.TARGETS.values() for p in s.get("layout", ())}))
    for key, spec in D.TARGETS.items():
        changed = statuses[spec["base"]]
        deleted = [p for state, p in changed if state == "D" and D.dest_for(spec["rules"], p)]
        assert not deleted, f"{key}: an overlay cannot follow a deleted file: {deleted}"
        names = [p for state, p in changed if state != "D"]
        mapping = D.files_for(spec, names)
        if not mapping:
            skipped[spec["container"]] = "nothing it runs changed"
            continue
        files = {}
        always = dict(spec.get("always", ()))
        for path, dest in sorted(mapping.items()):
            data = git.blob(commit, path)
            assert data is not None, f"not in the commit: {path}"
            blobs[path] = data
            base_blob = git.blob(resolved[spec["base"]], path)
            files[path] = {"dest": dest, "after": sha(data), "mode": git.mode(commit, path), "new": base_blob is None,
                           "base": sha(base_blob) if base_blob is not None else None, "accepted": accepted_hashes(git, commit, path)}
            if path in always:                      # an input a RUN step compiles over: the file the image holds is the compiled one
                files[path]["generated_input"] = True
        out = {k: v for k, v in spec.items() if k not in ("rules", "extra_rules", "always", "layout", "exclude")}
        layout = {("/app/" + p): h for p, h in tree_cache.items()
                  if p.startswith(tuple(spec.get("layout", ()) or ("\0",))) and p.endswith(D.TEXT) and "/data/" not in p and p not in mapping}
        targets[key] = {**out, "files": files, "layout_files": layout}
    return {"release": D.RELEASE, "commit": commit, "bases": resolved, "oldest_accepted": git.rev(D.OLDEST_ACCEPTED), "targets": targets, "skipped": skipped}, blobs


def make_tar(meta: dict, blobs: dict, operator_files: dict) -> bytes:
    """Same inputs, same bytes: fixed times, root-owned, modes from git."""
    modes = {p: m["mode"] for t in meta["targets"].values() for p, m in t["files"].items()}
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        def add(name, data, mode=0o644):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime, info.uid, info.gid = len(data), mode, 0, 0, 0
            tar.addfile(info, io.BytesIO(data))
        add("meta.json", json.dumps(meta, indent=1, sort_keys=True).encode())
        for path in sorted(blobs):
            add("payload/" + path, blobs[path], int(modes[path], 8))
        for name, data in sorted(operator_files.items()):
            add("operator/" + name, data, 0o755)
    return buf.getvalue()


def chunk_scripts(tar: bytes, digest: str, host_dir: str = HOST_DIR, chunk_bytes: int = CHUNK_BYTES) -> list:
    """Scripts for ssm_run.sh: each writes one verified part; the last reassembles, verifies the whole hash, and unpacks."""
    check_destination(host_dir)
    parts = [tar[i:i + chunk_bytes] for i in range(0, len(tar), chunk_bytes)]
    scripts = []
    for n, part in enumerate(parts):
        scripts.append(f"""set -eu
umask 077
D={host_dir}
mkdir -p "$D/incoming"
chmod 700 "$D"
printf %s '{base64.b64encode(part).decode()}' | base64 -d > "$D/incoming/part-{n:05d}"
echo '{sha(part)}  '"$D/incoming/part-{n:05d}" | sha256sum -c - >/dev/null
echo 'part {n + 1} of {len(parts)}'
""")
    scripts.append(f"""set -eu
umask 022
D={host_dir}
[ ! -e "$D/recovery-required.json" ] || {{ echo 'an earlier run needs recovery; nothing sent'; exit 1; }}
[ ! -e "$D/compose.beta.yml" ] || {{ echo 'already applied: roll back first'; exit 1; }}
[ "$(ls "$D/incoming" | wc -l)" = {len(parts)} ] || {{ echo 'a part is missing'; exit 1; }}
cat "$D"/incoming/part-* > "$D/{TAR_NAME}.new"
echo '{digest}  '"$D/{TAR_NAME}.new" | sha256sum -c -
rm -rf "$D/release.new"
mkdir "$D/release.new"
tar -xzf "$D/{TAR_NAME}.new" -C "$D/release.new" --no-same-owner
rm -rf "$D/release"
mv "$D/release.new" "$D/release"
mv "$D/{TAR_NAME}.new" "$D/{TAR_NAME}"
echo '{digest}' > "$D/release.sha256"
rm -rf "$D/incoming"
echo 'release unpacked, sha256 {digest}'
""")
    return scripts


def envelope_size(script: str) -> int:
    """What ssm_run.sh actually hands to SSM: base64 of the gzip of the script, plus its small wrapper."""
    return len(base64.b64encode(gzip.compress(script.encode()))) + 200


def check_ssm_wrapper(path: pathlib.Path) -> None:
    """ssm_run.sh names its instance. It must be Dev's."""
    assert f"IID={STAGING_INSTANCE}" in path.read_text(), "ssm_run.sh does not target the Dev host"


def run_on_host(script: str, timeout_s: int = 120) -> str:
    check_ssm_wrapper(SSM_RUN)
    assert envelope_size(script) < SSM_LIMIT, "script over SSM's limit"
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(script)
    try:
        p = subprocess.run(["bash", str(SSM_RUN), f.name, str(timeout_s)], capture_output=True, text=True, timeout=timeout_s + 120)
    finally:
        os.unlink(f.name)
    if p.returncode or "[status: Success]" not in p.stdout:
        raise RuntimeError("host step failed:\n" + (p.stdout + p.stderr)[-1500:])
    return p.stdout


def start_script(action: str, host_dir: str = HOST_DIR) -> str:
    """Detached, because SSM kills long scripts. The operator holds the deploy lock itself, and writes status.json."""
    assert action in ("dry-run", "apply", "rollback"), "dry-run, apply or rollback"
    check_destination(host_dir)
    return f"""set -eu
D={host_dir}
cd "$D"
[ -f release.sha256 ] || {{ echo 'no release on the host'; exit 1; }}
log="$D/run-{action}-$(date -u +%Y%m%dT%H%M%SZ).log"
umask 077
nohup setsid python3 "$D/release/operator/beta_dev_deploy.py" {action} > "$log" 2>&1 < /dev/null &
echo "started {action}; log $log"
"""


def read_script(host_dir: str = HOST_DIR) -> str:
    """One bounded read."""
    check_destination(host_dir)
    return f"""D={host_dir}
cat "$D/status.json" 2>/dev/null; echo
[ ! -e "$D/recovery-required.json" ] || echo 'RECOVERY REQUIRED'
tail -n 15 "$D/journal.jsonl" 2>/dev/null
log=$(ls -t "$D"/run-*.log 2>/dev/null | head -n 1)
[ -z "$log" ] || tail -n 60 "$log" | cut -c1-240
exit 0
"""


def print_plan(meta: dict) -> None:
    for key, target in sorted(meta["targets"].items(), key=lambda kv: kv[1]["tier"]):
        print(f"tier {target['tier']} {target['container']}: {len(target['files'])} files")
        for path, m in sorted(target["files"].items()):
            state = "build input" if m.get("generated_input") else "new" if m["new"] else "changed"
            print(f"    {path} -> {m['dest']} ({state})")
    for name, why in meta["skipped"].items():
        print(f"skip {name}: {why}")


def operator_blobs(git, commit: str) -> dict:
    """The operator from a commit, and refused unless the code doing this build is that commit's."""
    out = {}
    for path in OPERATOR_FILES:
        data = git.blob(commit, path)
        assert data is not None, f"{path} is not in {commit}; commit it first"
        here = pathlib.Path(__file__).resolve().parent / pathlib.Path(path).name
        assert here.read_bytes() == data, f"{path} differs from {commit}"
        out[pathlib.Path(path).name] = data
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--commit", required=True)
    build.add_argument("--operator-commit", required=True)
    build.add_argument("--out", required=True)
    send = sub.add_parser("send")
    send.add_argument("--out", required=True)
    send.add_argument("--start-part", type=int, default=0)
    start = sub.add_parser("start")
    start.add_argument("action", choices=("dry-run", "apply", "rollback"))
    sub.add_parser("read")
    args = parser.parse_args(argv)

    if args.command == "build":
        git, out = Git(), pathlib.Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        meta, blobs = build_meta(git, args.commit)
        tar = make_tar(meta, blobs, operator_blobs(git, git.rev(args.operator_commit)))
        (out / TAR_NAME).write_bytes(tar)
        digest = sha(tar)
        (out / (TAR_NAME + ".sha256")).write_text(f"{digest}  {TAR_NAME}\n")
        print_plan(meta)
        print(f"{TAR_NAME}: {len(tar)} bytes, sha256 {digest}, from {meta['commit']}")
    elif args.command == "send":
        out = pathlib.Path(args.out)
        tar = (out / TAR_NAME).read_bytes()
        digest = (out / (TAR_NAME + ".sha256")).read_text().split()[0]
        D.check_release(tar, digest)
        scripts = chunk_scripts(tar, digest)
        for n, script in enumerate(scripts):
            if n >= args.start_part or n == len(scripts) - 1:
                print(run_on_host(script).strip())
    elif args.command == "start":
        print(run_on_host(start_script(args.action), 60))
    else:
        print(run_on_host(read_script(), 60))


if __name__ == "__main__":
    main()

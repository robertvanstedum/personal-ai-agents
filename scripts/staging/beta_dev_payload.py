#!/usr/bin/env python3
"""Run on the Mac. Packages the combined 1.2 candidate from an exact commit, sends it to Dev through ssm_run.sh, starts the host
operator detached, and reads its state once. ``build`` and the tarball touch nothing on any host.

  beta_dev_payload.py build --commit e7017247 --operator-commit HEAD --out DIR [--release NAME]
  beta_dev_payload.py build --commit C --from DEV_COMMIT --release NAME --operator-commit HEAD --out DIR    an update
  beta_dev_payload.py send --out DIR [--start-part N]  one SSM call when the tarball fits, verified parts when it does not
  beta_dev_payload.py start dry-run|apply|rollback|update-dry-run|update
  beta_dev_payload.py read                            one bounded read of status, journal and log; never loops

An update (``--from``) ships only what changed since the commit Dev runs, to release-next/ beside the running release. The
host refuses it unless Dev runs exactly that commit, then rebuilds only the containers those files reach, FROM what they run.
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

    def titles(self, since: str, commit: str, limit: int) -> list:
        """[(short hash, title)] for since..commit, newest first, at most ``limit``."""
        out = self._out("log", f"--max-count={limit}", "--format=%h%x09%s", f"{since}..{commit}").decode().splitlines()
        return [tuple(line.split("\t", 1)) for line in out if "\t" in line]

    def subject(self, commit: str) -> str:
        return self._out("log", "-1", "--format=%s", commit).decode().strip()

    def count(self, since: str, commit: str) -> int:
        return int(self._out("rev-list", "--count", f"{since}..{commit}").decode().strip())

    def branches(self, commit: str) -> list:
        return self._out("for-each-ref", "--points-at", commit, "--format=%(refname:short)", "refs/heads").decode().split()

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


def replaced_trees(git, commit: str, spec: dict) -> dict:
    """{repo path: image path} for every commit file under a folder the build empties first (MC's persona and handbook):
    the overlay must carry all of it, or the image loses what did not change."""
    replaces = spec.get("replaces", ())
    if not replaces:
        return {}
    sources = [src for src, dest in spec["rules"] if dest.startswith(tuple(replaces))]
    out = {}
    for path in git.tree_hashes(commit, sources):
        dest = D.dest_for(spec["rules"], path)
        if dest and dest.startswith(tuple(replaces)):
            out[path] = dest
    return out


def build_meta(git, commit_ref: str, bases=None, from_ref=None, release=None) -> tuple:
    """(meta, {repo path: bytes}). One entry per image: which changed files it holds, where, and every hash the host proves.
    With ``from_ref`` (the commit Dev runs) every image diffs from there: an update carries only what changed since."""
    commit = git.rev(commit_ref)
    declared = resolved = {k: git.rev(v) for k, v in (bases or D.BASES).items()}
    from_commit = git.rev(from_ref) if from_ref else None
    if from_commit:
        resolved = {k: from_commit for k in resolved}
    catching_up = from_commit and any(s.get("catch_up") for s in D.TARGETS.values())
    statuses = {c: git.status(c, commit) for c in set(resolved.values()) | (set(declared.values()) if catching_up else set())}
    targets, skipped, blobs = {}, {}, {}
    tree_cache = git.tree_hashes(commit, sorted({p for s in D.TARGETS.values() for p in s.get("layout", ())}))
    tree = {"/app/" + p: h for p, h in tree_cache.items() if p.endswith(D.TEXT) and "/data/" not in p}
    for key, spec in D.TARGETS.items():
        changed = statuses[resolved[spec["base"]]]
        deleted = [p for state, p in changed if state == "D" and D.dest_for(spec["rules"], p)]
        assert not deleted, f"{key}: an overlay cannot follow a deleted file: {deleted}"
        names = [p for state, p in changed if state != "D"]
        mapping = D.files_for(spec, names)
        if not mapping:
            skipped[spec["container"]] = "nothing it runs changed"
            continue
        if from_commit and spec.get("catch_up"):    # a folder the running release never laid out: all of it since the base, as
            since_base = statuses[declared[spec["base"]]]       # the full chain ships it; only when the image is rebuilt anyway
            inside = lambda p: p.startswith(tuple(spec["catch_up"]))
            gone = [p for state, p in since_base if state == "D" and inside(p) and D.dest_for(spec["rules"], p)]
            assert not gone, f"{key}: an overlay cannot follow a deleted file: {gone}"
            mapping.update(D.files_for(spec, [p for state, p in since_base if state != "D" and inside(p)]))
        mapping.update(replaced_trees(git, commit, spec))
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
        out = {k: v for k, v in spec.items() if k not in ("rules", "extra_rules", "always", "layout", "exclude", "catch_up")}
        if from_commit:                             # one digest, recomputed in the built image from the shared path list
            paths = D.layout_paths(tree, spec.get("layout", ()), mapping)
            targets[key] = {**out, "files": files, "layout_digest": {"prefixes": list(spec.get("layout", ())), "count": len(paths),
                                                                       "sha256": D.layout_digest({p: tree[p] for p in paths})}}
        else:
            layout = {("/app/" + p): h for p, h in tree_cache.items()
                      if p.startswith(tuple(spec.get("layout", ()) or ("\0",))) and p.endswith(D.TEXT) and "/data/" not in p and p not in mapping}
            targets[key] = {**out, "files": files, "layout_files": layout}
    meta = {"release": release or D.RELEASE, "commit": commit, "bases": resolved, "oldest_accepted": git.rev(D.OLDEST_ACCEPTED), "targets": targets,
            "skipped": skipped, "delivery": delivery_meta(git, commit)}
    if from_commit:
        assert release, "an update needs a release name of its own (--release)"
        meta.update(mode="update", tree=sorted(tree), **{"from": {"commit": from_commit}})
    return meta, blobs


MAIN = "origin/main"                                # production deploys from main: what main lacks is on Dev only
DELIVERY_TITLES = 40


def delivery_meta(git, commit: str, main_ref: str = MAIN) -> dict:
    """What MC and CoS read as Dev's delivery record: this commit, main as packaged, and the commits Dev has that main lacks."""
    main = git.rev(main_ref)
    titles = git.titles(main, commit, DELIVERY_TITLES)
    return {"commit": commit, "title": git.subject(commit), "branches": git.branches(commit),
            "main": main, "main_ref": main_ref, "ahead_of_main": git.count(main, commit), "titles": [list(t) for t in titles]}


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


# Where a transfer lands, and what must be true there first. "release": a full release, only while nothing is applied.
# "release-next": an update, only while a release is applied and no earlier update is unresolved. The running release stays.
LANDINGS = {
    "release": ("release", "incoming", """[ ! -e "$D/compose.beta.yml" ] || { echo 'already applied: roll back first'; exit 1; }"""),
    "release-next": ("release-next", "incoming-next",
                     """[ -e "$D/compose.beta.yml" ] || { echo 'nothing applied: an update needs an applied release'; exit 1; }\n"""
                     f"""[ ! -e "$D/{D.UPDATE_IN_FLIGHT}" ] || {{ echo 'an earlier update did not finish; nothing sent'; exit 1; }}"""),
}


def _part_script(part: bytes, n: int, total: int, host_dir: str, incoming: str) -> str:
    return f"""set -eu
umask 077
D={host_dir}
mkdir -p "$D/{incoming}"
chmod 700 "$D"
printf %s '{base64.b64encode(part).decode()}' | base64 -d > "$D/{incoming}/part-{n:05d}"
echo '{sha(part)}  '"$D/{incoming}/part-{n:05d}" | sha256sum -c - >/dev/null
echo 'part {n + 1} of {total}'
"""


def _unpack_body(digest: str, landing: str, parts: int) -> str:
    """Everything after the opening ``set -eu``/``umask``/``D=`` lines: guards, reassembly, the whole hash, unpack."""
    name, incoming, guards = LANDINGS[landing]
    return f"""[ ! -e "$D/recovery-required.json" ] || {{ echo 'an earlier run needs recovery; nothing sent'; exit 1; }}
{guards}
[ "$(ls "$D/{incoming}" | wc -l | tr -d ' ')" = {parts} ] || {{ echo 'a part is missing'; exit 1; }}
cat "$D"/{incoming}/part-* > "$D/{name}.tar.gz.new"
echo '{digest}  '"$D/{name}.tar.gz.new" | sha256sum -c -
rm -rf "$D/{name}.new"
mkdir "$D/{name}.new"
tar -xzf "$D/{name}.tar.gz.new" -C "$D/{name}.new" --no-same-owner
rm -rf "$D/{name}"
mv "$D/{name}.new" "$D/{name}"
mv "$D/{name}.tar.gz.new" "$D/{name}.tar.gz"
echo '{digest}' > "$D/{name}.sha256"
rm -rf "$D/{incoming}"
echo '{name} unpacked, sha256 {digest}'
"""


def chunk_scripts(tar: bytes, digest: str, host_dir: str = HOST_DIR, chunk_bytes: int = CHUNK_BYTES, landing: str = "release") -> list:
    """Scripts for ssm_run.sh: each writes one verified part; the last reassembles, verifies the whole hash, and unpacks.
    The last part carries the unpack when both fit in one envelope: a tarball of one part is one SSM call."""
    check_destination(host_dir)
    incoming = LANDINGS[landing][1]
    parts = [tar[i:i + chunk_bytes] for i in range(0, len(tar), chunk_bytes)]
    scripts = [_part_script(part, n, len(parts), host_dir, incoming) for n, part in enumerate(parts)]
    body = _unpack_body(digest, landing, len(parts))
    joined = scripts[-1] + "umask 022\n" + body
    if envelope_size(joined) < SSM_LIMIT:
        return [*scripts[:-1], joined]
    return [*scripts, f"set -eu\numask 022\nD={host_dir}\n" + body]


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


ACTIONS = ("dry-run", "apply", "rollback", "update-dry-run", "update")


def landing_for(tar: bytes) -> str:
    """An update's tarball goes beside the running release; a full one replaces release/ (only while nothing is applied)."""
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:gz") as t:
        meta = json.load(t.extractfile("meta.json"))
    return "release-next" if meta.get("mode") == "update" else "release"


def start_script(action: str, host_dir: str = HOST_DIR) -> str:
    """Detached, because SSM kills long scripts. The operator holds the deploy lock itself, and writes status.json."""
    assert action in ACTIONS, ", ".join(ACTIONS)
    check_destination(host_dir)
    where = "release-next" if action.startswith("update") else "release"     # an update runs the operator it was sent with
    return f"""set -eu
D={host_dir}
cd "$D"
[ -f {where}.sha256 ] || {{ echo 'no {where} on the host'; exit 1; }}
log="$D/run-{action}-$(date -u +%Y%m%dT%H%M%SZ).log"
umask 077
nohup setsid python3 "$D/{where}/operator/beta_dev_deploy.py" {action} > "$log" 2>&1 < /dev/null &
echo "started {action}; log $log"
"""


def read_script(host_dir: str = HOST_DIR) -> str:
    """One bounded read."""
    check_destination(host_dir)
    return f"""D={host_dir}
cat "$D/status.json" 2>/dev/null; echo
if [ -e "$D/compose.beta.yml" ]; then
  python3 -c 'import json,sys;m=json.load(open(sys.argv[1]));print("APPLIED", m.get("release"), m.get("commit"), "(an update builds --from this commit)")' "$D/release/meta.json" 2>/dev/null || true
else echo 'APPLIED nothing: Dev runs its pre-Beta baseline'; fi
[ ! -e "$D/update-in-flight.json" ] || echo 'UPDATE IN FLIGHT'
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
    build.add_argument("--release", help="this release's name; its tags. Default: " + D.RELEASE)
    build.add_argument("--from", dest="from_ref", help="the commit Dev runs: build an update of only what changed since")
    send = sub.add_parser("send")
    send.add_argument("--out", required=True)
    send.add_argument("--start-part", type=int, default=0)
    start = sub.add_parser("start")
    start.add_argument("action", choices=ACTIONS)
    sub.add_parser("read")
    args = parser.parse_args(argv)

    if args.command == "build":
        git, out = Git(), pathlib.Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        meta, blobs = build_meta(git, args.commit, from_ref=args.from_ref, release=args.release)
        tar = make_tar(meta, blobs, operator_blobs(git, git.rev(args.operator_commit)))
        (out / TAR_NAME).write_bytes(tar)
        digest = sha(tar)
        (out / (TAR_NAME + ".sha256")).write_text(f"{digest}  {TAR_NAME}\n")
        print_plan(meta)
        sends = len(chunk_scripts(tar, digest, landing=landing_for(tar)))
        print(f"{TAR_NAME}: {len(tar)} bytes, sha256 {digest}, {meta['release']} from {meta['commit']}"
              + (f", an update from {meta['from']['commit'][:8]}" if meta.get("mode") == "update" else "") + f"; {sends} SSM call(s) to send")
    elif args.command == "send":
        out = pathlib.Path(args.out)
        tar = (out / TAR_NAME).read_bytes()
        digest = (out / (TAR_NAME + ".sha256")).read_text().split()[0]
        D.check_release(tar, digest)
        scripts = chunk_scripts(tar, digest, landing=landing_for(tar))
        for n, script in enumerate(scripts):
            if n >= args.start_part or n == len(scripts) - 1:
                print(run_on_host(script).strip())
    elif args.command == "start":
        print(run_on_host(start_script(args.action), 60))
    else:
        print(run_on_host(read_script(), 60))


if __name__ == "__main__":
    main()

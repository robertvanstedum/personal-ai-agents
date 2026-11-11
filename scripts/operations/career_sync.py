#!/usr/bin/env python3
"""career-sync: the owner's private sources, Mac → host, as encrypted, content-addressed releases.

Spec: docs/specs/spec_1_2_data_map_and_private_sync_v1.0_2026-11-11.md (§3.2, §4). Standard library only, Python 3.9+,
because the host side runs on the EC2 host's own python with no checkout (the same constraint as the Vault's EC2
bundle). Paths, recipients and the publish set come from a config file outside this repo; nothing personal is here.

Mac, ``publish``:
  * Collects the publish set (folders relative to ``workspace``): regular files only; symlinks refused; ``.git``,
    Office lock files (``~$*``), ``.DS_Store`` and the ``never_copy`` globs skipped.
  * ``MANIFEST.json``: per file path, sha256, size, mtime and ``dirty`` (uncommitted or untracked in the workspace's
    git repo), plus that repo's HEAD. The manifest hash is the sha256 of the canonical JSON of the file list and HEAD
    only, so an unchanged tree has the same hash.
  * A deterministic tar (sorted, fixed owner and times) holding ``MANIFEST.json`` and ``tree/…``, ``age``-encrypted to
    every configured recipient, stored as ``career-sync/<manifest-hash>.tar.age`` only if that key does not exist,
    then the small pointer ``career-sync/latest.json``. Plaintext scratch is wiped.

Host, ``apply`` (as root):
  * Follows the pointer, downloads the object into ``<private>/.work`` (0700, same filesystem), checks the ciphertext
    hash, decrypts with the root-only identity file, extracts with a strict extractor (regular files and folders only,
    no absolute or ``..`` paths), checks that the extracted files are exactly the manifest's, byte for byte.
  * For each configured reader (spec D14: one copy per reader uid) the release becomes ``<reader dir>/releases/<hash>/``
    owned by that uid, folders 0700, files 0400, and ``<reader dir>/current`` is switched by an atomic rename of a
    relative symlink. The current release and one previous are kept; older ones are removed; current is never removed.
  * A failed hash, decrypt or check moves the download to ``<reader dir>/quarantine/`` and leaves ``current`` where it is.
  * Writes ``RECEIPT.json`` per reader and, for the Work reader, ``work_source_roots.json`` naming the REAL release
    path of each configured root (``roots.py`` drops a path that contains a symlink). Loading it into cos-scheduler is a
    separate, gated step: this tool restarts nothing.

``rollback`` points ``current`` back at the previous retained release, for every reader. ``status`` prints state.
Exit codes: 0 ok or no-op, 2 refused (with a reason code on stderr), 3 failed.
"""
from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

FORMAT = "career-sync/1"
PREFIX = "career-sync"
POINTER = "latest.json"
RECIPIENT = re.compile(r"^age1[0-9a-z]{58}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
ALWAYS_SKIP = ("~$*", ".DS_Store", "._*")
KEEP = 2


class Refused(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code, self.detail = code, detail


# ── small helpers ──
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_whole(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write a file whole: temp file, fsync, rename, fsync the folder."""
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    fsync_dir(path.parent)


def fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def wipe(path: Path) -> None:
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            f.write(b"\0" * size)
            f.flush()
            os.fsync(f.fileno())
        path.unlink()
    except FileNotFoundError:
        pass


def check_age(age_bin: str) -> str:
    try:
        done = subprocess.run([age_bin, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("age_unavailable", age_bin) from None
    if done.returncode != 0:
        raise Refused("age_unavailable", age_bin)
    return done.stdout.strip()


# ── destinations: file:///dir (tests, dry runs) or s3://bucket/prefix ──
class Dest:
    def __init__(self, url: str, aws_bin: str = "aws", run=subprocess.run) -> None:
        self.url, self.aws, self.run = url.rstrip("/"), aws_bin, run
        if url.startswith("file://"):
            self.kind, self.root = "file", Path(url[len("file://"):])
        elif url.startswith("s3://"):
            self.kind, self.root = "s3", None
        else:
            raise Refused("dest_unknown", url)

    def _key(self, key: str) -> str:
        return f"{self.url}/{key}"

    def exists(self, key: str) -> bool:
        if self.kind == "file":
            return (self.root / key).exists()
        bucket, _, prefix = self.url[len("s3://"):].partition("/")
        full = f"{prefix}/{key}" if prefix else key
        done = self.run([self.aws, "s3api", "head-object", "--bucket", bucket, "--key", full], capture_output=True, text=True)
        return done.returncode == 0

    def put(self, src: Path, key: str) -> None:
        if self.kind == "file":
            dst = self.root / key
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            return
        done = self.run([self.aws, "s3", "cp", "--only-show-errors", "--sse", "AES256", str(src), self._key(key)], capture_output=True, text=True)
        if done.returncode != 0:
            raise Refused("upload_failed", done.stderr.strip()[-300:])

    def get(self, key: str, dst: Path) -> None:
        if self.kind == "file":
            src = self.root / key
            if not src.exists():
                raise Refused("object_missing", key)
            shutil.copyfile(src, dst)
            return
        done = self.run([self.aws, "s3", "cp", "--only-show-errors", self._key(key), str(dst)], capture_output=True, text=True)
        if done.returncode != 0:
            raise Refused("download_failed", done.stderr.strip()[-300:])

    def list(self, prefix: str) -> list:
        if self.kind == "file":
            base = self.root / prefix
            return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()) if base.exists() else []
        bucket, _, root = self.url[len("s3://"):].partition("/")
        full = f"{root}/{prefix}" if root else prefix
        done = self.run([self.aws, "s3api", "list-objects-v2", "--bucket", bucket, "--prefix", full, "--query", "Contents[].Key", "--output", "json"],
                        capture_output=True, text=True)
        if done.returncode != 0:
            raise Refused("list_failed", done.stderr.strip()[-300:])
        keys = json.loads(done.stdout or "null") or []
        return sorted(k[len(root) + 1:] if root else k for k in keys)


# ── Mac: publish ──
def git_state(repo: Path) -> tuple:
    """(HEAD or None, set of dirty paths relative to repo)."""
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        out = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z", "--untracked-files=all"],
                             capture_output=True, check=True).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        return None, None
    dirty, parts, i = set(), out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            dirty.add(entry[3:])
            if entry[0] in "RC":          # a rename carries its source as the next field
                i += 1
        i += 1
    return head, dirty


def skipped(rel: PurePosixPath, never_copy: list) -> bool:
    if ".git" in rel.parts:
        return True
    name = rel.name
    if any(fnmatch.fnmatch(name, g) for g in ALWAYS_SKIP):
        return True
    return any(fnmatch.fnmatch(str(rel), g) or fnmatch.fnmatch(name, g) for g in never_copy)


def collect(workspace: Path, publish_set: list, never_copy: list, repo: Path | None) -> dict:
    head, dirty = git_state(repo) if repo else (None, None)
    files = []
    for top in publish_set:
        base = workspace / top
        if base.is_symlink() or not base.is_dir():
            raise Refused("publish_set_missing", top)
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = PurePosixPath(path.relative_to(workspace).as_posix())
                if skipped(rel, never_copy):
                    continue
                st = path.lstat()
                if stat.S_ISLNK(st.st_mode):
                    raise Refused("symlink_in_publish_set", str(rel))
                if not stat.S_ISREG(st.st_mode):
                    continue
                in_repo = None
                if repo is not None and dirty is not None:
                    try:
                        in_repo = path.resolve().relative_to(repo.resolve()).as_posix()
                    except ValueError:
                        in_repo = None
                files.append({
                    "path": str(rel),
                    "sha256": sha256_file(path),
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                    "dirty": True if in_repo is None else (in_repo in dirty),
                })
    files.sort(key=lambda f: f["path"])
    if not files:
        raise Refused("publish_set_empty")
    body = {"format": FORMAT, "head": head, "files": files}
    return {**body, "manifest_hash": hashlib.sha256(canonical(body)).hexdigest()}


def build_tar(workspace: Path, manifest: dict, out: Path) -> None:
    def info(name: str, size: int, is_dir: bool = False) -> tarfile.TarInfo:
        ti = tarfile.TarInfo(name)
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        ti.mtime = 0
        if is_dir:
            ti.type, ti.mode = tarfile.DIRTYPE, 0o700
        else:
            ti.size, ti.mode = size, 0o600
        return ti

    raw = canonical({k: v for k, v in manifest.items()})
    with open(out, "wb") as fh:
        with gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                tar.addfile(info("MANIFEST.json", len(raw)), io.BytesIO(raw))
                for f in manifest["files"]:
                    src = workspace / f["path"]
                    with open(src, "rb") as data:
                        tar.addfile(info("tree/" + f["path"], f["size"]), data)
                    if sha256_file(src) != f["sha256"]:
                        raise Refused("file_changed_during_publish", f["path"])


def publish(cfg: dict, *, dry_run: bool = False) -> dict:
    age_bin = cfg.get("age_bin", "age")
    recipients = cfg["recipients"]
    if not recipients or not all(RECIPIENT.match(r) for r in recipients):
        raise Refused("recipient_malformed")
    workspace = Path(cfg["workspace"]).expanduser()
    repo = Path(cfg["git_repo"]).expanduser() if cfg.get("git_repo") else None
    dest = Dest(cfg["dest"], cfg.get("aws_bin", "aws"))
    manifest = collect(workspace, cfg["publish_set"], cfg.get("never_copy", []), repo)
    h = manifest["manifest_hash"]
    key = f"{PREFIX}/{h}.tar.age"
    pending = unpulled_inbound(dest)
    if pending:
        raise Refused("inbound_not_pulled", f"{len(pending)} inbound bundle(s); run pull first")
    result = {"manifest_hash": h, "files": len(manifest["files"]), "dirty": sum(f["dirty"] for f in manifest["files"]),
              "head": manifest["head"], "object": key}
    if dry_run:
        return {**result, "dry_run": True}
    check_age(age_bin)
    work = Path(tempfile.mkdtemp(prefix="career-sync-"))
    os.chmod(work, 0o700)
    try:
        plain, enc = work / "bundle.tar.gz", work / "bundle.tar.gz.age"
        build_tar(workspace, manifest, plain)
        args = [age_bin]
        for r in recipients:
            args += ["-r", r]
        done = subprocess.run(args + ["-o", str(enc), str(plain)], capture_output=True, text=True)
        wipe(plain)
        if done.returncode != 0:
            raise Refused("encrypt_failed", done.stderr.strip()[-300:])
        stored = False
        if not dest.exists(key):
            dest.put(enc, key)
            stored = True
        pointer = {"format": FORMAT, "manifest_hash": h, "object": key, "ciphertext_sha256": sha256_file(enc),
                   "files": len(manifest["files"]), "head": manifest["head"], "published_at": now_utc()}
        if stored or not dest.exists(f"{PREFIX}/{POINTER}") or current_pointer_hash(dest) != h:
            ptr = work / POINTER
            ptr.write_bytes(canonical(pointer))
            dest.put(ptr, f"{PREFIX}/{POINTER}")
        return {**result, "stored_new_object": stored, "ciphertext_sha256": pointer["ciphertext_sha256"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def current_pointer_hash(dest: Dest):
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / POINTER
            dest.get(f"{PREFIX}/{POINTER}", p)
            return json.loads(p.read_text()).get("manifest_hash")
    except (Refused, ValueError):
        return None


def unpulled_inbound(dest: Dest) -> list:
    """Inbound bundles the host wrote that the Mac has not acknowledged (spec §4: publish refuses until pulled)."""
    bundles = {Path(k).name[:-len(".tar.age")] for k in dest.list("career-inbound/") if k.endswith(".tar.age")}
    acks = {Path(k).stem for k in dest.list("career-inbound-acks/") if k.endswith(".json")}
    return sorted(bundles - acks)


# ── host: apply ──
def safe_extract(archive: Path, target: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            p = PurePosixPath(m.name)
            if p.is_absolute() or ".." in p.parts or not (m.isfile() or m.isdir()):
                raise Refused("archive_unsafe", m.name)
        for m in members:
            dst = target / m.name
            if m.isdir():
                dst.mkdir(parents=True, exist_ok=True)
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(m)
            with open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
                out.flush()
                os.fsync(out.fileno())


def verify_tree(build: Path, expect_hash: str) -> dict:
    manifest = json.loads((build / "MANIFEST.json").read_text())
    body = {k: manifest[k] for k in ("format", "head", "files")}
    if hashlib.sha256(canonical(body)).hexdigest() != expect_hash or manifest.get("manifest_hash") != expect_hash:
        raise Refused("manifest_hash_mismatch")
    tree = build / "tree"
    want = {f["path"]: f["sha256"] for f in manifest["files"]}
    have = {p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file()} if tree.exists() else set()
    if have != set(want):
        raise Refused("tree_does_not_match_manifest", f"extra={len(have - set(want))} missing={len(set(want) - have)}")
    for rel, digest in want.items():
        if sha256_file(tree / rel) != digest:
            raise Refused("file_hash_mismatch", rel)
    return manifest


def seal(root: Path, uid: int, gid: int) -> None:
    """Owner = reader uid; folders 0700, files 0400; nothing for group or other."""
    for dirpath, dirnames, filenames in os.walk(root):
        own(dirpath, uid, gid)
        os.chmod(dirpath, 0o700)
        for name in filenames:
            p = os.path.join(dirpath, name)
            own(p, uid, gid)
            os.chmod(p, 0o400)
    for dirpath, _, _ in sorted(os.walk(root), key=lambda t: -len(t[0])):
        fsync_dir(Path(dirpath))


def switch_current(reader_dir: Path, release: str) -> None:
    link, tmp = reader_dir / "current", reader_dir / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(f"releases/{release}", tmp)
    os.replace(tmp, link)
    fsync_dir(reader_dir)


def current_release(reader_dir: Path):
    link = reader_dir / "current"
    return os.readlink(link).split("/", 1)[1] if link.is_symlink() else None


def retained(reader_dir: Path) -> list:
    rel = reader_dir / "releases"
    if not rel.exists():
        return []
    entries = [p for p in rel.iterdir() if p.is_dir() and HASH.match(p.name)]
    return [p.name for p in sorted(entries, key=lambda p: p.stat().st_mtime)]


def prune(reader_dir: Path, keep: int = KEEP) -> list:
    cur, removed = current_release(reader_dir), []
    names = retained(reader_dir)
    for name in names[:-keep] if len(names) > keep else []:
        if name != cur:
            shutil.rmtree(reader_dir / "releases" / name)
            removed.append(name)
    return removed


def own(path, uid: int, gid: int) -> None:
    """chown as root. Without root (tests only, see ``apply``) ownership is left as is."""
    if os.geteuid() == 0:
        os.chown(path, uid, gid)


def ensure_dir(path: Path, uid: int, gid: int, mode: int = 0o700) -> None:
    if path.is_symlink():
        raise Refused("unsafe_path", str(path))
    path.mkdir(parents=True, exist_ok=True)
    own(path, uid, gid)
    os.chmod(path, mode)


def apply(cfg: dict) -> dict:
    private = Path(cfg["private_root"])
    readers = cfg["readers"]           # [{"name", "dir", "uid", "gid", "work_roots": bool}]
    age_bin, dest = cfg.get("age_bin", "age"), Dest(cfg["dest"], cfg.get("aws_bin", "aws"))
    if os.geteuid() != 0 and not cfg.get("test_without_root"):
        raise Refused("needs_root", "apply sets each reader's ownership and must run as root on the host")
    check_age(age_bin)
    work_parent = private / ".work"
    ensure_dir(private, 0, 0, 0o711)
    ensure_dir(work_parent, 0, 0)
    work = Path(tempfile.mkdtemp(prefix="apply-", dir=str(work_parent)))
    try:
        ptr = work / POINTER
        dest.get(f"{PREFIX}/{POINTER}", ptr)
        pointer = json.loads(ptr.read_text())
        h = pointer["manifest_hash"]
        if not HASH.match(h) or pointer["object"] != f"{PREFIX}/{h}.tar.age":
            raise Refused("pointer_malformed")
        if all(current_release(Path(r["dir"])) == h and (Path(r["dir"]) / "releases" / h).is_dir() for r in readers):
            return {"result": "unchanged", "manifest_hash": h}
        enc = work / "bundle.tar.gz.age"
        dest.get(pointer["object"], enc)
        try:
            if sha256_file(enc) != pointer["ciphertext_sha256"]:
                raise Refused("ciphertext_hash_mismatch")
            plain = work / "bundle.tar.gz"
            done = subprocess.run([age_bin, "-d", "-i", cfg["identity_file"], "-o", str(plain), str(enc)], capture_output=True, text=True)
            if done.returncode != 0:
                raise Refused("decrypt_failed")
            build = work / "build"
            build.mkdir(mode=0o700)
            safe_extract(plain, build)
            wipe(plain)
            manifest = verify_tree(build, h)
        except Refused:
            for r in readers:
                q = Path(r["dir"]) / "quarantine"
                ensure_dir(Path(r["dir"]), r["uid"], r["gid"])
                ensure_dir(q, 0, 0)
                shutil.copyfile(enc, q / f"{h}-{int(time.time())}.tar.age")
            raise
        applied = []
        for r in readers:
            rdir = Path(r["dir"])
            ensure_dir(rdir, r["uid"], r["gid"])
            ensure_dir(rdir / "releases", r["uid"], r["gid"])
            final = rdir / "releases" / h
            if not final.is_dir():
                partial = rdir / "releases" / f".{h}.partial"
                if partial.exists():
                    shutil.rmtree(partial)
                shutil.copytree(build, partial)
                seal(partial, r["uid"], r["gid"])
                os.replace(partial, final)
                fsync_dir(rdir / "releases")
            verify_tree(final, h)
            previous = current_release(rdir)
            switch_current(rdir, h)
            receipt = {"format": FORMAT, "manifest_hash": h, "head": manifest["head"], "files": len(manifest["files"]),
                       "previous": previous, "applied_at": now_utc(), "reader": r["name"], "uid": r["uid"],
                       "ciphertext_sha256": pointer["ciphertext_sha256"]}
            write_whole(rdir / "RECEIPT.json", canonical(receipt), 0o600)
            own(rdir / "RECEIPT.json", r["uid"], r["gid"])
            if r.get("work_roots"):
                write_work_roots(cfg, rdir, h, r)
            applied.append({"reader": r["name"], "previous": previous, "pruned": prune(rdir)})
        return {"result": "applied", "manifest_hash": h, "files": len(manifest["files"]), "readers": applied}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def write_work_roots(cfg: dict, rdir: Path, h: str, reader: dict) -> None:
    """COS_WORK_SOURCE_ROOTS_FILE content: each configured root at its REAL release path, as the container sees it."""
    mount = cfg.get("work_roots_mount", str(rdir))     # where rdir appears inside cos-scheduler
    doc = {}
    for root in cfg.get("work_roots", []):            # [{"subject", "ref", "path", "context_class"}], path relative to the tree
        doc.setdefault(root["subject"], {})[root["ref"]] = {
            "path": f"{mount}/releases/{h}/tree/{root['path']}", "context_class": root["context_class"]}
    target = Path(cfg.get("work_roots_file", str(rdir / "work_source_roots.json")))
    write_whole(target, json.dumps(doc, indent=2, sort_keys=True).encode(), 0o600)
    own(target, reader["uid"], reader["gid"])


def rollback(cfg: dict, to: str | None = None) -> dict:
    out = []
    for r in cfg["readers"]:
        rdir = Path(r["dir"])
        cur, names = current_release(rdir), retained(rdir)
        target = to or next((n for n in reversed(names) if n != cur), None)
        if not target or target not in names:
            raise Refused("no_previous_release", r["name"])
        switch_current(rdir, target)
        if r.get("work_roots"):
            write_work_roots(cfg, rdir, target, r)
        out.append({"reader": r["name"], "from": cur, "to": target})
    return {"result": "rolled_back", "readers": out}


def status(cfg: dict) -> dict:
    out = []
    for r in cfg.get("readers", []):
        rdir = Path(r["dir"])
        receipt = json.loads((rdir / "RECEIPT.json").read_text()) if (rdir / "RECEIPT.json").exists() else None
        out.append({"reader": r["name"], "current": current_release(rdir), "retained": retained(rdir),
                    "quarantined": len(list((rdir / "quarantine").glob("*"))) if (rdir / "quarantine").exists() else 0,
                    "receipt": receipt})
    return {"readers": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["publish", "apply", "rollback", "status"])
    ap.add_argument("--config", required=True)
    ap.add_argument("--dry-run", action="store_true", help="publish: collect and hash only; nothing encrypted or uploaded")
    ap.add_argument("--to", help="rollback: a retained release hash")
    a = ap.parse_args(argv)
    cfg = json.loads(Path(a.config).expanduser().read_text())
    try:
        if a.command == "publish":
            res = publish(cfg, dry_run=a.dry_run)
        elif a.command == "apply":
            res = apply(cfg)
        elif a.command == "rollback":
            res = rollback(cfg, a.to)
        else:
            res = status(cfg)
    except Refused as e:
        print(json.dumps({"refused": e.code, "detail": e.detail}), file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The Vault's independent backup (V5): the shelf, encrypted with `age` to a PUBLIC recipient, copied to a destination, and a restore drill that proves it opens again.

Design, in plain words.
  * The backup job holds only the public recipient. It can write a backup and cannot read one back. The private identity is used only by the restore drill, and is
    fetched through a configurable adapter (a file, the macOS Keychain, AWS Parameter Store, or a command); it is written to a 0600 temp file for the one `age` call
    and removed, and it is never logged, put in a manifest, or passed on a command line other than as the file name.
  * `age` is the official pinned binary (its path and expected version are configuration). If it is missing or its version is wrong the job fails BEFORE anything is
    uploaded.
  * Every attempt writes to its OWN immutable folder, ``<dest>/daily/<YYYY-MM-DD>/<attempt>/``: ``vault.tar.gz.age``, ``manifest.json``, and ``COMPLETE.json`` last
    (an attempt id is the UTC time plus random bytes, so two writers, even at once, never share a key and nothing earlier is ever overwritten). Only after COMPLETE.json
    is safely written is the small pointer ``<dest>/daily/<date>/latest.json`` replaced to name that attempt; the pointer is the only object that is ever overwritten, and
    overwriting it destroys nothing. An attempt without COMPLETE.json is an orphan and is never restored from. On the 1st of a month the same three objects are also written
    under ``monthly/<YYYY-MM>/<attempt>/`` with their own pointer. The manifest holds counts, sizes, SHA-256 hashes, the age version and a short fingerprint of the
    recipient. No record names, titles or content.
  * The archive is the whole shelf folder read-only (symlinks are never followed; temp and lock files are skipped). The CAPTURED archive is then unpacked into scratch and
    verified with the portable reader's own check, and the manifest's counts come from that unpacked copy, not from the live shelf (which may change while it is copied). A
    damaged snapshot is still backed up, and the run says ``warn``.
  * Disk: every step that writes scratch (the archive, the encrypted copy, the unpacked check; and on restore the download, decryption and extraction) first checks that the
    needed bytes fit AND that the standing 5 GiB reserve (``MOI_RESERVE_GIB``) would remain; otherwise it refuses with ``insufficient_space`` before writing.
  * Cleanup is checked: scratch and the temporary key file are removed and verified gone, the key file is overwritten first, and any failure is reported
    (``cleanup_failed``) rather than ignored.
  * `dest` is ``file:///absolute/dir`` (dev and tests) or ``s3://bucket/prefix`` (production: the existing ``vault/`` prefix). S3 uploads ask for server-side
    encryption in addition to the client-side age encryption.
  * Nothing here claims protection against losing the account that holds both the backup and the private key: that needs the second recovery copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core.vault_t1 import portable
from core.vault_t1.reader import Vault

FORMAT = "vault-backup/1"
ARCHIVE = "vault.tar.gz.age"
MANIFEST = "manifest.json"
COMPLETE = "COMPLETE.json"
LATEST = "latest.json"
RECIPIENT = re.compile(r"^age1[0-9a-z]{58}$")
IDENTITY_LINE = re.compile(r"^AGE-SECRET-KEY-1[0-9A-Z]{58}$", re.M)
ATTEMPT = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
DAY = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
WINDOW = (0, 6)                                   # the standing batch-job window, America/Chicago, hours [0, 6)
CHICAGO = ZoneInfo("America/Chicago")
SKIP = re.compile(r"(^|/)(\.[^/]*\.tmp-[^/]*|[^/]*\.lock|\.DS_Store)$")
GIB = 1 << 30
BLOCK = 4096                                       # filesystem allocation unit assumed for every file (a file of 1 byte still takes a block)
TAR_PER_FILE = 2048                                # a tar entry's 512-byte header, its pax extension (long names) and up to 511 bytes of padding, rounded up
MAX_CONDITIONAL_PUT = 5 * GIB                      # a single PutObject is limited to 5 GiB; a larger attempt needs a different (multipart) path and is refused, not downgraded


def reserve_bytes() -> int:
    """The standing free-space reserve, 5 GiB unless the owner changes MOI_RESERVE_GIB. A bad value falls back to 5, never to zero."""
    try:
        gib = float(os.environ.get("MOI_RESERVE_GIB", "5"))
    except ValueError:
        gib = 5.0
    return int((gib if gib >= 0 else 5.0) * GIB)


class BackupError(RuntimeError):
    """A fixed, content-free reason (``code``); never a path of the shelf, a key, or a record name."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


def ensure_space(path, need: int, *, usage=shutil.disk_usage) -> None:
    """Refuse (``insufficient_space``) unless ``need`` more bytes fit on the filesystem holding ``path`` AND the reserve would still remain afterwards."""
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if usage(probe).free < int(need) + reserve_bytes():
        raise BackupError("insufficient_space")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def in_window(now: datetime) -> bool:
    return WINDOW[0] <= now.astimezone(CHICAGO).hour < WINDOW[1]


def fingerprint(recipient: str) -> str:
    return hashlib.sha256(recipient.encode()).hexdigest()[:16]


def new_attempt_id(now: datetime) -> str:
    return f"{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def age_version(age_bin: str) -> str:
    try:
        done = subprocess.run([age_bin, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError("age_unavailable") from None
    if done.returncode != 0:
        raise BackupError("age_unavailable")
    return done.stdout.strip().splitlines()[0] if done.stdout.strip() else "unknown"


def check_age(age_bin: str, expect_version: str | None) -> str:
    version = age_version(age_bin)
    if expect_version and expect_version not in version:
        raise BackupError("age_version_mismatch", f"expected {expect_version}")
    return version


# ── checked cleanup of sensitive scratch ──
WIPE_CHUNK = 1 << 20                                  # zero in fixed 1 MiB pieces: wiping a multi-GB plaintext archive never needs that much memory


def wipe_file(path) -> bool:
    """Overwrite a sensitive file with zeros (in fixed-size chunks), flush it, remove it, and confirm it is gone. False if any step failed or it is still there."""
    p = Path(path)
    try:
        if p.exists():
            remaining = p.stat().st_size
            zeros = b"\0" * min(WIPE_CHUNK, max(remaining, 1))
            with open(p, "r+b") as f:
                while remaining > 0:
                    n = min(len(zeros), remaining)
                    f.write(zeros if n == len(zeros) else zeros[:n])
                    remaining -= n
                f.flush()
                os.fsync(f.fileno())
            p.unlink()
    except OSError:
        pass
    return not p.exists()


def remove_tree(path) -> bool:
    """Remove a scratch folder and confirm it is gone. False if anything is left behind."""
    failed = []
    shutil.rmtree(path, onerror=lambda func, p, exc: failed.append(p))
    return not failed and not Path(path).exists()


# ── identity adapters (restore drill only) ──
def fetch_identity(spec: str, *, run=subprocess.run) -> str:
    """``file:/path`` | ``keychain:service/account`` | ``ssm:/parameter/name`` | ``cmd:program arg...`` -> the identity text. Never logged."""
    kind, _, rest = spec.partition(":")
    try:
        if kind == "file":
            text = Path(rest).read_text()
        elif kind == "keychain":
            service, _, account = rest.partition("/")
            text = run(["security", "find-generic-password", "-s", service, "-a", account, "-w"], capture_output=True, text=True, timeout=30, check=True).stdout
        elif kind == "ssm":
            text = run(["aws", "ssm", "get-parameter", "--name", rest, "--with-decryption", "--query", "Parameter.Value", "--output", "text"],
                       capture_output=True, text=True, timeout=60, check=True).stdout
        elif kind == "cmd":
            text = run(rest.split(), capture_output=True, text=True, timeout=60, check=True).stdout
        else:
            raise BackupError("identity_source_unknown")
    except BackupError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise BackupError("identity_unavailable") from None
    if not IDENTITY_LINE.search(text):
        raise BackupError("identity_malformed")
    return text


# ── destination adapters ──
class Dest:
    def __init__(self, url: str, *, aws_bin: str = "aws", run=subprocess.run):
        self.url, self.aws, self.run = url.rstrip("/"), aws_bin, run
        if url.startswith("file://"):
            self.kind, self.base = "file", Path(url[len("file://"):])
        elif url.startswith("s3://"):
            self.kind, self.base = "s3", None
        else:
            raise BackupError("destination_unknown")

    def put(self, local: Path, key: str, *, replace: bool = False) -> None:
        """Write one object. ``replace=False`` (every attempt object) is a real conditional create: the local file is published with an exclusive hard link (it either becomes
        the object or fails, atomically, whoever else is writing) and S3 gets ``PutObject`` with ``If-None-Match: *`` (a 412 means the key already exists). Neither is a
        check followed by a write. Only the small ``latest.json`` pointer is replaced."""
        if self.kind == "file":
            target = self.base / key
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
            try:
                shutil.copyfile(local, tmp)
                with open(tmp, "rb") as f:
                    os.fsync(f.fileno())
                if replace:
                    os.replace(tmp, target)
                else:
                    try:
                        os.link(tmp, target)                 # atomic and exclusive: FileExistsError if anything is already there
                    except FileExistsError:
                        raise BackupError("object_exists") from None
            except OSError:
                raise BackupError("upload_failed") from None
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
            return
        if replace:
            try:
                self.run([self.aws, "s3", "cp", str(local), f"{self.url}/{key}", "--sse", "AES256", "--only-show-errors"], check=True, capture_output=True, timeout=3600)
            except (OSError, subprocess.SubprocessError):
                raise BackupError("upload_failed") from None
            return
        if local.stat().st_size > MAX_CONDITIONAL_PUT:
            raise BackupError("object_too_large_for_conditional_put")
        bucket, _, prefix = self.url[len("s3://"):].partition("/")
        full_key = f"{prefix}/{key}" if prefix else key
        try:
            done = self.run([self.aws, "s3api", "put-object", "--bucket", bucket, "--key", full_key, "--body", str(local), "--if-none-match", "*",
                             "--server-side-encryption", "AES256"], capture_output=True, text=True, timeout=3600)
        except (OSError, subprocess.SubprocessError):
            raise BackupError("upload_failed") from None
        if done.returncode != 0:
            text = (done.stderr or "") + (done.stdout or "")
            raise BackupError("object_exists" if ("PreconditionFailed" in text or "412" in text) else "upload_failed")

    def get(self, key: str, local: Path) -> None:
        if self.kind == "file":
            try:
                shutil.copyfile(self.base / key, local)
            except OSError:
                raise BackupError("download_failed") from None
            return
        try:
            self.run([self.aws, "s3", "cp", f"{self.url}/{key}", str(local), "--only-show-errors"], check=True, capture_output=True, timeout=3600)
        except (OSError, subprocess.SubprocessError):
            raise BackupError("download_failed") from None


# ── the archive ──
def footprint(shelf_root: str) -> dict:
    """What a folder really costs: logical bytes, file count, and a conservative on-disk size (every file rounded UP to whole blocks, or its actual allocation if larger)."""
    logical = files = alloc = directories = 0
    for dirpath, _dirs, filenames in os.walk(shelf_root, followlinks=False):
        directories += 1
        alloc += BLOCK
        for name in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                continue
            files += 1
            logical += st.st_size
            alloc += max(-(-st.st_size // BLOCK) * BLOCK, BLOCK, getattr(st, "st_blocks", 0) * 512)
    return {"bytes": logical, "files": files, "alloc": alloc, "directories": directories}


def shelf_bytes(shelf_root: str) -> int:
    return footprint(shelf_root)["bytes"]


def tar_estimate(bytes_: int, files: int) -> int:
    """The uncompressed tar's size: the data, a header-and-padding allowance per file, the tar's end blocks, plus 2% for a gzip that cannot shrink incompressible data."""
    return int((bytes_ + files * TAR_PER_FILE + 10240) * 1.02)


def ciphertext_bound(plaintext_bytes: int) -> int:
    """Single X25519 recipient: age tags each 64 KiB chunk; 64 KiB covers its header, nonce and final tag. Round to filesystem blocks."""
    overhead = -(-plaintext_bytes // 65536) * 16 + 65536
    return -(-(plaintext_bytes + overhead) // BLOCK) * BLOCK


def backup_need(fp: dict, *, s3: bool = False) -> int:
    """S3 phases never retain check and ciphertext together. Local copies retain the original conservative three-copy estimate."""
    archive = tar_estimate(fp["bytes"], fp["files"])
    if s3:
        return max(archive + fp["alloc"], archive + ciphertext_bound(archive)) + (16 << 20)
    return 2 * archive + fp["alloc"] + (16 << 20)


def captured_extract_need(archive: Path) -> int:
    """Use captured member sizes and parent directories, so growth after discovery cannot understate extraction space."""
    allocated, dirs = 0, {()}
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise BackupError("archive_unsafe")
            if member.isfile():
                allocated += max(BLOCK, -(-member.size // BLOCK) * BLOCK)
            for n in range(1, len(path.parts) + (1 if member.isdir() else 0)):
                dirs.add(path.parts[:n])
    return allocated + len(dirs) * BLOCK + (16 << 20)


def restore_need(manifest: dict) -> int:
    """Scratch for a restore: the download, the decrypted archive and the extracted shelf (whole blocks per file), plus slack, from numbers the manifest records."""
    shelf = manifest["shelf"]
    return (int(manifest["ciphertext"]["bytes"]) + int(manifest["archive"]["plaintext_bytes"]) + int(shelf["bytes"]) + int(shelf["files"]) * BLOCK + (16 << 20))


def snapshot_tar(shelf_root: str, out: Path) -> dict:
    """The shelf folder as one gzip tar, read-only on the shelf, deterministic order, no symlinks followed. Returns counts of what the archive holds."""
    root = Path(shelf_root).resolve(strict=True)
    files = skipped = nbytes = 0
    with tarfile.open(out, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=6) as tar:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = path.relative_to(root).as_posix()
                if path.is_symlink() or SKIP.search(rel) or not path.is_file():
                    skipped += 1
                    continue
                info = tar.gettarinfo(str(path), arcname=rel)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with open(path, "rb") as f:
                    tar.addfile(info, f)
                files += 1
                nbytes += info.size
    return {"files": files, "skipped": skipped, "bytes": nbytes}


def encrypt(age_bin: str, recipient: str, src: Path, dst: Path) -> None:
    if not RECIPIENT.match(recipient):
        raise BackupError("recipient_malformed")
    try:
        subprocess.run([age_bin, "-r", recipient, "-o", str(dst), str(src)], check=True, capture_output=True, timeout=3600)
    except (OSError, subprocess.SubprocessError):
        raise BackupError("encrypt_failed") from None


def decrypt(age_bin: str, identity_text: str, src: Path, dst: Path, workdir: Path) -> None:
    """Decrypt with the private identity, which exists only as a 0600 file for this one call and is overwritten and removed after; a failure to remove it is reported."""
    fd, name = tempfile.mkstemp(prefix=".id-", dir=workdir)
    problem = None
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, identity_text.encode())
        os.close(fd)
        try:
            subprocess.run([age_bin, "-d", "-i", name, "-o", str(dst), str(src)], check=True, capture_output=True, timeout=3600)
        except (OSError, subprocess.SubprocessError):
            problem = BackupError("decrypt_failed")
    finally:
        if not wipe_file(name):
            raise BackupError("key_cleanup_failed")
    if problem:
        raise problem from None


def _safe_extract(archive: Path, target: Path) -> int:
    n = 0
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar:
            p = Path(m.name)
            if p.is_absolute() or ".." in p.parts or not (m.isfile() or m.isdir()):
                raise BackupError("archive_unsafe")
            n += 1
        tar.extractall(target, filter="data")
    return n


# ── the backup ──
def run_backup(*, shelf_root: str, dest: str, recipient: str, workdir: str, age_bin: str = "age", expect_age: str | None = None,
               aws_bin: str = "aws", now: datetime | None = None, enforce_window: bool = True, host: str = "mac", run=subprocess.run,
               usage=shutil.disk_usage, attempt: str | None = None) -> dict:
    """Do everything: check, archive, verify the captured copy, encrypt, upload the attempt, COMPLETE last, then the pointer. Returns the manifest. Raises BackupError."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if enforce_window and not in_window(now):
        raise BackupError("outside_window")
    version = check_age(age_bin, expect_age)                    # before anything is read or uploaded
    if not RECIPIENT.match(recipient):
        raise BackupError("recipient_malformed")
    d = Dest(dest, aws_bin=aws_bin, run=run)
    attempt = attempt or new_attempt_id(now)
    if not ATTEMPT.match(attempt):
        raise BackupError("attempt_malformed")
    work = Path(workdir)
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    fp = footprint(shelf_root)
    ensure_space(work, backup_need(fp, s3=d.kind == "s3"), usage=usage)            # the archive, its encrypted copy and the unpacked check copy, per-file overhead included, with the reserve left over
    if d.kind == "file":
        ensure_space(d.base, tar_estimate(fp["bytes"], fp["files"]) + (16 << 20), usage=usage)
    scratch = Path(tempfile.mkdtemp(prefix="vault-backup-", dir=work))
    clean = True
    try:
        os.chmod(scratch, 0o700)
        plain, cipher = scratch / "vault.tar.gz", scratch / ARCHIVE
        counts = snapshot_tar(shelf_root, plain)
        plain_sha, plain_bytes = sha256_file(plain), plain.stat().st_size
        # Verify what was CAPTURED, not the live shelf: unpack the archive and run the portable reader's own check on the unpacked copy; counts come from it.
        check = scratch / "captured"
        ensure_space(work, captured_extract_need(plain), usage=usage)
        check.mkdir(mode=0o700)
        _safe_extract(plain, check)
        verify = portable.verify_shelf(str(check))
        records = Vault(str(check), ()).coverage()["totals"]["records"]
        if not remove_tree(check):
            raise BackupError("cleanup_failed")
        ensure_space(work, ciphertext_bound(plain_bytes) + (16 << 20), usage=usage)
        encrypt(age_bin, recipient, plain, cipher)
        if not wipe_file(plain):                                # the plaintext archive never outlives this step
            raise BackupError("cleanup_failed")
        manifest = {
            "format": FORMAT, "attempt": attempt, "created_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "host": host,
            "shelf": {"records": records, "files": counts["files"], "skipped": counts["skipped"], "bytes": counts["bytes"],
                      "verify_ok": bool(verify.get("ok")), "verify_problems": len(verify.get("problems") or []), "verified": "captured_copy"},
            "archive": {"plaintext_sha256": plain_sha, "plaintext_bytes": plain_bytes},
            "ciphertext": {"name": ARCHIVE, "sha256": sha256_file(cipher), "bytes": cipher.stat().st_size},
            "age": {"version": version, "recipient_fingerprint": fingerprint(recipient)},
        }
        mpath = scratch / MANIFEST
        mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        complete = {"format": FORMAT, "attempt": attempt, "created_utc": manifest["created_utc"], "manifest_sha256": sha256_file(mpath),
                    "ciphertext_sha256": manifest["ciphertext"]["sha256"], "ciphertext_bytes": manifest["ciphertext"]["bytes"]}
        cpath = scratch / COMPLETE
        cpath.write_text(json.dumps(complete, indent=2, sort_keys=True) + "\n")
        pointer = {"format": FORMAT, "attempt": attempt, "complete_sha256": sha256_file(cpath)}
        ppath = scratch / LATEST
        ppath.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n")
        day = now.astimezone(CHICAGO)
        prefixes = [f"daily/{day:%Y-%m-%d}"] + ([f"monthly/{day:%Y-%m}"] if day.day == 1 else [])
        for prefix in prefixes:
            folder = f"{prefix}/{attempt}"
            d.put(cipher, f"{folder}/{ARCHIVE}")
            d.put(mpath, f"{folder}/{MANIFEST}")
            d.put(cpath, f"{folder}/{COMPLETE}")                # COMPLETE last: an attempt without it is an orphan and is never restored from
            d.put(ppath, f"{prefix}/{LATEST}", replace=True)    # only now does the day point at this attempt
        manifest["state"] = "ok" if verify.get("ok") else "warn"
        manifest["keys"] = [f"{p}/{attempt}" for p in prefixes]
        return manifest
    finally:
        if not remove_tree(scratch):
            clean = False
        if not clean:
            raise BackupError("cleanup_failed")


# ── the restore drill ──
def restore_drill(*, dest: str, day: str, identity_spec: str, workdir: str, age_bin: str = "age", aws_bin: str = "aws", monthly: bool = False,
                  attempt: str | None = None, run=subprocess.run, fetch=fetch_identity, usage=shutil.disk_usage) -> dict:
    """Fetch one backup, check every hash, decrypt with the private identity, extract to a CLEAN folder, verify the shelf and open one retained edition.
    Needs only the backup objects, the identity and `age`: no MiniMoi service, no database, no provider login. Returns evidence with hashes and counts only.
    Without ``attempt`` it follows the day's ``latest.json`` pointer; with one it restores exactly that attempt (which must have its COMPLETE.json)."""
    if not DAY.match(day) or (attempt is not None and not ATTEMPT.match(attempt)):
        raise BackupError("bad_arguments")
    d = Dest(dest, aws_bin=aws_bin, run=run)
    work = Path(workdir)
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    ensure_space(work, 4 << 20, usage=usage)                    # room for the small pointer, COMPLETE and manifest files
    scratch = Path(tempfile.mkdtemp(prefix="vault-drill-", dir=work))
    clean = True
    try:
        os.chmod(scratch, 0o700)
        prefix = f"{'monthly' if monthly else 'daily'}/{day}"
        cipher, plain = scratch / ARCHIVE, scratch / "vault.tar.gz"
        if attempt is None:
            ppath = scratch / LATEST
            d.get(f"{prefix}/{LATEST}", ppath)
            try:
                pointer = json.loads(ppath.read_text())
                attempt = pointer["attempt"]
                want_complete = pointer["complete_sha256"]
            except (ValueError, KeyError, TypeError):
                raise BackupError("pointer_unreadable") from None
            if not isinstance(attempt, str) or not ATTEMPT.match(attempt):
                raise BackupError("pointer_unreadable")
        else:
            want_complete = None
        folder = f"{prefix}/{attempt}"
        cpath, mpath = scratch / COMPLETE, scratch / MANIFEST
        d.get(f"{folder}/{COMPLETE}", cpath)                    # no COMPLETE.json, no restore: that attempt never finished
        if want_complete is not None and sha256_file(cpath) != want_complete:
            raise BackupError("complete_mismatch")
        try:
            complete = json.loads(cpath.read_text())
        except ValueError:
            raise BackupError("complete_unreadable") from None
        d.get(f"{folder}/{MANIFEST}", mpath)
        if sha256_file(mpath) != complete.get("manifest_sha256"):
            raise BackupError("manifest_mismatch")
        try:
            manifest = json.loads(mpath.read_text())
        except ValueError:
            raise BackupError("manifest_unreadable") from None
        if manifest.get("format") != FORMAT or manifest.get("attempt") != attempt:
            raise BackupError("manifest_format")
        try:
            need = restore_need(manifest)
        except (KeyError, TypeError, ValueError):
            raise BackupError("manifest_format") from None
        ensure_space(work, need, usage=usage)                   # download + decrypted archive + extracted shelf (whole blocks per file), with the reserve left over
        d.get(f"{folder}/{ARCHIVE}", cipher)
        if sha256_file(cipher) != manifest["ciphertext"]["sha256"] or cipher.stat().st_size != manifest["ciphertext"]["bytes"] \
                or manifest["ciphertext"]["sha256"] != complete.get("ciphertext_sha256"):
            raise BackupError("ciphertext_mismatch")
        decrypt(age_bin, fetch(identity_spec), cipher, plain, scratch)
        if sha256_file(plain) != manifest["archive"]["plaintext_sha256"]:
            raise BackupError("plaintext_mismatch")
        target = scratch / "restored"
        target.mkdir(mode=0o700)
        extracted = _safe_extract(plain, target)
        verify = portable.verify_shelf(str(target))
        vault = Vault(str(target), ())
        records = vault.coverage()["totals"]["records"]
        opened = None
        listing = vault.list()["records"]
        if listing:
            first = listing[0]
            _, _, data = vault.open(first["id"], first["edition"])
            opened = {"edition_bytes": len(data), "edition_sha256": hashlib.sha256(data).hexdigest()}
        ok = bool(verify.get("ok")) and records == manifest["shelf"]["records"] and (opened is not None or records == 0)
        return {"ok": ok, "format": FORMAT, "backup": folder, "extracted_entries": extracted, "records": records, "records_expected": manifest["shelf"]["records"],
                "verify": {"ok": bool(verify.get("ok")), "problems": len(verify.get("problems") or [])}, "sample_edition": opened,
                "ciphertext_sha256": manifest["ciphertext"]["sha256"], "plaintext_sha256": manifest["archive"]["plaintext_sha256"],
                "needed": ["the backup objects", "the private identity", "age"], "not_needed": ["MiniMoi", "a database", "a provider login"]}
    finally:
        if not remove_tree(scratch):
            clean = False
        if not clean:
            raise BackupError("cleanup_failed")

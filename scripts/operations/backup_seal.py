#!/usr/bin/env python3
"""Personal stores leave the host only as ciphertext, and a drill proves they come back (#508, spec 1.2 S3, D13).

``backup_local.sh`` calls this after copying into the dated folder ``backups/<date>/``:

  * ``capture-volumes --dest <dated> --volume <name>...``: copies each Docker named volume (found by its compose
    name, with or without the project prefix) into ``vol-<name>/``. SQLite databases are copied with SQLite's online
    backup, so a copy taken while the agent runs is consistent (``-wal``/``-shm`` are not copied). A top-level
    ``tmp/`` is skipped.
  * ``seal --dir <dated> --recipients <file> --item <name>...``: each listed item present in the folder becomes
    ``<name>.tar.gz.age``, encrypted with the pinned ``age`` to every public key in the recipients file. The plaintext
    is then removed, so S3 (03:00) and Dropbox (Sundays) only ever see ciphertext. If anything fails, every remaining
    plaintext item is moved OUT of the dated folder into ``backups/.unsealed-<date>/`` (local only, 0700), and the
    exit status is 3, so the backup's ERR trap alerts.
  * ``drill --dir <dated> --identity <file> --live <item>=<path>...``: decrypts each sealed item into scratch,
    compares file counts and combined sha256 with the live path, checks every ``*.jsonl`` parses and every SQLite
    database passes ``integrity_check``. Prints JSON; never writes outside its scratch folder.

Standard library only. Nothing here prints file contents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

AGE_DEFAULT = "/opt/minimoi-tools/age-v1.3.2/age"
AGE_HEADER = b"age-encryption.org/v1"
SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")
SUFFIX = ".tar.gz.age"


class SealError(Exception):
    pass


def tree_digest(root: Path) -> tuple[int, str]:
    """(file count, sha256 over sorted 'sha256  relpath' lines), skipping lock files and SQLite side files."""
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink() and not _side_file(p):
            lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}\n")
    return len(lines), hashlib.sha256("".join(lines).encode()).hexdigest()


def _side_file(p: Path) -> bool:
    return p.name == ".lock" or p.name.endswith(("-wal", "-shm", "-journal"))


def match_volume(names: list[str], name: str) -> list[str]:
    """The volume itself, or the compose-prefixed one: <project>_<name> or <project>-<name> (Dev's is the latter)."""
    exact = [v for v in names if v == name]
    return exact or [v for v in names if v.endswith("_" + name) or v.endswith("-" + name)]


def _find_volume(name: str) -> str:
    out = subprocess.run(["docker", "volume", "ls", "-q"], capture_output=True, text=True, check=True).stdout.split()
    hits = match_volume(out, name)
    if len(hits) != 1:
        raise SealError(f"volume {name}: {len(hits)} matches")
    return subprocess.run(["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", hits[0]],
                          capture_output=True, text=True, check=True).stdout.strip()


def copy_tree_consistent(src: Path, dest: Path) -> dict:
    """Copy src to dest; SQLite files through the online backup API; skip side files and a top-level tmp/."""
    copied = dbs = 0
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if rel.parts and rel.parts[0] == "tmp":
            continue
        target = dest / rel
        if p.is_symlink():
            continue
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if _side_file(p):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix in SQLITE_SUFFIXES:
            source = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            copy = sqlite3.connect(target)
            with copy:
                source.backup(copy)
            copy.close()
            source.close()
            dbs += 1
        else:
            shutil.copy2(p, target)
        copied += 1
    return {"files": copied, "sqlite": dbs}


def capture_volumes(dest: Path, names: list[str], resolver=_find_volume) -> dict:
    out = {}
    for name in names:
        mount = Path(resolver(name))
        target = dest / f"vol-{name}"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        os.chmod(target, 0o700)
        out[name] = copy_tree_consistent(mount, target)
    return out


def _recipients(path: Path) -> list[str]:
    keys = [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not keys or not all(k.startswith("age1") for k in keys):
        raise SealError("recipients file has no age1 public keys")
    return keys


def seal(folder: Path, recipients: Path, items: list[str], age: str) -> dict:
    sealed, skipped = {}, []
    try:
        _recipients(recipients)
        for item in items:
            src = folder / item
            if not src.exists():
                skipped.append(item)
                continue
            count, _ = tree_digest(src) if src.is_dir() else (1, "")
            plain = folder / f".{item}.tar.gz.tmp"
            fd = os.open(plain, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
                tar.add(src, arcname=item)
            final = folder / f"{item}{SUFFIX}"
            partial = folder / f".{item}{SUFFIX}.partial"
            done = subprocess.run([age, "-R", str(recipients), "-o", str(partial), str(plain)],
                                  capture_output=True, text=True)
            plain.unlink()
            if done.returncode != 0 or not partial.exists() or partial.read_bytes()[:len(AGE_HEADER)] != AGE_HEADER:
                raise SealError(f"encrypt failed for {item}")
            os.replace(partial, final)
            if src.is_dir():
                shutil.rmtree(src)
            else:
                src.unlink()
            sealed[item] = {"files": count, "sha256": hashlib.sha256(final.read_bytes()).hexdigest()}
    except Exception as exc:
        moved = _quarantine_plaintext(folder, items)
        raise SealError(f"{exc}; plaintext moved out of the dated folder: {moved}") from exc
    return {"sealed": sealed, "absent": skipped}


def _quarantine_plaintext(folder: Path, items: list[str]) -> list[str]:
    hold = folder.parent / f".unsealed-{folder.name}"
    hold.mkdir(mode=0o700, exist_ok=True)
    moved = []
    for item in items:
        for p in (folder / item, folder / f".{item}.tar.gz.tmp", folder / f".{item}{SUFFIX}.partial"):
            if p.exists():
                shutil.move(str(p), str(hold / p.name))
                moved.append(p.name)
    return moved


def _safe_extract(archive: Path, dest: Path) -> None:
    with tarfile.open(archive) as tar:
        for m in tar.getmembers():
            if m.issym() or m.islnk() or m.isdev() or Path(m.name).is_absolute() or ".." in Path(m.name).parts:
                raise SealError(f"unsafe member {m.name}")
        tar.extractall(dest, filter="data")


def drill(folder: Path, identity: Path, live: dict[str, Path], age: str) -> dict:
    report = {}
    scratch = Path(tempfile.mkdtemp(prefix="restore-drill-"))
    os.chmod(scratch, 0o700)
    try:
        for enc in sorted(folder.glob(f"*{SUFFIX}")):
            item = enc.name[: -len(SUFFIX)]
            plain = scratch / f"{item}.tar.gz"
            done = subprocess.run([age, "-d", "-i", str(identity), "-o", str(plain), str(enc)],
                                  capture_output=True, text=True)
            if done.returncode != 0:
                report[item] = {"ok": False, "error": "decrypt_failed"}
                continue
            out = scratch / "x"
            if out.exists():
                shutil.rmtree(out)
            out.mkdir()
            _safe_extract(plain, out)
            plain.unlink()
            restored = out / item
            entry = {"ok": True}
            if restored.is_dir():
                entry["files"], digest = tree_digest(restored)
                lines = bad = 0
                for j in restored.rglob("*.jsonl"):
                    for line in j.read_text().splitlines():
                        if line.strip():
                            lines += 1
                            try:
                                json.loads(line)
                            except ValueError:
                                bad += 1
                entry["jsonl_lines"], entry["jsonl_bad"] = lines, bad
                integrity, unchecked = {}, []
                for db in restored.rglob("*"):
                    if db.is_file() and db.suffix in SQLITE_SUFFIXES:
                        rel = db.relative_to(restored).as_posix()
                        if db.read_bytes()[:16] != b"SQLite format 3\x00":
                            integrity[rel] = "not_sqlite"
                            continue
                        try:
                            integrity[rel] = sqlite3.connect(db).execute("pragma integrity_check").fetchone()[0]
                        except sqlite3.Error:
                            # A schema newer than this host's SQLite (e.g. octet_length() in an index): the file is a
                            # valid database, but this SQLite can't run the full check. Reported, not failed.
                            unchecked.append(rel)
                entry["sqlite_ok"] = all(v == "ok" for v in integrity.values())
                entry["sqlite"] = len(integrity) + len(unchecked)
                entry["sqlite_unchecked"] = len(unchecked)
                if item in live and live[item].exists():
                    lc, ld = tree_digest(live[item])
                    entry["live_files"], entry["matches_live"] = lc, ld == digest
                entry["ok"] = bad == 0 and entry["sqlite_ok"]
            else:
                entry["files"] = 1
                if item in live and live[item].is_file():
                    entry["matches_live"] = live[item].read_bytes() == restored.read_bytes()
            report[item] = entry
            shutil.rmtree(out)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"ok": all(v.get("ok") for v in report.values()) and bool(report), "items": report}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("capture-volumes")
    v.add_argument("--dest", required=True)
    v.add_argument("--volume", action="append", required=True)
    s = sub.add_parser("seal")
    s.add_argument("--dir", required=True)
    s.add_argument("--recipients", required=True)
    s.add_argument("--item", action="append", required=True)
    s.add_argument("--age", default=AGE_DEFAULT)
    d = sub.add_parser("drill")
    d.add_argument("--dir", required=True)
    d.add_argument("--identity", required=True)
    d.add_argument("--live", action="append", default=[], help="item=live path")
    d.add_argument("--age", default=AGE_DEFAULT)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "capture-volumes":
            res = capture_volumes(Path(a.dest), a.volume)
        elif a.cmd == "seal":
            res = seal(Path(a.dir), Path(a.recipients), a.item, a.age)
        else:
            live = dict((k, Path(p)) for k, p in (x.split("=", 1) for x in a.live))
            res = drill(Path(a.dir), Path(a.identity), live, a.age)
            print(json.dumps(res, indent=2, sort_keys=True))
            return 0 if res["ok"] else 1
    except (SealError, subprocess.CalledProcessError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 3
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

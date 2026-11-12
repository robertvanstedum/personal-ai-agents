#!/usr/bin/env python3
"""Host scripts match the deployed commit, or someone hears about it (#505, #506).

``config/host_scripts.json`` lists every script a production host runs from outside an image. On the deploy runner,
``bundle`` writes a tar.gz holding the files to ship (under their repo paths) and ``HOST_SCRIPTS.json``: for EVERY
listed entry, the sha256 it must have at this commit. On the host, as root:

  * ``install --from <extracted bundle>`` puts each shipped file in place by rename (an existing file keeps its owner
    and mode; a new one gets the listed mode, root-owned) and saves the manifest beside this tool.
  * ``check`` compares every listed host file with the manifest. A mismatch or a missing file exits 1, and with
    ``--notify`` sends one Telegram line (an alert means action is needed).
  * ``install-cron`` adds the daily check to root's crontab once (marker ``# minimoi-host-script-parity``).

Why: the deploy copied a fixed list of scripts, so the hand-installed backup script stayed old and the CoS store had no
backup in production (#505). Standard library only; nothing here reads file contents beyond hashing.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

FORMAT = "host-scripts/1"
MANIFEST_NAME = "HOST_SCRIPTS.json"
DEFAULT_MANIFEST = "/opt/minimoi/scripts/" + MANIFEST_NAME
CRON_MARKER = "# minimoi-host-script-parity"
CRON_LINE = ("20 5 * * * /usr/bin/python3 -I -B /opt/minimoi/scripts/host_script_parity.py check --notify "
             ">> /opt/minimoi/logs/host_script_parity.log 2>&1 " + CRON_MARKER)
TELEGRAM_CHAT_ID = "8379221702"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def load_config(path: Path) -> list[dict]:
    cfg = json.loads(path.read_text())
    if cfg.get("format") != FORMAT:
        raise SystemExit(f"unexpected format in {path}")
    hosts = [e["host"] for e in cfg["entries"]]
    if len(hosts) != len(set(hosts)):
        raise SystemExit("duplicate host path in config")
    return cfg["entries"]


def build_manifest(repo: Path, entries: list[dict], commit: str) -> dict:
    files = {}
    for e in entries:
        src = repo / e["repo"]
        if not src.is_file():
            raise SystemExit(f"listed repo file missing: {e['repo']}")
        files[e["host"]] = {"repo": e["repo"], "sha256": sha256_file(src), "ship": bool(e.get("ship")),
                            "mode": e.get("mode", "0755")}
    return {"format": FORMAT, "commit": commit, "files": files}


def bundle(repo: Path, config: Path, commit: str, out: Path) -> dict:
    entries = load_config(config)
    manifest = build_manifest(repo, entries, commit)
    raw = json.dumps(manifest, indent=2, sort_keys=True).encode()
    with tarfile.open(out, "w:gz") as tar:
        info = tarfile.TarInfo(MANIFEST_NAME)
        info.size, info.mode = len(raw), 0o644
        tar.addfile(info, io.BytesIO(raw))
        for e in entries:
            if e.get("ship"):
                tar.add(repo / e["repo"], arcname=e["repo"], recursive=False)
    return manifest


def install(src: Path, manifest_out: Path) -> dict:
    manifest = json.loads((src / MANIFEST_NAME).read_text())
    if manifest.get("format") != FORMAT:
        raise SystemExit("unexpected manifest format")
    installed = []
    for host, f in sorted(manifest["files"].items()):
        if not f["ship"]:
            continue
        body = src / f["repo"]
        if sha256_file(body) != f["sha256"]:
            raise SystemExit(f"bundle file does not match its manifest: {f['repo']}")
        dest = Path(host)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", dir=str(dest.parent))
        with os.fdopen(fd, "wb") as out, open(body, "rb") as inp:
            out.write(inp.read())
        if dest.exists():
            st = dest.stat()
            os.chmod(tmp, st.st_mode & 0o7777)
            if os.geteuid() == 0:
                os.chown(tmp, st.st_uid, st.st_gid)
        else:
            os.chmod(tmp, int(f["mode"], 8))
        os.replace(tmp, dest)
        installed.append(host)
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_out.with_name(manifest_out.name + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    os.chmod(tmp, 0o644)
    os.replace(tmp, manifest_out)
    return {"installed": installed, "commit": manifest["commit"]}


def check(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    mismatched, missing = [], []
    for host, f in sorted(manifest["files"].items()):
        p = Path(host)
        if not p.is_file():
            missing.append(host)
        elif sha256_file(p) != f["sha256"]:
            mismatched.append(host)
    return {"ok": not (mismatched or missing), "commit": manifest.get("commit"), "checked": len(manifest["files"]),
            "mismatched": mismatched, "missing": missing}


def notify(result: dict) -> None:
    token = subprocess.run(["aws", "ssm", "get-parameter", "--name", "/minimoi/production/telegram_bot_token",
                            "--with-decryption", "--query", "Parameter.Value", "--output", "text",
                            "--region", "us-east-1"], capture_output=True, text=True).stdout.strip()
    if not token:
        return
    names = [os.path.basename(h) for h in result["mismatched"] + result["missing"]]
    text = (f"⚠️ Host scripts differ from the deployed commit {str(result['commit'])[:8]}: "
            f"{len(result['mismatched'])} changed, {len(result['missing'])} missing ({', '.join(names[:8])}). "
            "See /opt/minimoi/logs/host_script_parity.log.")
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20).read()
    except OSError:
        pass


def install_cron() -> str:
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = current.stdout.splitlines() if current.returncode == 0 else []
    if any(CRON_MARKER in line for line in lines):
        return "present"
    subprocess.run(["crontab", "-"], input="\n".join(lines + [CRON_LINE]) + "\n", text=True, check=True)
    return "added"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bundle")
    b.add_argument("--repo", default=".")
    b.add_argument("--config", default="config/host_scripts.json")
    b.add_argument("--commit", required=True)
    b.add_argument("--out", required=True)
    i = sub.add_parser("install")
    i.add_argument("--from", dest="src", required=True)
    i.add_argument("--manifest", default=DEFAULT_MANIFEST)
    c = sub.add_parser("check")
    c.add_argument("--manifest", default=DEFAULT_MANIFEST)
    c.add_argument("--notify", action="store_true")
    sub.add_parser("install-cron")
    a = ap.parse_args(argv)
    if a.cmd == "bundle":
        m = bundle(Path(a.repo), Path(a.config), a.commit, Path(a.out))
        print(json.dumps({"files": len(m["files"]), "shipped": sum(f["ship"] for f in m["files"].values())}))
        return 0
    if a.cmd == "install":
        print(json.dumps(install(Path(a.src), Path(a.manifest))))
        return 0
    if a.cmd == "check":
        res = check(Path(a.manifest))
        print(json.dumps(res, sort_keys=True))
        if not res["ok"] and a.notify:
            notify(res)
        return 0 if res["ok"] else 1
    print(json.dumps({"cron": install_cron()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

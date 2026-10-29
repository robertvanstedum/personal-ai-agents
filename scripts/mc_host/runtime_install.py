"""Install a version 2 host runtime from a host configuration (host runtime spec v0.5 §2.2, R2.2-a, R3.4-a; S1-H).

Started by ``install_delegation_runner.sh --host-config TEMPLATE ...``; production's version 1 runner keeps that
script's own path, unchanged. Stdlib only and Python 3.9-compatible: it runs on the Mac's ``/usr/bin/python3`` and on
a Linux host's ``python3``, and asks the pinned interpreter itself every question about the runtime.

    install_delegation_runner.sh --host-config TEMPLATE --python PY [--ref REF] [--base DIR] [--home DIR]
                                 [--path-prefix DIR]... [--label LABEL] [--load]

What it does, in order:
1. **The bundle** (R2.2-a, G3): ``git archive REF`` of ``core/__init__.py``, ``core/mc_delegation_runner``,
   ``core/mc_delegation_dispatcher`` and ``core/minimoi_actions`` (the authority's grants and receipts), with today's
   ``run_runner.py`` entry, into ``<base>/bundle``, outside any checkout. ``REF`` defaults to ``origin/main``; it is
   archived as it stands (nothing is fetched here), and its commit is recorded.
2. **The digest** (``bundle_digest``): computed by the bundle's own ``host.tree_digest`` under the pinned interpreter,
   P0's method exactly, and recorded in ``<base>/install.json``. Every heartbeat reports the same value.
3. **The interpreter pin** (S1-D): ``--python`` is asked, under ``-I`` as the runtime runs, for its version, its
   ``sys.prefix`` (the virtual environment) and ``host.packages_digest()`` (what is installed in it). All four go into
   the installed ``host.yaml``, and the service file runs exactly that path with ``-I``. A host that reaches its store
   by DSN, or serves a door, needs ``psycopg2`` importable there: checked now, never found missing at start.
   The installer creates no environment (that needs the network); the operator makes it, outside any checkout.
4. **host.yaml**: the template with ``interpreter`` and ``bundle_root`` written in, checked by the bundle's own
   ``host.parse`` (the same rules the runtime starts under), with the authority's values file checked too.
5. **The service files**, by ``service_manager``:
   - ``launchd``: ``<label>.plist`` (default ``ai.minimoi.runtime.<env>``; gate M's is ``ai.minimoi.runtime.dev``),
     ``KeepAlive`` and ``ThrottleInterval 30`` (G1, G2), ``ExitTimeOut`` above the host's ``stop_timeout_s`` (G4),
     ``HOME`` and ``PATH`` set (``--home``, ``--path-prefix``), stdout and stderr kept in ``<base>/runtime.log``;
   - ``systemd``: ``minimoi-runtime-<env>.service`` and, for a host with a ``door``, ``minimoi-authority-<env>.service``
     (the templates beside this file), ``Restart=always``, ``KillMode=mixed`` (SIGTERM to the runtime alone, so it
     stops its own children at a checkpoint, G4), ``TimeoutStopSec`` above ``stop_timeout_s``; the runtime is ordered
     after the authority on the same host (upgrade order: the authority before its workers).
   All are written under ``<base>/service/``; none is installed anywhere a service manager reads.
6. **Loading** only with ``--load`` (Robert's word): a ``launchd`` dev host's plist is copied into
   ``~/Library/LaunchAgents`` and bootstrapped in ``gui/<uid>``. Without it nothing is loaded, and the plan is printed.
   ``--load`` is refused for ``prod`` (production's runtime ships in Robert's production release) and for ``systemd``
   (that needs ``sudo``; the operator commands are printed instead).

Every file is written with the user's own permissions (0700 folders, 0600 configuration). No value is read from the
Keychain and none is printed: references only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BUNDLE_PATHS = ("core/__init__.py", "core/mc_delegation_runner", "core/mc_delegation_dispatcher", "core/minimoi_actions")
RUN_RUNNER = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.mc_delegation_runner.cli import main
sys.exit(main(sys.argv[1:]))
'''                                           # the same entry production's bundle has (install_delegation_runner.sh)
RECORD_SCHEMA = "minimoi.runtime.install.v1"
EXIT_MARGIN_S = 10                            # the service manager waits this much longer than the runtime's own bound
_LABEL = re.compile(r"^ai\.minimoi\.[a-z0-9.-]{1,64}$")
_UNIT_VALUE = re.compile(r"^[A-Za-z0-9._/@:+=-]{1,1024}$")     # no space, quote, % or newline in a systemd value


class InstallRefused(SystemExit):
    def __init__(self, code):
        super().__init__(f"refused: {code}")
        self.code = code


def _need(ok, code):
    if not ok:
        raise InstallRefused(code)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _ask(python, code, *, bundle=None) -> dict:
    """Run ``code`` under the pinned interpreter as the runtime runs (``-I``), with the bundle first on its path; the
    code prints one JSON object."""
    prelude = "import sys, json\n" + (f"sys.path.insert(0, {str(bundle)!r})\n" if bundle else "")
    out = subprocess.run([str(python), "-I", "-c", prelude + code], capture_output=True, text=True, timeout=120,
                         env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/"), "LANG": "C.UTF-8"})
    if out.returncode != 0:
        raise InstallRefused("interpreter_check_failed:" + (out.stderr.strip().splitlines() or ["?"])[-1][:200])
    return json.loads(out.stdout)


def build_bundle(ref, dest: Path) -> str:
    """``git archive`` of the bundle's paths at ``ref`` into ``dest`` (replaced whole), plus run_runner.py. -> commit."""
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                            capture_output=True, text=True).stdout.strip()
    _need(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "ref_unknown")
    new = dest.with_name(dest.name + ".new")
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True, mode=0o700)
    archive = subprocess.run(["git", "-C", str(REPO), "archive", commit, *BUNDLE_PATHS], capture_output=True)
    _need(archive.returncode == 0, "bundle_archive_failed")
    subprocess.run(["tar", "-x", "-C", str(new)], input=archive.stdout, check=True)
    (new / "run_runner.py").write_text(RUN_RUNNER)
    shutil.rmtree(dest, ignore_errors=True)
    new.rename(dest)
    return commit


def interpreter_pin(python, bundle, needs_db) -> dict:
    """The four facts the runtime checks at every start (host.py's interpreter: path, version, prefix, packages)."""
    got = _ask(python, "from core.mc_delegation_runner import host\n"
                       "out = {'version': '%d.%d' % sys.version_info[:2], 'prefix': sys.prefix,"
                       " 'packages': host.packages_digest()}\n"
                       "try:\n    import psycopg2\n    out['psycopg2'] = True\nexcept ImportError:\n"
                       "    out['psycopg2'] = False\nprint(json.dumps(out))", bundle=bundle)
    _need(not needs_db or got["psycopg2"], "interpreter_lacks_psycopg2")
    return {"path": str(python), "version": got["version"], "prefix": got["prefix"], "packages": got["packages"]}


def check_host(python, bundle, doc) -> dict:
    """The installed host.yaml through the bundle's own rules, and the authority's values file it names."""
    return _ask(python, "from core.mc_delegation_runner import host\n"
                        "from core.mc_delegation_dispatcher.store import Authority\n"
                        f"cfg = host.parse(json.loads({json.dumps(json.dumps(doc))}))\n"
                        "auth = Authority.load(cfg.authority.values)\n"
                        "assert auth.environment == cfg.environment, 'authority_values_environment_mismatch'\n"
                        "assert auth.hosts is not None and auth.sweeper is not None and auth.speaks_v2, "
                        "'host_values_missing'\n"
                        "print(json.dumps({'runner_id': cfg.runner_id, 'roles': list(cfg.roles),"
                        " 'transport': cfg.authority.transport, 'door': cfg.door is not None}))", bundle=bundle)


def bundle_digest(python, bundle) -> tuple:
    got = _ask(python, "from core.mc_delegation_runner import host\n"
                       f"d, n = host.tree_digest({str(bundle)!r})\nprint(json.dumps([d, n]))", bundle=bundle)
    return got[0], got[1]


def env_path(prefixes) -> str:
    """The runtime's PATH: the given folders first (gate M: the provider tripwire), then the system's."""
    return ":".join([str(Path(p).expanduser()) for p in prefixes] + ["/opt/homebrew/bin", "/usr/bin", "/bin"])


def plist(label, python, bundle, host_config, base, home, path, stop_timeout_s) -> bytes:
    doc = {"Label": label,
           "ProgramArguments": [str(python), "-I", str(bundle / "run_runner.py"), "run", "--host-config",
                                str(host_config)],
           "EnvironmentVariables": {"HOME": str(home), "PATH": path},
           "KeepAlive": True, "ThrottleInterval": 30, "ExitTimeOut": stop_timeout_s + EXIT_MARGIN_S,
           "StandardOutPath": str(base / "runtime.log"), "StandardErrorPath": str(base / "runtime.log")}
    return plistlib.dumps(doc, sort_keys=True)


def unit(template, values) -> str:
    text = (HERE / template).read_text()
    for k, v in values.items():
        _need(_UNIT_VALUE.match(str(v)) is not None, f"unit_value_invalid:{k}")
        text = text.replace("@" + k + "@", str(v))
    _need(re.search(r"@[A-Z_]+@", text) is None, "unit_template_incomplete")
    return text


def _write(path: Path, data: bytes, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def install(args) -> dict:
    template = json.loads(Path(args.host_config).expanduser().read_text())
    _need(type(template) is dict and template.get("schema") == "minimoi.host.v1", "host_template_invalid")
    env = template.get("environment")
    _need(env in ("dev", "prod"), "host_environment_invalid")
    base = Path(args.base or f"~/minimoi-runtime/{env}").expanduser().resolve()
    home = Path(args.home or "~").expanduser().resolve()
    python = Path(args.python).expanduser()
    _need(python.is_absolute() and os.access(python, os.X_OK), "interpreter_invalid")
    manager = template.get("service_manager", "launchd")
    label = args.label or f"ai.minimoi.runtime.{env}"
    _need(_LABEL.match(label) is not None and label != "ai.minimoi.delegation-runner.prod", "label_invalid")
    if args.load:
        _need(env != "prod", "load_prod_not_in_this_build")       # production's runtime ships in Robert's release
        _need(manager == "launchd", "load_needs_the_operator")     # systemd needs sudo: commands are printed
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundle = base / "bundle"
    commit = build_bundle(args.ref, bundle)
    roles = template.get("roles") or []
    authority = template.get("authority") or {}
    pin = interpreter_pin(python, bundle, "dsn_file" in authority or template.get("door") is not None)
    doc = {**template, "interpreter": pin, "bundle_root": str(bundle)}
    checked = check_host(python, bundle, doc)
    host_config = base / "host.yaml"
    text = (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode()
    _write(host_config, text)
    digest, files = bundle_digest(python, bundle)
    stop = int(doc.get("stop_timeout_s", 20))
    path = env_path(args.path_prefix or [])
    services = {}
    if manager == "launchd":
        services[f"{label}.plist"] = plist(label, python, bundle, host_config, base, home, path, stop)
    elif manager == "systemd":
        values = {"ENVIRONMENT": env, "HOST_ID": doc["host_id"], "BASE": base, "HOME": home, "PATH": path,
                  "PYTHON": python, "BUNDLE": bundle, "HOST_CONFIG": host_config, "USER": args.user or _user(),
                  "STOP_TIMEOUT": stop + EXIT_MARGIN_S}
        after = f"minimoi-authority-{env}.service" if checked["door"] else "network-online.target"
        services[f"minimoi-runtime-{env}.service"] = unit("minimoi-runtime.service.in", {**values, "AFTER": after}).encode()
        if checked["door"]:
            services[f"minimoi-authority-{env}.service"] = unit("minimoi-authority.service.in", values).encode()
    else:
        raise InstallRefused("service_manager_not_installable")
    for name, data in services.items():
        _write(base / "service" / name, data, 0o644)
    record = {"schema": RECORD_SCHEMA, "environment": env, "host_id": doc["host_id"], "runner_id": checked["runner_id"],
              "roles": checked["roles"], "authority_transport": checked["transport"], "service_manager": manager,
              "label": label if manager == "launchd" else None, "ref": args.ref, "commit": commit,
              "bundle_root": str(bundle), "bundle_digest": digest, "bundle_files": files, "interpreter": pin,
              "host_config": {"path": str(host_config), "sha256": _sha(text)},
              "services": {n: {"path": str(base / "service" / n), "sha256": _sha(d)} for n, d in services.items()},
              "home": str(home), "path": path, "rendered_at": datetime.now(timezone.utc).isoformat(), "loaded": False}
    if args.load:
        target = home_of_user() / "Library" / "LaunchAgents" / f"{label}.plist"
        _write(target, services[f"{label}.plist"], 0o644)
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(target)], check=True)
        record.update(loaded=True, loaded_plist=str(target))
    _write(base / "install.json", (json.dumps(record, indent=1, sort_keys=True) + "\n").encode())
    return record


def _user() -> str:
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def home_of_user() -> Path:
    """The login user's own home (where launchd reads LaunchAgents), never the runtime's --home."""
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def plan(record) -> str:
    lines = [f"host runtime {record['host_id']} ({record['environment']}), {record['service_manager']}",
             f"  bundle   {record['bundle_root']}  {record['bundle_digest']} ({record['bundle_files']} files) "
             f"from {record['ref']} = {record['commit']}",
             f"  python   {record['interpreter']['path']} {record['interpreter']['version']} "
             f"prefix {record['interpreter']['prefix']} packages {record['interpreter']['packages']}",
             f"  config   {record['host_config']['path']}  {record['host_config']['sha256']}"]
    for name, s in sorted(record["services"].items()):
        lines.append(f"  service  {s['path']}  {s['sha256']}")
    if record["loaded"]:
        lines.append(f"  loaded   gui/{os.getuid()}/{record['label']} from {record['loaded_plist']}")
    elif record["service_manager"] == "launchd":
        lines.append("  NOT loaded. To load it (Robert's word): rerun with --load.")
    else:
        lines.append("  NOT installed. The operator, with sudo: install -m 0644 <base>/service/*.service "
                     "/etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now <the authority unit "
                     "first, then the runtime unit>")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="install_delegation_runner.sh --host-config")
    p.add_argument("--host-config", required=True, help="the host.yaml template (JSON, minimoi.host.v1)")
    p.add_argument("--python", required=True, help="the interpreter to pin (absolute path)")
    p.add_argument("--ref", default="origin/main")
    p.add_argument("--base", help="install folder (default ~/minimoi-runtime/<environment>)")
    p.add_argument("--home", help="the runtime's HOME (default: this user's)")
    p.add_argument("--path-prefix", action="append", help="a folder first on the runtime's PATH (repeatable)")
    p.add_argument("--label", help="launchd label (default ai.minimoi.runtime.<environment>)")
    p.add_argument("--user", help="systemd: the user the units run as (default: this user)")
    p.add_argument("--load", action="store_true", help="load the launchd job (dev only; Robert's word)")
    record = install(p.parse_args(argv))
    print(plan(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""``fakecli`` (host runtime spec v0.5 §9.3): a ``fakework/1`` build packaged the way a vendor CLI is installed, used
to exercise the launch contract (T19). Test instrument only; nothing here is installed outside a test's folder.

    <install>/bin/fakecli          the pinned executable (its SHA-256 goes in the test host.yaml's ``tools.fakework``)
    <install>/lib/fakework.py      its own copy of fakework/1 (the bundle's, byte for byte at install time), with the
                                   census module beside it (its spawn step writes census events, §6.6)
    <install>/etc/planted.sha256   the SHA-256 of each planted test value (digests only, never a value)
    <install>/var/fakecli-env.jsonl  one line per start: the NAMES in the environment it was started with (read from
                                   the kernel's record of its exec), and for any value matching a planted digest, that
                                   name and the digest; never a value

Its sign-in file and its user configuration live under the HOME it is given (``~/.fakecli/auth.json``,
``~/.fakecli/settings.json``) and its project configuration in the checkout (``.fakecli/settings.json``), as the
launch policy's ``fakecli`` entry says; the runtime checks them before it starts the binary. Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from core.mc_delegation_runner import commands

LAUNCHER = r'''#!{python} -I
"""fakecli: a fakework/1 build packaged like a vendor CLI (spec v0.5 section 9.3). It records the NAMES in the
environment it was started with, and the SHA-256 of any value matching a planted test value, never a value; then it
runs fakework/1. The environment is read as the kernel recorded it at exec (macOS: sysctl KERN_PROCARGS2; Linux:
/proc/self/environ), because this interpreter adds names of its own at start (LC_CTYPE, __CF_USER_TEXT_ENCODING)."""
import hashlib, json, os, re, struct, sys

def exec_environ():
    if os.path.exists("/proc/self/environ"):
        with open("/proc/self/environ", "rb") as fh:
            return [e for e in fh.read().split(b"\0") if e], "proc"
    import ctypes, ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    argmax, size = ctypes.c_int(), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
    libc.sysctl((ctypes.c_int * 2)(1, 8), 2, ctypes.byref(argmax), ctypes.byref(size), None, 0)
    buf, size = ctypes.create_string_buffer(argmax.value), ctypes.c_size_t(argmax.value)
    if libc.sysctl((ctypes.c_int * 3)(1, 49, os.getpid()), 3, buf, ctypes.byref(size), None, 0) != 0:
        return None, "unreadable"
    raw = buf.raw[:size.value]
    argc = struct.unpack("i", raw[:4])[0]
    parts = raw[4:].split(b"\0")[1:]                     # after the executable path
    while parts and parts[0] == b"":
        parts.pop(0)                                       # its padding
    env = []
    for p in parts[argc:]:
        if p == b"":
            break                                          # the environment ends; the kernel's own strings follow
        env.append(p)
    return env, "kern.procargs2"

entries, source = exec_environ()
HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
try:
    with open(os.path.join(HERE, "etc", "planted.sha256")) as fh:
        planted = set(fh.read().split())
except FileNotFoundError:
    planted = set()
names, hits = [], []
for e in entries or []:
    name, sep, value = e.partition(b"=")
    if not sep or not re.match(rb"^[A-Za-z_][A-Za-z0-9_]*$", name):
        continue
    names.append(name.decode())
    digest = hashlib.sha256(value).hexdigest()
    if digest in planted:
        hits.append({{"name": name.decode(), "sha256": digest}})
line = {{"pid": os.getpid(), "source": source, "names": sorted(set(names)),
         "planted": sorted(hits, key=lambda h: h["name"])}}
os.makedirs(os.path.join(HERE, "var"), exist_ok=True)
with open(os.path.join(HERE, "var", "fakecli-env.jsonl"), "a") as fh:
    fh.write(json.dumps(line, sort_keys=True) + "\n")
    fh.flush()
    os.fsync(fh.fileno())
os.execv(sys.executable, [sys.executable, "-I", "-B", os.path.join(HERE, "lib", "fakework.py")] + sys.argv[1:])
'''


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def real_interpreter() -> str:
    """The interpreter binary itself, for fakecli's first line. On a macOS framework build (and a venv made from one),
    ``sys.executable`` is a launcher that adds ``__PYVENV_LAUNCHER__`` and ``__CF_USER_TEXT_ENCODING`` to the
    environment before it starts the real binary (shown by test, 29 Oct), which would read as names the runtime gave."""
    import os
    app = os.path.join(sys.base_prefix, "Resources", "Python.app", "Contents", "MacOS", "Python")
    return app if os.path.isfile(app) and os.access(app, os.X_OK) else os.path.realpath(sys.executable)


def install(root, *, planted=(), python=None) -> tuple:
    """Install fakecli under ``root``; ``planted`` are test VALUES, of which only digests are written. Returns
    (path to bin/fakecli, its sha256 hex)."""
    root = Path(root)
    for d in ("bin", "lib", "etc", "var"):
        (root / d).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(commands.FAKEWORK, root / "lib" / "fakework.py")
    shutil.copyfile(Path(commands.FAKEWORK).with_name("census.py"), root / "lib" / "census.py")   # spawn's writer
    (root / "etc" / "planted.sha256").write_text("".join(sha256_hex(v.encode()) + "\n" for v in planted))
    exe = root / "bin" / "fakecli"
    exe.write_text(LAUNCHER.format(python=python or real_interpreter()))
    exe.chmod(0o755)
    return str(exe), sha256_hex(exe.read_bytes())


def starts(root) -> list:
    """What each start recorded (names and digests only)."""
    path = Path(root) / "var" / "fakecli-env.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def sign_in(home) -> None:
    """fakecli's plan sign-in under a test HOME (no secret: a mode only)."""
    (Path(home) / ".fakecli").mkdir(parents=True, exist_ok=True)
    (Path(home) / ".fakecli" / "auth.json").write_text(json.dumps({"auth_mode": "plan"}))


def sign_out(home) -> None:
    (Path(home) / ".fakecli" / "auth.json").unlink()

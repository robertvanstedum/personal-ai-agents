#!/usr/bin/env python3
"""Make a persona release that differs from a base release ONLY in the AGENTS.md header version (and CHANGES/manifest).

    make_header_release.py BASE_VERSION NEW_VERSION "change note"

Copies SOUL.md and RUNTIME.openclaw.md byte for byte, rewrites line 1 of AGENTS.md to
"# MC conversation contract — persona NEW_VERSION", verifies the rest of AGENTS.md is byte-identical, writes CHANGES.md and
a manifest (hashes, base, budget carried from the base)."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2] / "services/mc_operations/personas/mc-chat/releases"
base, new, note = sys.argv[1], sys.argv[2], sys.argv[3]
b, n = root / base, root / new
if n.exists():
    raise SystemExit(f"refused: {n} exists")
n.mkdir()
for f in ("SOUL.md", "RUNTIME.openclaw.md"):
    shutil.copyfile(b / f, n / f)
lines = (b / "AGENTS.md").read_text().split("\n")
if not lines[0].startswith("# MC conversation contract — persona "):
    raise SystemExit("refused: unexpected AGENTS header")
lines[0] = f"# MC conversation contract — persona {new}"
(n / "AGENTS.md").write_text("\n".join(lines))
assert (n / "AGENTS.md").read_text().split("\n")[1:] == (b / "AGENTS.md").read_text().split("\n")[1:]
(n / "CHANGES.md").write_text(f"# {new} — header corrected\n\n{note}\n")
bm = json.loads((b / "manifest.json").read_text())
h = lambda f: hashlib.sha256((n / f).read_bytes()).hexdigest()
m = {**bm, "version": new, "base": base, "status": "candidate", "files": {f: h(f) for f in ("SOUL.md", "AGENTS.md", "RUNTIME.openclaw.md", "CHANGES.md")}}
(n / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
print(json.dumps({"release": new, "base": base, "header": lines[0]}))

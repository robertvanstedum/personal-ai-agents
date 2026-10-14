#!/usr/bin/env python3
"""sample_drill.py: the portable-export drill (V4) on a REAL shelf, read-only on it. Exports one small tag (the smallest, or --tag), verifies the export, restores it to a
clean folder, verifies the restore, opens the first record's retained edition from the restored copy and compares its bytes with the shelf's. Prints counts and hashes only
(no record names, titles or content) and removes its temporary folders. Needs no MiniMoi service, no network and no provider login."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.vault_t1 import portable                          # noqa: E402
from core.vault_t1.reader import Vault                       # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.expanduser("~/minimoi-staging/data/memory"))
    ap.add_argument("--tag")
    ap.add_argument("--out", help="write the evidence JSON here")
    a = ap.parse_args(argv)
    vault = Vault(a.root, ())
    tags = vault.topics()["tags"]
    tag = a.tag or min(tags, key=lambda t: (tags[t], t))
    scratch = Path(tempfile.mkdtemp(prefix="vault-sample-drill-"))
    try:
        exported = scratch / "export"
        manifest = portable.export(vault, str(exported), topic=tag)
        v1 = portable.verify(str(exported))
        restored = scratch / "restored"
        r = portable.restore(str(exported), str(restored))
        v2 = portable.verify(str(restored))
        rv = Vault(str(restored), ())
        listing = rv.list()["records"]
        same = None
        if listing:
            rec = listing[0]
            _, _, got = rv.open(rec["id"], rec["edition"])
            _, _, want = vault.open(rec["id"], rec["edition"])
            same = hashlib.sha256(got).digest() == hashlib.sha256(want).digest()
        evidence = {
            "ok": bool(v1["ok"] and v2["ok"] and manifest["complete"] and same is not False and len(listing) == manifest["counts"].get("records", len(listing))),
            "tag_size": tags[tag], "counts": manifest["counts"], "complete": manifest["complete"], "left_out": manifest["left_out"],
            "export_verify_ok": v1["ok"], "restore_verify_ok": v2["ok"], "restored_records": len(listing),
            "first_edition_identical_to_shelf": same,
            "export_manifest_sha256": hashlib.sha256((exported / "manifest.json").read_bytes()).hexdigest(),
            "needed": ["the exported folder", "python (or any text tools)"], "not_needed": ["MiniMoi", "a database", "the network", "a provider login"],
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    text = json.dumps(evidence, indent=2, sort_keys=True)
    if a.out:
        Path(a.out).write_text(text + "\n")
    print(text)
    return 0 if evidence["ok"] else 5


if __name__ == "__main__":
    sys.exit(main())

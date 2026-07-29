#!/usr/bin/env python3
"""Generate one Curator Scan from a persisted web request payload."""

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from domains.curator.curator_feedback import generate_scan, regenerate_scans_index


def _update_history(hash_id: str, article: dict, output_path: str) -> None:
    """Best-effort compatibility update for the legacy history index."""
    history_path = REPO_ROOT / "curator_history.json"
    if not history_path.exists():
        return

    try:
        history = json.loads(history_path.read_text())
        item = history.setdefault(hash_id, {})
        for key in ("title", "url", "source", "category"):
            if article.get(key):
                item.setdefault(key, article[key])
        item["bookmarked"] = True
        item["deep_dive_path"] = output_path

        tmp_path = history_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(history, indent=2))
        tmp_path.replace(history_path)
    except Exception as exc:
        print(f"History compatibility update skipped: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()

    payload_path = Path(args.payload)
    payload = json.loads(payload_path.read_text())
    hash_id = payload["hash_id"]
    article = payload["article"]

    _, _, output_path = generate_scan(
        hash_id,
        article,
        payload["interest"],
        payload.get("focus") or None,
    )
    if not output_path:
        print("Scan generation returned no output", file=sys.stderr)
        return 1

    _update_history(hash_id, article, output_path)
    regenerate_scans_index()
    print(json.dumps({
        "ok": True,
        "hash_id": hash_id,
        "view_url": f"/research/scan/{hash_id}",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

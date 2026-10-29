"""Shared German Anki import operations; entrypoints own state paths and policy.

No imports of the language application and no work occurs at import time.
Callers inject orchestration operations so standalone scripts retain their hooks.
"""
import csv
import json
import urllib.error
import urllib.request
from pathlib import Path


def clean_back(raw: str) -> str:
    return raw.split(" \u2014 ")[0].strip()


def load_tracker(tracker: Path) -> set[str]:
    if not tracker.exists():
        return set()
    return set(tracker.read_text(encoding="utf-8").splitlines())


def mark_imported(tracker: Path, filename: str) -> None:
    with open(tracker, "a", encoding="utf-8") as f:
        f.write(filename + "\n")


def parse_csv(path: Path, *, deck_name: str, model_name: str, clean_back=clean_back) -> list[dict]:
    notes = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for i, row in enumerate(reader, start=2):
            if len(row) != 3:
                print(f"  ⚠️  {path.name} row {i}: expected 3 columns, got {len(row)} — skipped")
                continue
            front, back, tags_raw = row
            notes.append({
                "deckName": deck_name,
                "modelName": model_name,
                "fields": {"Front": front, "Back": clean_back(back)},
                "tags": tags_raw.split(),
            })
    return notes


def ankiconnect(url: str, action: str, **params) -> dict:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        print(f"❌ AnkiConnect unreachable: {e}")
        raise SystemExit(1)


def add_notes(notes: list[dict], *, ankiconnect) -> tuple[int, int]:
    resp = ankiconnect("addNotes", notes=notes)
    ids = resp.get("result") or []
    added = sum(1 for x in ids if x is not None)
    dupes = sum(1 for x in ids if x is None)
    return added, dupes


def run(anki_dir: Path, *, load_tracker, parse_csv, add_notes, mark_imported) -> None:
    imported = load_tracker()
    pending = sorted(f for f in anki_dir.glob("*.csv") if f.name not in imported)

    if not pending:
        print("Nothing new to import.")
        return

    total_files = total_added = total_dupes = 0

    for csv_path in pending:
        notes = parse_csv(csv_path)
        if not notes:
            print(f"  ⚠️  {csv_path.name}: no valid rows — skipping")
            continue
        added, dupes = add_notes(notes)
        if added == 0 and dupes == 0:
            print(f"  ❌ {csv_path.name}: AnkiConnect returned no results — deck may not exist. Not marked as imported.")
            continue
        mark_imported(csv_path.name)
        total_files += 1
        total_added += added
        total_dupes += dupes
        dupe_note = f", {dupes} duplicate(s)" if dupes else ""
        print(f"  ✅ {csv_path.name}: {added} card(s) added{dupe_note}")

    print(f"\nImported {total_files} file(s), {total_added} card(s) added.")

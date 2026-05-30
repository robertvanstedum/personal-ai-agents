#!/usr/bin/env python3
"""
import_cards.py — Push unimported German Anki CSVs to Anki via AnkiConnect.

Usage:
  python3 import_cards.py

Idempotent: tracks imported files in imported_files.txt; re-runs are safe.
Requires Anki to be open with AnkiConnect add-on running on port 8765.
"""
import csv
import json
import urllib.error
import urllib.request
from pathlib import Path

ANKI_URL   = "http://localhost:8765"
DECK_NAME  = "German"
MODEL_NAME = "Basic"
ANKI_DIR   = Path(__file__).parent.parent.parent / "anki"
TRACKER    = Path(__file__).parent / "imported_files.txt"


def clean_back(raw: str) -> str:
    return raw.split(" \u2014 ")[0].strip()


def load_tracker() -> set[str]:
    if not TRACKER.exists():
        return set()
    return set(TRACKER.read_text(encoding="utf-8").splitlines())


def mark_imported(filename: str) -> None:
    with open(TRACKER, "a", encoding="utf-8") as f:
        f.write(filename + "\n")


def parse_csv(path: Path) -> list[dict]:
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
                "deckName": DECK_NAME,
                "modelName": MODEL_NAME,
                "fields": {"Front": front, "Back": clean_back(back)},
                "tags": tags_raw.split(),
            })
    return notes


def ankiconnect(action: str, **params) -> dict:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = urllib.request.Request(
        ANKI_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        print(f"❌ AnkiConnect unreachable: {e}")
        raise SystemExit(1)


def add_notes(notes: list[dict]) -> tuple[int, int]:
    resp = ankiconnect("addNotes", notes=notes)
    ids = resp.get("result") or []
    added = sum(1 for x in ids if x is not None)
    dupes = sum(1 for x in ids if x is None)
    return added, dupes


def main() -> None:
    imported = load_tracker()
    pending = sorted(f for f in ANKI_DIR.glob("*.csv") if f.name not in imported)

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


if __name__ == "__main__":
    main()

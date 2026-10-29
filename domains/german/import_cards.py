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
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from german_domain import GERMAN_STATE_DIR
from domains.german import anki_import as _import

ANKI_URL   = "http://localhost:8765"
DECK_NAME  = "German"
MODEL_NAME = "Basic"
ANKI_DIR   = GERMAN_STATE_DIR / "anki"
TRACKER    = GERMAN_STATE_DIR / "imported_files.txt"


def clean_back(raw: str) -> str:
    return _import.clean_back(raw)


def load_tracker() -> set[str]:
    return _import.load_tracker(TRACKER)


def mark_imported(filename: str) -> None:
    _import.mark_imported(TRACKER, filename)


def parse_csv(path: Path) -> list[dict]:
    return _import.parse_csv(path, deck_name=DECK_NAME, model_name=MODEL_NAME, clean_back=clean_back)


def ankiconnect(action: str, **params) -> dict:
    return _import.ankiconnect(ANKI_URL, action, **params)


def add_notes(notes: list[dict]) -> tuple[int, int]:
    return _import.add_notes(notes, ankiconnect=ankiconnect)


def main() -> None:
    _import.run(ANKI_DIR, load_tracker=load_tracker, parse_csv=parse_csv,
                add_notes=add_notes, mark_imported=mark_imported)


if __name__ == "__main__":
    main()

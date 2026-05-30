#!/usr/bin/env python3
"""Build vienna_deck.csv from needs-practice cards + drill_pool.json."""

import csv
import json
from pathlib import Path

ANKI_DIR = Path(__file__).parent
DRILL_POOL = ANKI_DIR.parent / "config" / "drill_pool.json"
OUTPUT = ANKI_DIR / "vienna_deck.csv"

PERSONS_DISPLAY = ["ich", "du", "er/sie/es", "wir", "ihr", "sie/Sie"]
PERSONS_POOL    = ["ich", "du", "er",         "wir", "ihr", "sie"]


def clean_back(back: str) -> str:
    """Strip session notes — keep only the translation before the first ' — '."""
    if " — " in back:
        return back.split(" — ")[0].strip()
    return back.strip()


def load_needs_practice() -> list[dict]:
    seen: set[str] = set()
    cards: list[dict] = []
    for csv_file in sorted(ANKI_DIR.glob("*.csv")):
        if csv_file.name in ("vienna_deck.csv", csv_file.name.startswith("build_")):
            continue
        with open(csv_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if "needs practice" not in row.get("Back", ""):
                    continue
                front = row["Front"].strip()
                if front in seen:
                    continue
                seen.add(front)
                cards.append({
                    "Front": front,
                    "Back": clean_back(row["Back"]),
                    "Tags": "needs-practice Vienna",
                })
    return cards


def verb_cards(pool: dict) -> list[dict]:
    cards = []
    for verb in pool.get("core", {}).get("verbs", []):
        conj = verb.get("conjugations", {})
        if not conj:
            continue
        table = "\n".join(
            f"{dp} → {conj.get(pp, '?')}"
            for dp, pp in zip(PERSONS_DISPLAY, PERSONS_POOL)
        )
        cards.append({
            "Front": f"{verb['verb']} — {verb.get('english', '')}",
            "Back": table,
            "Tags": f"verb conjugation Vienna",
        })
    return cards


def phrase_cards(pool: dict) -> list[dict]:
    cards = []
    for verb in pool.get("core", {}).get("verbs", []):
        for phrase in verb.get("phrases", []):
            cards.append({
                "Front": phrase["english"],
                "Back": phrase["german"],
                "Tags": f"phrase {verb['verb']} Vienna",
            })
    return cards


def noun_cards(pool: dict) -> list[dict]:
    cards = []
    for noun in pool.get("core", {}).get("nouns", []):
        cards.append({
            "Front": f"{noun['article']} {noun['de']}",
            "Back": noun["en"],
            "Tags": "noun Vienna",
        })
    return cards


def main():
    pool = json.loads(DRILL_POOL.read_text(encoding="utf-8"))

    np_cards   = load_needs_practice()
    v_cards    = verb_cards(pool)
    ph_cards   = phrase_cards(pool)
    n_cards    = noun_cards(pool)

    all_cards = np_cards + v_cards + ph_cards + n_cards

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Front", "Back", "Tags"], quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(all_cards)

    print(f"✅ Vienna deck: {len(all_cards)} cards → {OUTPUT.name}")
    print(f"   {len(np_cards)} needs-practice  |  {len(v_cards)} verb tables  |  {len(ph_cards)} phrases  |  {len(n_cards)} nouns")


if __name__ == "__main__":
    main()

"""MC's cards (#553): the SKILL.md text of one of the skills MC's pinned persona release ships, read-only.

OpenClaw lists MC's skills as index cards (name and an "Open when..." line) and MC opens one when a message matches. On
the Claude plan route MC's own Read and Skill tools are off by policy, so a card could not open there. One mechanism for
every route: minimoi_open_card returns a card's text through MiniMoi's tools door.

The set is exactly what the container lays out: the persona release named by pin.json (the same release MC's image
copies; the persona tests check they agree), its skills/<name>/SKILL.md files, and the handbook's own
minimoi-engineering skill that release pins (handbook.load "skill"). A card is returned only when its bytes match the
release or handbook manifest. Names only: no path is ever taken from the caller.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONA = ROOT / "services/mc_operations/personas/mc-chat"
HANDBOOK = ROOT / "services/mc_operations/handbook"
HANDBOOK_CARD = "minimoi-engineering"
MAX_CARD = 32768
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_OWN = re.compile(r"skills/([a-z0-9][a-z0-9-]{0,62})/SKILL\.md")


class Unavailable(Exception):
    """The pinned release or handbook can't be read or doesn't verify: no card is served."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def catalogue(persona: Path = PERSONA, handbook: Path = HANDBOOK) -> dict:
    """{card name: (file, sha256)} for the pinned release. Raises Unavailable."""
    try:
        release = json.loads((persona / "pin.json").read_text())["release"]
        if not isinstance(release, str) or not _VERSION.fullmatch(release):
            raise Unavailable("pin")
        folder = persona / "releases" / release
        manifest = json.loads((folder / "manifest.json").read_text())
        pinned = manifest.get("handbook") or {}
        if pinned.get("load") != "skill":
            return {}                                       # a release without cards
        cards = {}
        for name, digest in (manifest.get("files") or {}).items():
            m = _OWN.fullmatch(name)
            if m:
                cards[m.group(1)] = (folder / name, digest)
        raw = (handbook / "manifest.json").read_bytes()
        if _sha(raw) != pinned.get("manifest_sha256"):
            raise Unavailable("handbook_pin")
        hb = json.loads(raw)
        skill = f"{hb['release_path']}/skills/{HANDBOOK_CARD}/SKILL.md"
        if HANDBOOK_CARD in cards or skill not in hb["files"]:
            raise Unavailable("handbook_card")
        cards[HANDBOOK_CARD] = (handbook / skill, hb["files"][skill])
        return cards
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Unavailable("unreadable") from exc


def open_card(name, persona: Path = PERSONA, handbook: Path = HANDBOOK) -> tuple[str, str]:
    """("ok", text) or (refusal code, ""). Codes: card_unknown, cards_unavailable, card_unverified."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return "card_unknown", ""
    try:
        cards = catalogue(persona, handbook)
    except Unavailable:
        return "cards_unavailable", ""
    if name not in cards:
        return "card_unknown", ""
    path, digest = cards[name]
    try:
        raw = path.read_bytes()
    except OSError:
        return "cards_unavailable", ""
    if len(raw) > MAX_CARD or _sha(raw) != digest:
        return "card_unverified", ""
    return "ok", raw.decode("utf-8")
